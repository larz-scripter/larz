"""
Larz async example — a WebSocket chat room + async HTTP, on the built-in
zero-dependency ASGI server (no uvicorn required).

  * async def handlers
  * WebSockets with a path param (@app.websocket)
  * Server-Sent Events
  * runs on app.run_async() — pure stdlib asyncio

Run:  python3 examples/async_chat.py   →  http://127.0.0.1:8000
Open two browser tabs of the page and chat between them.
"""
import time
from larz import Larz, Response

app = Larz(secret="dev")
ROOMS = {}          # room -> set of WebSocket


@app.get("/")
async def home(req):
    return Response("""<!doctype html><meta charset=utf-8><title>Larz Chat</title>
<h1>Larz async chat</h1><input id=m placeholder="say something"><div id=log></div>
<script>
const ws = new WebSocket("ws://" + location.host + "/ws/lobby");
ws.onmessage = e => log.innerHTML += "<div>" + e.data + "</div>";
m.addEventListener("keydown", e => { if (e.key==="Enter"){ ws.send(m.value); m.value=""; } });
</script>""")


@app.websocket("/ws/<room>")
async def chat(ws):
    room = ws.params["room"]
    await ws.accept()
    peers = ROOMS.setdefault(room, set())
    peers.add(ws)
    await ws.send("— joined #%s (%d online) —" % (room, len(peers)))
    try:
        async for msg in ws:
            dead = set()
            for peer in peers:
                try:
                    await peer.send(msg)
                except Exception:
                    dead.add(peer)
            peers -= dead
    finally:
        peers.discard(ws)


@app.get("/time")
async def clock(req):
    "Server-Sent Events: a live clock."
    def tick():
        for _ in range(10):
            yield ("time", str(int(time.time())))
            time.sleep(1)
    return Response.sse(tick())


if __name__ == "__main__":
    app.run_async()          # built-in async server (http + websockets, zero deps)
