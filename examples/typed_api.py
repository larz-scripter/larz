"""
Larz typed-API example — the v1.3 "Modern API" features in one file.

  * typed request binding (params validated & coerced from the signature)
  * dataclass request bodies
  * dependency injection (Depends)
  * Server-Sent Events (real-time over plain WSGI)
  * auto OpenAPI + a self-contained API explorer at /docs
  * CORS + startup/shutdown lifecycle

Run:  python3 examples/typed_api.py   →  http://127.0.0.1:8000/docs
"""
import time
from dataclasses import dataclass
from larz import Larz, Response, Depends, Query
import larz.api as api

app = Larz(secret="dev")
api.enable(app)
app.enable_cors(origins="*")

DB = {"items": []}


@app.on_startup
def seed():
    DB["items"].append({"id": 1, "name": "Starter", "price": 9.0})
    print("  seeded", len(DB["items"]), "items")


def store(req):
    return DB                       # a trivial injected dependency


@dataclass
class NewItem:
    name: str
    price: float
    qty: int = 1


@app.get("/items")
def list_items(req, limit: int = Query(20), db=Depends(store)):
    "List items (typed query param + injected store)."
    return {"items": db["items"][:limit]}


@app.post("/items")
def create_item(req, item: NewItem, db=Depends(store)):
    "Create an item from a validated JSON body."
    row = {"id": len(db["items"]) + 1, "name": item.name,
           "price": item.price * item.qty}
    db["items"].append(row)
    return Response.json(row, status=201)


@app.get("/items/<id:int>")
def get_item(req, id: int, db=Depends(store)):
    "Fetch one item by id (typed path param)."
    for r in db["items"]:
        if r["id"] == id:
            return r
    return Response.json({"error": "not found"}, status=404)


@app.get("/stream")
def stream(req, ticks: int = Query(5)):
    "Server-Sent Events — a live counter, no websockets needed."
    def events():
        for i in range(ticks):
            yield ("tick", str(i))
            time.sleep(0.2)
        yield ("done", "bye")
    return Response.sse(events())


if __name__ == "__main__":
    app.run()
