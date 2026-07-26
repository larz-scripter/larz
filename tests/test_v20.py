"""v2.0 async-core tests — ASGI http (async & sync handlers), streaming over ASGI,
WebSockets, lifespan, WS framing, and a real end-to-end run against the built-in
zero-dependency async server. Plain python3, no pytest. asyncio (3.6-safe)."""
import os, sys, asyncio, threading, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response, WebSocket
from larz.aserver import _build_frame, _read_frame
import struct

P = [0]; F = [0]
def ck(name, cond):
    if cond: P[0] += 1; print("  ok   " + name)
    else: F[0] += 1; print("  FAIL " + name)

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def build_app():
    app = Larz(secret="x")
    @app.get("/async")
    async def ah(req):
        return {"async": True, "path": req.path}
    @app.get("/sync")
    def sh(req):
        return {"sync": True}
    @app.post("/echo")
    async def echo(req):
        return {"got": (req.json() or {}).get("v")}
    @app.get("/stream")
    def stream(req):
        return Response.stream((str(i) for i in range(4)), content_type="text/plain")
    @app.websocket("/ws/<room>")
    async def ws(sock):
        await sock.accept()
        async for msg in sock:
            await sock.send("echo:%s:%s" % (msg, sock.params["room"]))
    return app


async def call_http(app, method, path, body=b"", qs=b""):
    scope = {"type": "http", "method": method, "path": path, "query_string": qs,
             "headers": [(b"content-type", b"application/json")], "client": ("127.0.0.1", 0),
             "scheme": "http"}
    inbox = [{"type": "http.request", "body": body, "more_body": False}]
    async def receive():
        return inbox.pop(0) if inbox else {"type": "http.disconnect"}
    out = []
    async def send(m): out.append(m)
    await app(scope, receive, send)
    start = next(m for m in out if m["type"] == "http.response.start")
    b = b"".join(m.get("body", b"") for m in out if m["type"] == "http.response.body")
    return start["status"], b


def test_asgi_http():
    app = build_app()
    import json
    s, b = run(call_http(app, "GET", "/async"))
    ck("async handler awaited", s == 200 and json.loads(b) == {"async": True, "path": "/async"})
    s, b = run(call_http(app, "GET", "/sync"))
    ck("sync handler under ASGI", json.loads(b) == {"sync": True})
    s, b = run(call_http(app, "POST", "/echo", body=b'{"v":"hi"}'))
    ck("async POST reads body", json.loads(b) == {"got": "hi"})
    s, b = run(call_http(app, "GET", "/stream"))
    ck("streaming over ASGI", b == b"0123")
    s, b = run(call_http(app, "GET", "/nope"))
    ck("404 over ASGI", s == 404)


def test_asgi_websocket():
    app = build_app()
    async def go():
        scope = {"type": "websocket", "path": "/ws/room1", "query_string": b"",
                 "headers": [], "client": ("127.0.0.1", 0)}
        inbox = [{"type": "websocket.connect"},
                 {"type": "websocket.receive", "text": "hello"},
                 {"type": "websocket.receive", "text": "again"},
                 {"type": "websocket.disconnect", "code": 1000}]
        async def receive():
            return inbox.pop(0) if inbox else {"type": "websocket.disconnect", "code": 1000}
        out = []
        async def send(m): out.append(m)
        await app(scope, receive, send)
        return out
    out = run(go())
    ck("ws accepted", any(m["type"] == "websocket.accept" for m in out))
    sends = [m.get("text") for m in out if m["type"] == "websocket.send"]
    ck("ws echoes with path param", sends == ["echo:hello:room1", "echo:again:room1"])


def test_lifespan():
    app = Larz(secret="x")
    events = []
    @app.on_startup
    def s(): events.append("up")
    @app.on_shutdown
    def d(): events.append("down")
    async def go():
        inbox = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        async def receive(): return inbox.pop(0)
        out = []
        async def send(m): out.append(m["type"])
        await app({"type": "lifespan"}, receive, send)
        return out
    out = run(go())
    ck("lifespan startup+shutdown", out == ["lifespan.startup.complete", "lifespan.shutdown.complete"])
    ck("startup/shutdown hooks fired", events == ["up", "down"])


def test_ws_framing():
    # server->client frame builds with FIN+opcode and correct length
    f = _build_frame(0x1, b"hi")
    ck("frame FIN+text opcode", f[0] == 0x81 and f[1] == 2 and f[2:] == b"hi")
    big = _build_frame(0x1, b"x" * 200)
    ck("extended length 126", big[1] == 126 and struct.unpack(">H", big[2:4])[0] == 200)
    # round-trip a masked client frame through _read_frame
    payload = b"ping"
    mask = b"\x01\x02\x03\x04"
    masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    raw = bytes([0x81, 0x80 | len(payload)]) + mask + masked

    class FakeReader:
        def __init__(self, data): self.data = data
        async def readexactly(self, n):
            b, self.data = self.data[:n], self.data[n:]
            return b
    op, data = run(_read_frame(FakeReader(raw)))
    ck("read masked client frame", op == 0x1 and data == b"ping")


def test_builtin_server_e2e():
    app = build_app()
    port = 8791
    ready = {"ok": False}
    def run_server():
        try:
            app.run_async(host="127.0.0.1", port=port)
        except Exception:
            pass
    th = threading.Thread(target=run_server, daemon=True)
    th.start()
    time.sleep(0.7)                       # let it bind

    async def client():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /async HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        data = b""
        while True:                       # Connection: close -> read to EOF
            chunk = await reader.read(4096)
            if not chunk:
                break
            data += chunk
        writer.close()
        return data
    try:
        data = run(client())
        ck("built-in server serves HTTP", b"200 OK" in data and b'"async": true' in data)
    except Exception as e:
        ck("built-in server serves HTTP", False)
        print("     (e2e error: %s)" % e)


def test_pg_unit():
    """Driver logic without a live server: SQL translation + SCRAM math (RFC 7677)."""
    from larz.pg import _translate, PgResult, _scram_proof, is_pg_url, _encode_param
    ck("pg url detected", is_pg_url("postgres://u:p@h/db") and not is_pg_url("app.db"))
    ck("translate placeholders", _translate("SELECT * FROM t WHERE a=? AND b=?")
       == "SELECT * FROM t WHERE a=$1 AND b=$2")
    ck("translate SERIAL PK", "SERIAL PRIMARY KEY" in
       _translate("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)"))
    ck("translate REAL->double in DDL", "DOUBLE PRECISION" in
       _translate("CREATE TABLE t (p REAL)"))
    ck("encode bool/int/str", (_encode_param(True), _encode_param(5), _encode_param("hi"))
       == (b"t", b"5", b"hi"))
    r = PgResult([{"id": 1}, {"id": 2}], 2, 2)
    ck("PgResult fetchone/all", r.fetchone() == {"id": 1} and r.fetchall()[1] == {"id": 2})
    # RFC 7677 SCRAM-SHA-256 test vector (username=user, password=pencil)
    nonce = "rOprNGfwEbeRWgbNEkqO"
    bare = "n=user,r=" + nonce
    server_first = ("r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
                    "s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
    client_final, server_sig = _scram_proof("pencil", nonce, server_first, bare, "n,,")
    ck("SCRAM client proof matches RFC 7677",
       client_final.endswith("p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="))
    ck("SCRAM server signature matches RFC 7677",
       server_sig == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")


def main():
    for t in [test_asgi_http, test_asgi_websocket, test_lifespan, test_ws_framing,
              test_builtin_server_e2e, test_pg_unit]:
        print("\n# " + t.__name__)
        t()
    print("\n%d passed, %d failed" % (P[0], F[0]))
    return 1 if F[0] else 0


if __name__ == "__main__":
    sys.exit(main())
