"""
larz.pg — a pure-Python PostgreSQL driver (zero dependencies).

Implements just enough of the PostgreSQL v3 wire protocol to run parameterized
queries against a real server, including modern **SCRAM-SHA-256** authentication
(RFC 5802/7677) as well as md5 and cleartext. No psycopg, no libpq — just the
standard library (socket, hashlib, hmac, struct, base64).

    from larz.pg import connect
    cx = connect("postgres://user:pass@localhost:5432/mydb")
    rows = cx.run("SELECT * FROM users WHERE id = $1", [1])   # -> list of dict

The ORM (larz.models) uses this automatically when you call
`connect("postgres://...")`. sqlite remains the zero-config default.
"""

import os
import ssl as _ssl
import hmac
import base64
import struct
import socket
import hashlib
from urllib.parse import urlsplit, unquote

__all__ = ["connect", "Connection", "PgError"]


class PgError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Wire helpers
# --------------------------------------------------------------------------- #
def _i32(n):
    return struct.pack(">i", n)


def _msg(tag, payload):
    """A tagged frontend message: tag byte + Int32 length (incl. itself) + body."""
    return tag + _i32(len(payload) + 4) + payload


def _cstr(s):
    return s.encode("utf-8") + b"\x00"


class _Reader:
    """Reads length-prefixed backend messages off a socket."""
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise PgError("connection closed by server")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def message(self):
        tag = self._recv(1)
        length = struct.unpack(">i", self._recv(4))[0]
        body = self._recv(length - 4) if length > 4 else b""
        return tag, body


# --------------------------------------------------------------------------- #
#  SCRAM-SHA-256
# --------------------------------------------------------------------------- #
def _scram_client_first(nonce):
    bare = "n=,r=%s" % nonce           # username is sent empty (server uses startup user)
    return "n,,", bare


def _scram_proof(password, nonce, server_first, client_first_bare, gs2):
    parts = dict(p.split("=", 1) for p in server_first.split(","))
    r, s, i = parts["r"], parts["s"], int(parts["i"])
    if not r.startswith(nonce):
        raise PgError("SCRAM: server nonce mismatch")
    salt = base64.b64decode(s)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, i, 32)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    channel = base64.b64encode(gs2.encode()).decode()
    client_final_bare = "c=%s,r=%s" % (channel, r)
    auth_msg = "%s,%s,%s" % (client_first_bare, server_first, client_final_bare)
    client_sig = hmac.new(stored_key, auth_msg.encode(), hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, client_sig))
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    server_sig = hmac.new(server_key, auth_msg.encode(), hashlib.sha256).digest()
    client_final = "%s,p=%s" % (client_final_bare, base64.b64encode(proof).decode())
    return client_final, base64.b64encode(server_sig).decode()


# --------------------------------------------------------------------------- #
#  Connection
# --------------------------------------------------------------------------- #
class Connection:
    def __init__(self, host="localhost", port=5432, user="postgres",
                 password="", database=None, sslmode="prefer", timeout=15):
        self.params = dict(host=host, port=port, user=user, password=password,
                           database=database or user)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        if sslmode in ("require", "prefer"):
            self._maybe_ssl(sslmode)
        self.r = _Reader(self.sock)
        self._startup()

    def _maybe_ssl(self, sslmode):
        self.sock.sendall(_i32(8) + _i32(80877103))     # SSLRequest
        resp = self.sock.recv(1)
        if resp == b"S":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            self.sock = ctx.wrap_socket(self.sock)
        elif sslmode == "require":
            raise PgError("server does not support SSL")

    def _send(self, data):
        self.sock.sendall(data)

    def _startup(self):
        p = self.params
        body = _i32(196608)                       # protocol 3.0
        body += _cstr("user") + _cstr(p["user"])
        body += _cstr("database") + _cstr(p["database"])
        body += b"\x00"
        self._send(_i32(len(body) + 4) + body)
        self._authenticate()
        # drain until ReadyForQuery
        while True:
            tag, b = self.r.message()
            if tag == b"Z":
                break
            if tag == b"E":
                raise PgError(_parse_error(b))

    def _authenticate(self):
        while True:
            tag, b = self.r.message()
            if tag == b"E":
                raise PgError(_parse_error(b))
            if tag != b"R":
                # ParameterStatus/BackendKeyData before auth done shouldn't happen,
                # but ignore anything non-auth defensively
                continue
            code = struct.unpack(">i", b[:4])[0]
            if code == 0:
                return                            # AuthenticationOk
            if code == 3:                         # cleartext
                self._send(_msg(b"p", _cstr(self.params["password"])))
            elif code == 5:                       # md5
                salt = b[4:8]
                pw, user = self.params["password"], self.params["user"]
                inner = hashlib.md5((pw + user).encode()).hexdigest()
                token = "md5" + hashlib.md5(inner.encode() + salt).hexdigest()
                self._send(_msg(b"p", _cstr(token)))
            elif code == 10:                      # SASL (SCRAM-SHA-256)
                self._scram()
                return
            else:
                raise PgError("unsupported auth method %d" % code)

    def _scram(self):
        nonce = base64.b64encode(os.urandom(18)).decode()
        gs2, bare = _scram_client_first(nonce)
        client_first = gs2 + bare
        payload = _cstr("SCRAM-SHA-256") + _i32(len(client_first)) + client_first.encode()
        self._send(_msg(b"p", payload))
        # AuthenticationSASLContinue
        tag, b = self.r.message()
        if tag == b"E":
            raise PgError(_parse_error(b))
        server_first = b[4:].decode()
        client_final, expected_sig = _scram_proof(
            self.params["password"], nonce, server_first, bare, gs2)
        self._send(_msg(b"p", client_final.encode()))
        # AuthenticationSASLFinal
        tag, b = self.r.message()
        if tag == b"E":
            raise PgError(_parse_error(b))
        server_final = b[4:].decode()
        got = dict(p.split("=", 1) for p in server_final.split(","))
        if got.get("v") != expected_sig:
            raise PgError("SCRAM: server signature mismatch")
        # then AuthenticationOk arrives in the outer loop
        tag, b = self.r.message()
        if tag == b"R" and struct.unpack(">i", b[:4])[0] == 0:
            return
        if tag == b"E":
            raise PgError(_parse_error(b))

    # -- queries (extended protocol, parameterized) ------------------------ #
    def run(self, sql, params=()):
        """Run a parameterized query ($1, $2, …). Returns a list of dict rows
        (empty for non-SELECT). last_insert_id / rowcount on the connection."""
        params = list(params or [])
        # Parse (unnamed) + Bind + Describe + Execute + Sync
        parse = _cstr("") + _cstr(sql) + struct.pack(">h", 0)
        pvals = struct.pack(">h", 0)                      # 0 => all params are text
        pvals += struct.pack(">h", len(params))
        for v in params:
            if v is None:
                pvals += _i32(-1)
            else:
                sv = _encode_param(v)
                pvals += _i32(len(sv)) + sv
        pvals += struct.pack(">h", 0)                     # result formats: text
        bind = _cstr("") + _cstr("") + pvals
        out = (_msg(b"P", parse) + _msg(b"B", bind)
               + _msg(b"D", b"P" + _cstr("")) + _msg(b"E", _cstr("") + _i32(0))
               + _msg(b"S", b""))
        self._send(out)
        cols, rows = [], []
        self.rowcount = 0
        self.last_insert_id = None
        while True:
            tag, b = self.r.message()
            if tag == b"T":                               # RowDescription
                cols = _parse_row_desc(b)
            elif tag == b"D":                             # DataRow
                rows.append(_parse_data_row(b, cols))
            elif tag == b"C":                             # CommandComplete
                self.rowcount = _parse_complete(b)
            elif tag == b"E":                             # ErrorResponse
                # consume until ReadyForQuery, then raise
                self._drain_to_ready()
                raise PgError(_parse_error(b))
            elif tag == b"Z":                             # ReadyForQuery
                break
            # ignore ParseComplete(1), BindComplete(2), NoData(n), etc.
        if rows and cols and cols[0] == "id" and len(cols) == 1:
            self.last_insert_id = rows[0]["id"]
        return rows

    def _drain_to_ready(self):
        while True:
            tag, _ = self.r.message()
            if tag == b"Z":
                return

    def commit(self):
        pass                              # autocommit (each run is its own tx)

    def close(self):
        try:
            self._send(_msg(b"X", b""))   # Terminate
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Message parsing
# --------------------------------------------------------------------------- #
def _parse_error(b):
    fields = {}
    for part in b.split(b"\x00"):
        if part:
            fields[chr(part[0])] = part[1:].decode("utf-8", "replace")
    return "%s: %s" % (fields.get("S", "ERROR"), fields.get("M", "unknown"))


def _parse_row_desc(b):
    n = struct.unpack(">h", b[:2])[0]
    cols, off = [], 2
    for _ in range(n):
        end = b.index(b"\x00", off)
        cols.append(b[off:end].decode("utf-8"))
        off = end + 1 + 18                # skip 18 bytes of column metadata
    return cols


def _parse_data_row(b, cols):
    n = struct.unpack(">h", b[:2])[0]
    row, off = {}, 2
    for i in range(n):
        ln = struct.unpack(">i", b[off:off + 4])[0]; off += 4
        if ln == -1:
            val = None
        else:
            val = b[off:off + ln].decode("utf-8"); off += ln
        row[cols[i] if i < len(cols) else i] = _coerce_out(val)
    return row


def _parse_complete(b):
    tag = b.split(b"\x00")[0].decode()
    parts = tag.split(" ")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def _coerce_out(val):
    if val is None:
        return None
    # best-effort numeric coercion (text protocol returns strings)
    if val.lstrip("-").isdigit():
        try: return int(val)
        except ValueError: pass
    try:
        if "." in val or "e" in val.lower():
            f = float(val)
            return f
    except ValueError:
        pass
    if val in ("t", "f"):
        return val == "t"
    return val


def _encode_param(v):
    if isinstance(v, bool):
        return b"t" if v else b"f"
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return str(v).encode("utf-8")


def connect(url):
    """connect('postgres://user:pass@host:port/db?sslmode=prefer')."""
    u = urlsplit(url)
    return Connection(
        host=u.hostname or "localhost", port=u.port or 5432,
        user=unquote(u.username or "postgres"),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/") or None,
        sslmode="require" if "sslmode=require" in (u.query or "") else "prefer")


# --------------------------------------------------------------------------- #
#  ORM adapter — makes a Connection look like a sqlite3 connection to larz.models
#  by translating the sqlite dialect the ORM emits into PostgreSQL.
# --------------------------------------------------------------------------- #
def is_pg_url(path):
    return isinstance(path, str) and path.startswith(("postgres://", "postgresql://"))


def _translate(sql):
    s = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    if s.lstrip().upper().startswith("CREATE TABLE"):
        s = s.replace(" REAL", " DOUBLE PRECISION")   # avoid float4 precision loss
    out, i = [], 0
    for ch in s:                                        # ?  ->  $1, $2, ...
        if ch == "?":
            i += 1
            out.append("$%d" % i)
        else:
            out.append(ch)
    return "".join(out)


class PgResult:
    def __init__(self, rows, lastrowid, rowcount):
        self._rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class PgAdapter:
    """Presents a sqlite3-like .execute()/.commit()/.close() API over a pg
    Connection, translating the ORM's SQL to PostgreSQL on the way."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        st = sql.strip()
        up = st.upper()
        if up.startswith("PRAGMA FOREIGN_KEYS"):
            return PgResult([], None, 0)               # no-op on pg
        if up.startswith("PRAGMA TABLE_INFO"):
            table = st[st.index("(") + 1:st.rindex(")")].strip().strip('"')
            rows = self.conn.run(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = $1", [table])
            return PgResult(rows, None, len(rows))
        s = _translate(sql)
        if up.startswith("INSERT") and "RETURNING" not in up:
            s = s.rstrip().rstrip(";") + " RETURNING id"
        rows = self.conn.run(s, list(params or []))
        return PgResult(rows, self.conn.last_insert_id, self.conn.rowcount)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.conn.close()


def orm_adapter(url):
    return PgAdapter(connect(url))
