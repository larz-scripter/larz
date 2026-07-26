"""
larz.aserver — a minimal, zero-dependency ASGI server (asyncio, stdlib only).

So a Larz app can run in async mode with NO uvicorn/hypercorn install:

    app.run_async()      # HTTP/1.1 + WebSockets on 127.0.0.1:8000

It speaks just enough HTTP/1.1 (Connection: close per request) and RFC 6455
WebSockets to drive the app's ASGI interface. For production you can still use
uvicorn/hypercorn/daphne — the app is a standard ASGI callable — but this keeps
"runs on your machine with nothing to install" true even for async.
"""

import asyncio
import base64
import hashlib
import struct

_WS_MAGIC = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_REASON = {200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently",
           302: "Found", 303: "See Other", 400: "Bad Request", 401: "Unauthorized",
           402: "Payment Required", 403: "Forbidden", 404: "Not Found",
           422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error"}


async def _read_headers(reader):
    line = await reader.readline()
    if not line:
        return None
    try:
        method, target, _ = line.decode("latin-1").rstrip("\r\n").split(" ", 2)
    except ValueError:
        return None
    headers = []
    while True:
        h = await reader.readline()
        if h in (b"\r\n", b"\n", b""):
            break
        k, _, v = h.decode("latin-1").partition(":")
        headers.append((k.strip().lower().encode("latin-1"), v.strip().encode("latin-1")))
    path, _, qs = target.partition("?")
    return method, path, qs, headers


def _hdr(headers, name):
    for k, v in headers:
        if k == name:
            return v
    return b""


class _Server:
    def __init__(self, app):
        self.app = app

    async def handle(self, reader, writer):
        try:
            parsed = await _read_headers(reader)
            if not parsed:
                writer.close(); return
            method, path, qs, headers = parsed
            if _hdr(headers, b"upgrade").lower() == b"websocket":
                await self._ws(reader, writer, path, qs, headers)
            else:
                await self._http(reader, writer, method, path, qs, headers)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _http(self, reader, writer, method, path, qs, headers):
        clen = int(_hdr(headers, b"content-length") or b"0" or 0)
        body = await reader.readexactly(clen) if clen else b""
        peer = writer.get_extra_info("peername") or ("", 0)
        scope = {"type": "http", "method": method, "path": path,
                 "query_string": qs.encode("latin-1"), "headers": headers,
                 "client": (peer[0], peer[1]), "scheme": "http"}
        served = {"i": False}
        async def receive():
            if not served["i"]:
                served["i"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}
        async def send(msg):
            if msg["type"] == "http.response.start":
                st = msg["status"]
                writer.write(("HTTP/1.1 %d %s\r\n" % (st, _REASON.get(st, "OK"))).encode())
                for k, v in msg["headers"]:
                    writer.write(k + b": " + v + b"\r\n")
                writer.write(b"Connection: close\r\n\r\n")
            elif msg["type"] == "http.response.body":
                b = msg.get("body", b"")
                if b:
                    writer.write(b)
                    await writer.drain()
        await self.app(scope, receive, send)
        await writer.drain()

    async def _ws(self, reader, writer, path, qs, headers):
        key = _hdr(headers, b"sec-websocket-key")
        accept = base64.b64encode(hashlib.sha1(key + _WS_MAGIC).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                     b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        await writer.drain()
        peer = writer.get_extra_info("peername") or ("", 0)
        scope = {"type": "websocket", "path": path, "query_string": qs.encode("latin-1"),
                 "headers": headers, "client": (peer[0], peer[1])}
        state = {"connected": False, "closed": False}
        incoming = asyncio.Queue()
        reader_done = {"v": False}

        async def pump():
            try:
                while True:
                    frame = await _read_frame(reader)
                    if frame is None:
                        break
                    op, data = frame
                    if op == 0x8:          # close
                        break
                    if op in (0x1, 0x2):   # text / binary
                        await incoming.put((op, data))
            finally:
                reader_done["v"] = True
                await incoming.put(None)

        pump_task = asyncio.ensure_future(pump())

        async def receive():
            if not state["connected"]:
                state["connected"] = True
                return {"type": "websocket.connect"}
            item = await incoming.get()
            if item is None:
                return {"type": "websocket.disconnect", "code": 1000}
            op, data = item
            if op == 0x1:
                return {"type": "websocket.receive", "text": data.decode("utf-8", "replace")}
            return {"type": "websocket.receive", "bytes": data}

        async def send(msg):
            t = msg["type"]
            if t == "websocket.accept":
                pass                       # handshake already sent
            elif t == "websocket.send":
                if msg.get("text") is not None:
                    writer.write(_build_frame(0x1, msg["text"].encode("utf-8")))
                else:
                    writer.write(_build_frame(0x2, msg.get("bytes", b"")))
                await writer.drain()
            elif t == "websocket.close":
                if not state["closed"]:
                    state["closed"] = True
                    writer.write(_build_frame(0x8, struct.pack(">H", msg.get("code", 1000))))
                    await writer.drain()
        try:
            await self.app(scope, receive, send)
        finally:
            pump_task.cancel()


async def _read_frame(reader):
    try:
        h = await reader.readexactly(2)
    except asyncio.IncompleteReadError:
        return None
    op = h[0] & 0x0F
    masked = h[1] & 0x80
    length = h[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return op, payload


def _build_frame(opcode, payload):
    b = bytearray([0x80 | opcode])          # FIN + opcode
    n = len(payload)
    if n < 126:
        b.append(n)
    elif n < 65536:
        b.append(126); b += struct.pack(">H", n)
    else:
        b.append(127); b += struct.pack(">Q", n)
    b += payload                             # server->client frames are unmasked
    return bytes(b)


def serve(app, host="127.0.0.1", port=8000):
    """Run the app with the built-in asyncio ASGI server (blocking)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = _Server(app)
    # fire ASGI lifespan startup
    loop.run_until_complete(_lifespan_startup(app))
    coro = asyncio.start_server(server.handle, host, port)
    srv = loop.run_until_complete(coro)
    print("  Larz async (ASGI)  ->  http://%s:%d  (ws + http)" % (host, port))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n  bye.")
    finally:
        srv.close()
        loop.run_until_complete(srv.wait_closed())
        loop.close()


async def _lifespan_startup(app):
    try:
        app.startup()
    except Exception:
        pass
