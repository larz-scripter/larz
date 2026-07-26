"""
Larz vs Flask vs FastAPI — in-process dispatch throughput (framework overhead).

Measures how many requests/second each framework can dispatch through its own
callable, with no network. This isolates framework cost. It is NOT a real-world
concurrency test: FastAPI's advantage is async I/O under load, which this does not
measure. What it does show is the per-request overhead each framework adds — where
Larz's zero-dependency, no-Pydantic design is competitive.

Run (needs flask + fastapi installed just for the comparison):
    pip install flask fastapi
    python3 bench/compare.py

Larz itself never depends on Flask or FastAPI — they're only imported here to
benchmark against.
"""
import io
import sys
import time
import json
import asyncio


def _wsgi_rps(app, path, n, body=b""):
    env = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": "",
           "CONTENT_LENGTH": str(len(body)), "SERVER_NAME": "b", "SERVER_PORT": "80",
           "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(body),
           "wsgi.errors": sys.stderr, "HTTP_HOST": "b"}
    def sr(s, h, e=None): pass
    for _ in range(500):
        env["wsgi.input"] = io.BytesIO(body); list(app(dict(env, **{"wsgi.input": io.BytesIO(body)}), sr))
    t0 = time.time()
    for _ in range(n):
        e = dict(env); e["wsgi.input"] = io.BytesIO(body)
        list(app(e, sr))
    return n / (time.time() - t0)


def _asgi_rps(app, path, n):
    loop = asyncio.new_event_loop()
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"",
             "headers": [], "client": ("127.0.0.1", 0), "scheme": "http"}
    async def one():
        inbox = [{"type": "http.request", "body": b"", "more_body": False}]
        async def receive(): return inbox.pop(0) if inbox else {"type": "http.disconnect"}
        async def send(m): pass
        await app(scope, receive, send)
    for _ in range(500):
        loop.run_until_complete(one())
    t0 = time.time()
    for _ in range(n):
        loop.run_until_complete(one())
    dt = time.time() - t0
    loop.close()
    return n / dt


def build_larz():
    from larz import Larz
    app = Larz(secret="b")
    @app.get("/plain")
    def p(req): return "hello world"
    @app.get("/json")
    def j(req): return {"message": "hello", "n": 3, "ok": True}
    return app


def build_flask():
    from flask import Flask, jsonify
    app = Flask(__name__)
    @app.get("/plain")
    def p(): return "hello world"
    @app.get("/json")
    def j(): return jsonify({"message": "hello", "n": 3, "ok": True})
    return app.wsgi_app


def build_fastapi():
    from fastapi import FastAPI
    app = FastAPI()
    @app.get("/plain")
    async def p(): return "hello world"
    @app.get("/json")
    async def j(): return {"message": "hello", "n": 3, "ok": True}
    return app


def main():
    n = 20000
    print("In-process dispatch throughput (requests/second) — higher is better\n")
    print("  %-10s %14s %14s   %s" % ("route", "req/s", "vs FastAPI", "framework"))
    print("  " + "-" * 62)
    results = {}
    larz = build_larz()
    results["Larz (WSGI)"] = {"plain": _wsgi_rps(larz, "/plain", n),
                              "json": _wsgi_rps(larz, "/json", n), "kind": "wsgi"}
    try:
        flask = build_flask()
        results["Flask (WSGI)"] = {"plain": _wsgi_rps(flask, "/plain", n),
                                   "json": _wsgi_rps(flask, "/json", n), "kind": "wsgi"}
    except Exception as e:
        print("  (flask not available: %s)" % e)
    fa = None
    try:
        fa = build_fastapi()
        results["FastAPI (ASGI)"] = {"plain": _asgi_rps(fa, "/plain", n),
                                     "json": _asgi_rps(fa, "/json", n), "kind": "asgi"}
    except Exception as e:
        print("  (fastapi not available: %s)" % e)

    fa_json = results.get("FastAPI (ASGI)", {}).get("json")
    for route in ("plain", "json"):
        print("  %s:" % route)
        for name, r in results.items():
            rps = r[route]
            rel = ("%.1fx" % (rps / fa_json)) if (fa_json and route == "json") else ""
            print("    %-16s %12s   %s" % (name, "{:,.0f}".format(rps), rel))
    print("\n  Note: in-process overhead only. FastAPI's real strength is async I/O")
    print("  concurrency under load, which this does not measure. Larz is synchronous;")
    print("  for money-native apps the bottleneck is your provider + database, not this.")


if __name__ == "__main__":
    main()
