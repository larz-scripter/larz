"""
Larz test suite — runs on plain `python3 tests/test_core.py`, no pytest needed.
Exercises routing, typed params, sessions, and the money-native paywall/metering
through a lightweight in-process WSGI client (no sockets).
"""
import sys, os, io, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.money import parse_price, EntitlementStore
import larz.money as money


class Client:
    """Minimal in-process WSGI client that remembers the session cookie."""
    def __init__(self, app):
        self.app = app
        self.cookie = None

    def request(self, method, path, body=b"", follow=True, _depth=0):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        query = ""
        if "?" in path:
            path, query = path.split("?", 1)
        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
            "REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "test",
        }
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        status_headers = {}

        def start_response(status, headers):
            status_headers["status"] = int(status.split()[0])
            status_headers["headers"] = headers

        chunks = self.app(environ, start_response)
        raw = b"".join(chunks)
        for k, v in status_headers["headers"]:
            if k == "Set-Cookie":
                self.cookie = v.split(";")[0]
        code = status_headers["status"]
        location = dict(status_headers["headers"]).get("Location")
        if follow and code in (301, 302, 303) and location and _depth < 6:
            # strip host for in-process follow
            p = location.split("://", 1)[-1]
            p = p[p.find("/"):] if "/" in p else "/"
            return self.request("GET", p, follow=True, _depth=_depth + 1)
        return code, raw


PASS = [0]; FAIL = [0]
def check(name, cond):
    if cond:
        PASS[0] += 1; print("  ok   " + name)
    else:
        FAIL[0] += 1; print("  FAIL " + name)


def test_price_parsing():
    check("price $9", parse_price("$9") == (900, None))
    check("price $9/mo", parse_price("$9/mo") == (900, "mo"))
    check("price $0.02/call", parse_price("$0.02/call") == (2, "call"))
    check("price float 4.5", parse_price(4.5) == (450, None))


def test_routing_and_params():
    app = Larz(secret="t")
    @app.get("/u/<id:int>/p/<slug>")
    def h(req):
        return Response.json({"id": req.params["id"], "slug": req.params["slug"],
                              "id_type": type(req.params["id"]).__name__})
    c = Client(app)
    code, body = c.request("GET", "/u/42/p/hello-world")
    data = json.loads(body)
    check("route matched 200", code == 200)
    check("int converter -> int", data["id"] == 42 and data["id_type"] == "int")
    check("str param", data["slug"] == "hello-world")
    code, _ = c.request("GET", "/u/notanumber/p/x")
    check("int converter rejects non-int (404)", code == 404)


def test_session_sticky():
    app = Larz(secret="t")
    @app.get("/whoami")
    def who(req):
        return req.subject
    c = Client(app)
    _, a = c.request("GET", "/whoami")
    _, b = c.request("GET", "/whoami")
    check("subject stable across requests", a == b and a != b"")


def test_paywall(tmpdb):
    app = Larz(secret="t")
    money.enable(app, db=tmpdb, base_url="http://x")
    @app.paid("$9")
    @app.get("/pro")
    def pro(req):
        return "PAID:" + req.subject
    c = Client(app)
    # first hit: not entitled -> redirected through mock checkout -> served
    code, body = c.request("GET", "/pro", follow=True)
    check("paywall grants after checkout (200)", code == 200 and body.startswith(b"PAID:"))
    # second hit: entitled directly, no redirect
    code2, _ = c.request("GET", "/pro", follow=False)
    check("entitlement sticks (direct 200)", code2 == 200)


def test_metering(tmpdb):
    app = Larz(secret="t")
    money.enable(app, db=tmpdb, base_url="http://x")
    @app.metered("$0.02/call")
    @app.post("/api")
    def api(req):
        return Response.json({"ok": True})
    c = Client(app)
    code, body = c.request("POST", "/api", follow=False)
    check("metered 402 when broke", code == 402)
    # discover this client's subject, then credit it
    code, body = c.request("GET", "/larz/credits")
    subject = json.loads(body)["subject"]
    app.money.store.add_credit(subject, 5)   # 5 cents = 2 calls + change
    code, _ = c.request("POST", "/api", follow=False)
    check("metered 200 when funded", code == 200)
    check("balance debited to 3c", app.money.store.balance(subject) == 3)
    code, _ = c.request("POST", "/api", follow=False)
    code, _ = c.request("POST", "/api", follow=False)
    check("metered 402 again once drained", code == 402)


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    test_price_parsing()
    test_routing_and_params()
    test_session_sticky()
    test_paywall(os.path.join(d, "a.db"))
    test_metering(os.path.join(d, "b.db"))
    print("\n  %d passed, %d failed" % (PASS[0], FAIL[0]))
    sys.exit(1 if FAIL[0] else 0)
