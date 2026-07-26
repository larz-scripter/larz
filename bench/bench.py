"""
Larz benchmark — measures framework dispatch overhead in-process (no sockets),
so it isolates Larz's cost from the network/server. Real-world throughput is
bounded by your server (gunicorn workers) and IO; these numbers are the ceiling
the framework itself imposes.

    python3 bench/bench.py

Reports requests/second for a few route shapes. Larz is synchronous (WSGI); for
the money-native use case (checkout, metering, dashboards) this is comfortably
fast — the bottleneck is your payment provider and database, not the framework.
"""
import io
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.models import connect
import larz.money as money


def build():
    connect(":memory:")
    app = Larz(secret="bench")
    money.enable(app, base_url="http://x", db=os.path.join(os.environ.get("TMPDIR", "/tmp"), "bench_m.db"))
    app.money.plan("pro", "$9/mo")

    @app.get("/plain")
    def plain(req):
        return "hello world"

    @app.get("/json")
    def js(req):
        return {"message": "hello", "items": [1, 2, 3], "ok": True}

    @app.get("/param/<id:int>")
    def param(req, id: int):
        return {"id": id, "doubled": id * 2}

    @app.plan("pro")
    @app.get("/pro")
    def pro(req):
        return "pro content"

    # entitle the bench session subject so /pro is served (not redirected)
    return app


_COOKIE = {"v": ""}


def _env(method, path):
    e = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "",
         "wsgi.input": io.BytesIO(b""), "CONTENT_LENGTH": "0",
         "HTTP_USER_AGENT": "bench", "REMOTE_ADDR": "127.0.0.1"}
    if _COOKIE["v"]:
        e["HTTP_COOKIE"] = _COOKIE["v"]
    return e


def hammer(app, method, path, n):
    env = _env(method, path)

    def sr(status, headers):
        pass
    # warm up
    for _ in range(1000):
        env["wsgi.input"] = io.BytesIO(b"")
        app(dict(env), sr)
    t0 = time.time()
    for _ in range(n):
        e = _env(method, path)
        list(app(e, sr))
    dt = time.time() - t0
    return n / dt, dt


def main():
    app = build()
    # a stable, entitled session so /pro is served (the realistic paid-route
    # hot path for a paying user: one indexed entitlement read + serve)
    _COOKIE["v"] = "larz_session=" + app.sessions.dump({"sid": "benchuser"})
    app.money.store.grant("benchuser", "plan:pro")
    n = 20000
    print("Larz %s — in-process dispatch throughput (requests/second)\n"
          % __import__("larz").__version__)
    print("  %-22s %12s   %s" % ("route", "req/s", "shape"))
    print("  " + "-" * 60)
    cases = [
        ("GET /plain", "GET", "/plain", "text response"),
        ("GET /json", "GET", "/json", "dict -> JSON"),
        ("GET /param/42", "GET", "/param/42", "typed path param + bind"),
        ("GET /pro (paid user)", "GET", "/pro", "money-gated, entitled (read + serve)"),
        ("GET /404", "GET", "/nope", "unmatched route"),
    ]
    for label, method, path, shape in cases:
        rps, _ = hammer(app, method, path, n)
        print("  %-22s %12s   %s" % (label, "{:,.0f}".format(rps), shape))
    print("\n  (in-process; real throughput is bounded by your WSGI server + IO)")


if __name__ == "__main__":
    main()
