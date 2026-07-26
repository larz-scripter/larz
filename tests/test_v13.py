"""v1.3 Modern API tests — typed binding, dependency injection, streaming/SSE,
CORS, lifecycle, and typed OpenAPI. Plain python3, in-process, no pytest.

Dataclass-body binding needs Python 3.7+; those checks skip on 3.6."""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response, Depends, Query, Path
from larz.testing import Client
import larz.api as api

PY37 = sys.version_info >= (3, 7)
P = [0]; F = [0]
def ck(name, cond):
    if cond: P[0] += 1; print("  ok   " + name)
    else: F[0] += 1; print("  FAIL " + name)


def test_typed_binding():
    app = Larz(secret="x")
    @app.post("/items")
    def items(req, name: str, qty: int = 1, active: bool = True):
        return {"name": name, "qty": qty, "active": active}
    @app.get("/search")
    def search(req, q: str, limit: int = 10):
        return {"q": q, "limit": limit}
    @app.get("/u/<id:int>")
    def user(req, id: int):
        return {"id": id, "t": type(id).__name__}
    c = Client(app)
    ck("body coerces types", c.post("/items", json={"name": "pen", "qty": "3"}).json == {"name": "pen", "qty": 3, "active": True})
    ck("form body binds", c.post("/items", form={"name": "ink"}).json["name"] == "ink")
    ck("bool coercion", c.post("/items", json={"name": "x", "active": "false"}).json["active"] is False)
    ck("missing required -> 422", c.post("/items", json={"qty": 2}).status == 422)
    ck("422 lists the field", "name" in c.post("/items", json={}).json["fields"])
    ck("bad int -> 422", c.post("/items", json={"name": "x", "qty": "nope"}).status == 422)
    ck("query params bind", c.get("/search?q=hi&limit=5").json == {"q": "hi", "limit": 5})
    ck("query default applies", c.get("/search?q=hi").json["limit"] == 10)
    ck("query required -> 422", c.get("/search").status == 422)
    ck("path param typed", c.get("/u/42").json == {"id": 42, "t": "int"})
    # req-only handlers untouched
    @app.get("/plain")
    def plain(req): return "hi"
    ck("req-only handler still works", c.get("/plain").text == "hi")


def test_markers_and_di():
    app = Larz(secret="x")
    calls = [0]
    def get_db(req):
        calls[0] += 1
        return {"db": True}
    def get_conf():
        return "conf"
    @app.get("/x")
    def x(req, page: int = Query(1), db=Depends(get_db), conf=Depends(get_conf)):
        return {"page": page, "db": db["db"], "conf": conf}
    c = Client(app)
    ck("Query marker default", c.get("/x").json["page"] == 1)
    ck("Query marker value", c.get("/x?page=7").json["page"] == 7)
    ck("Depends with req injected", c.get("/x").json["db"] is True)
    ck("Depends no-arg injected", c.get("/x").json["conf"] == "conf")
    # dep cache: two deps in one request, get_db called once per request
    calls[0] = 0
    @app.get("/y")
    def y(req, a=Depends(get_db), b=Depends(get_db)):
        return {"same": a is b}
    ck("Depends cached per request", c.get("/y").json["same"] is True and calls[0] == 1)


def test_dataclass_body():
    if not PY37:
        print("  skip dataclass-body (needs py3.7+)"); return
    from dataclasses import dataclass
    @dataclass
    class Order:
        product: str
        price: float
        qty: int = 1
    app = Larz(secret="x")
    @app.post("/order")
    def order(req, o: Order):
        return {"total": o.price * o.qty, "product": o.product}
    c = Client(app)
    ck("dataclass body binds", c.post("/order", json={"product": "book", "price": 9.5, "qty": 2}).json == {"total": 19.0, "product": "book"})
    ck("dataclass default field", c.post("/order", json={"product": "x", "price": 5}).json["total"] == 5.0)
    ck("dataclass missing required -> 422", c.post("/order", json={"product": "x"}).status == 422)
    ck("dataclass bad type -> 422", c.post("/order", json={"product": "x", "price": "abc"}).status == 422)


def test_streaming_sse():
    app = Larz(secret="x")
    @app.get("/dl")
    def dl(req):
        return Response.stream((("row%d\n" % i) for i in range(3)), content_type="text/csv")
    @app.get("/ev")
    def ev(req):
        return Response.sse(["a", ("named", "b"), "multi\nline"])
    c = Client(app)
    r = c.get("/dl")
    ck("stream body assembled", r.text == "row0\nrow1\nrow2\n")
    ck("stream content-type", r.header("Content-Type") == "text/csv")
    s = c.get("/ev")
    ck("sse content-type", s.header("Content-Type") == "text/event-stream")
    ck("sse data frames", "data: a\n" in s.text and "event: named\ndata: b\n" in s.text)
    ck("sse multiline framed", "data: multi\ndata: line\n" in s.text)


def test_cors_lifecycle():
    app = Larz(secret="x")
    started = []
    @app.on_startup
    def s(): started.append(1)
    @app.get("/a")
    def a(req): return "a"
    app.enable_cors(origins="*")
    c = Client(app)
    ck("lifecycle not run before requests", started == [])
    c.get("/a")
    ck("startup ran on first request", started == [1])
    c.get("/a")
    ck("startup runs once", started == [1])
    ck("cors header on response", c.get("/a", headers={"Origin": "https://x.com"}).header("Access-Control-Allow-Origin") == "*")
    ck("cors preflight 204", c.request("OPTIONS", "/a").status == 204)
    # restricted origins
    app2 = Larz(secret="x")
    @app2.get("/b")
    def b(req): return "b"
    app2.enable_cors(origins=["https://ok.com"])
    c2 = Client(app2)
    ck("cors allows listed origin", c2.get("/b", headers={"Origin": "https://ok.com"}).header("Access-Control-Allow-Origin") == "https://ok.com")


def test_typed_openapi():
    app = Larz(secret="x")
    api.enable(app)
    @app.post("/widgets")
    def widgets(req, name: str, size: int = 3):
        "Create a widget"
        return {}
    @app.get("/widgets/<id:int>")
    def widget(req, id: int, verbose: bool = Query(False)):
        return {}
    app.enable_docs(title="T", version="9")
    c = Client(app)
    spec = c.get("/openapi.json").json
    post = spec["paths"]["/widgets"]["post"]
    ck("openapi summary from docstring", post["summary"] == "Create a widget")
    body = post["requestBody"]["content"]["application/json"]["schema"]
    ck("openapi body props typed", body["properties"]["size"]["type"] == "integer")
    ck("openapi required from no-default", body.get("required") == ["name"])
    get = spec["paths"]["/widgets/{id}"]["get"]
    kinds = {p["name"]: p["in"] for p in get["parameters"]}
    ck("openapi path param", kinds.get("id") == "path")
    ck("openapi query param", kinds.get("verbose") == "query")
    ck("docs page self-contained (no CDN)", "http" not in c.get("/docs").text.split("<script>")[0] or "cdn" not in c.get("/docs").text.lower())


def main():
    for t in [test_typed_binding, test_markers_and_di, test_dataclass_body,
              test_streaming_sse, test_cors_lifecycle, test_typed_openapi]:
        print("\n# " + t.__name__)
        t()
    print("\n%d passed, %d failed" % (P[0], F[0]))
    return 1 if F[0] else 0


if __name__ == "__main__":
    sys.exit(main())
