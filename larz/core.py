"""
larz.core — the from-scratch web engine.

A small, dependency-free WSGI framework: routing with typed path params,
Request/Response objects, signed-cookie sessions, before/after hooks, and a
stdlib dev server. No Flask, no Werkzeug — just the Python standard library.

The money-native layer (larz.money) and SEO helpers (larz.seo) plug into the
Larz app object defined here; the paywall is enforced in dispatch() by reading
metadata that the @app.paid / @app.metered decorators attach to handlers.
"""

import re
import os
import json
import hmac
import time
import base64
import hashlib
import uuid
import inspect
import mimetypes
import traceback as _traceback
from http.cookies import SimpleCookie
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

__all__ = ["Larz", "Request", "Response", "Blueprint", "UploadedFile",
           "get_flashed_messages", "WebSocket"]


# --------------------------------------------------------------------------- #
#  File uploads (multipart/form-data) — pure stdlib, no cgi module
# --------------------------------------------------------------------------- #
class UploadedFile:
    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self.data = data

    @property
    def size(self):
        return len(self.data)

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.data)
        return path

    def __repr__(self):
        return "<UploadedFile %r %d bytes>" % (self.filename, self.size)


def _parse_multipart(body, boundary):
    """Split a multipart/form-data body into (text_fields, files) dicts."""
    fields, files = {}, {}
    delim = b"--" + boundary.encode("latin-1")
    for part in body.split(delim):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, _, content = part.partition(b"\r\n\r\n")
        if not _:
            continue
        headers = {}
        for line in head.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower().decode("latin-1")] = v.strip().decode("latin-1")
        disp = headers.get("content-disposition", "")
        name = _cd_param(disp, "name")
        if name is None:
            continue
        filename = _cd_param(disp, "filename")
        if filename is not None:
            files[name] = UploadedFile(filename, headers.get("content-type",
                                       "application/octet-stream"), content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


def _cd_param(disposition, key):
    m = re.search(r'%s="((?:[^"\\]|\\.)*)"' % re.escape(key), disposition)
    if m:
        return m.group(1).replace('\\"', '"')
    return None


def get_flashed_messages(req, with_categories=False):
    """Pop and return one-shot flash messages stored in the session."""
    msgs = req.session.pop("_flashes", [])
    if with_categories:
        return [tuple(m) for m in msgs]
    return [m[1] for m in msgs]


# --------------------------------------------------------------------------- #
#  HTTP messages
# --------------------------------------------------------------------------- #
class Request:
    """Parsed view over a WSGI environ."""

    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = environ.get("PATH_INFO", "/") or "/"
        self.query = {k: v[0] if len(v) == 1 else v
                      for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self.params = {}          # filled by the router (path params)
        self.session = {}         # filled by the app (signed cookie)
        self._body = None
        self.remote_addr = environ.get("HTTP_X_FORWARDED_FOR",
                                       environ.get("REMOTE_ADDR", "")).split(",")[0].strip()
        self.user_agent = environ.get("HTTP_USER_AGENT", "")

    def header(self, name, default=None):
        # CONTENT_TYPE / CONTENT_LENGTH are CGI vars WITHOUT the HTTP_ prefix.
        key = name.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            return self.environ.get(key, default)
        return self.environ.get("HTTP_" + key, default)

    @property
    def cookies(self):
        jar = SimpleCookie(self.environ.get("HTTP_COOKIE", ""))
        return {k: m.value for k, m in jar.items()}

    @property
    def body(self):
        if self._body is None:
            try:
                size = int(self.environ.get("CONTENT_LENGTH") or 0)
            except (TypeError, ValueError):
                size = 0
            self._body = self.environ["wsgi.input"].read(size) if size else b""
        return self._body

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    def _parse_multipart(self):
        if getattr(self, "_mp", None) is None:
            ctype = self.header("Content-Type") or ""
            m = re.search(r"boundary=([^;]+)", ctype)
            if "multipart/form-data" in ctype and m:
                self._mp = _parse_multipart(self.body, m.group(1).strip().strip('"'))
            else:
                self._mp = ({}, {})
        return self._mp

    @property
    def form(self):
        ctype = self.header("Content-Type") or ""
        if "application/x-www-form-urlencoded" in ctype:
            return {k: v[0] if len(v) == 1 else v
                    for k, v in parse_qs(self.body.decode("utf-8")).items()}
        if "multipart/form-data" in ctype:
            return dict(self._parse_multipart()[0])
        return {}

    @property
    def files(self):
        """Uploaded files from a multipart/form-data request: name -> UploadedFile."""
        return self._parse_multipart()[1]

    @property
    def htmx(self):
        """True if the request came from HTMX (has the HX-Request header)."""
        return self.header("HX-Request") == "true"

    def flash(self, message, category="info"):
        """Queue a one-shot message for the next response (via the session).
        Read it with larz.get_flashed_messages(req)."""
        self.session.setdefault("_flashes", []).append([category, message])

    @property
    def subject(self):
        """Who this request is 'billed to': a logged-in user (stable across
        devices), else an API key, else the anonymous session id."""
        u = getattr(self, "user", None)
        if u is not None and getattr(u, "id", None) is not None:
            return "user:%s" % u.id
        ak = getattr(self, "api_key", None)
        if ak is not None and getattr(ak, "id", None) is not None:
            return "apikey:%s" % ak.id
        return self.session.get("user") or self.session.get("sid")


class Response:
    def __init__(self, body="", status=200, headers=None, content_type="text/html; charset=utf-8"):
        self.status = status
        self.headers = headers or {}
        if body is None:
            body = ""
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
            content_type = "application/json"
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.headers.setdefault("Content-Type", content_type)
        self._stream = None      # set by Response.stream()/sse() for streaming bodies

    @classmethod
    def redirect(cls, location, status=302):
        return cls("", status=status, headers={"Location": location})

    @classmethod
    def json(cls, data, status=200):
        return cls(json.dumps(data), status=status, content_type="application/json")

    @classmethod
    def stream(cls, iterator, status=200, content_type="application/octet-stream",
               headers=None):
        """Stream a response from an iterable of str/bytes chunks (WSGI streaming).
        Good for large downloads, CSV exports, or progressive output."""
        r = cls("", status=status, headers=headers, content_type=content_type)
        def _chunks():
            for c in iterator:
                yield c if isinstance(c, bytes) else str(c).encode("utf-8")
        r._stream = _chunks()
        return r

    @classmethod
    def sse(cls, events, headers=None):
        """Server-Sent Events stream. `events` yields strings (data) or
        (event_name, data) tuples; Larz frames them as text/event-stream.
        Works over plain WSGI — real-time push without websockets."""
        def _frames():
            for e in events:
                if isinstance(e, tuple):
                    name, data = e
                    yield ("event: %s\n" % name).encode()
                else:
                    data = e
                for line in str(data).split("\n"):
                    yield ("data: %s\n" % line).encode()
                yield b"\n"
        h = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        h.update(headers or {})
        r = cls("", headers=h, content_type="text/event-stream")
        r._stream = _frames()
        return r

    def hx_trigger(self, event, detail=None):
        """Fire a client-side HTMX event via the HX-Trigger response header."""
        self.headers["HX-Trigger"] = event if detail is None else json.dumps({event: detail})
        return self

    @classmethod
    def hx_redirect(cls, location):
        """Client-side redirect that HTMX honours (HX-Redirect header)."""
        return cls("", headers={"HX-Redirect": location})

    def set_cookie(self, key, value, http_only=True, path="/", max_age=None):
        parts = ["%s=%s" % (key, value), "Path=%s" % path, "SameSite=Lax"]
        if http_only:
            parts.append("HttpOnly")
        if max_age is not None:
            parts.append("Max-Age=%d" % max_age)
        # allow multiple Set-Cookie headers
        self.headers.setdefault("_set_cookie", [])
        self.headers["_set_cookie"].append("; ".join(parts))


_STATUS = {200: "200 OK", 201: "201 Created", 204: "204 No Content",
           302: "302 Found", 303: "303 See Other", 400: "400 Bad Request",
           401: "401 Unauthorized", 402: "402 Payment Required",
           403: "403 Forbidden", 404: "404 Not Found",
           429: "429 Too Many Requests", 500: "500 Internal Server Error"}


def _status_line(code):
    return _STATUS.get(code, "%d Status" % code)


# --------------------------------------------------------------------------- #
#  ASGI helpers
# --------------------------------------------------------------------------- #
def _scope_to_environ(scope, body):
    """Build a WSGI-style environ from an ASGI http scope + body, so the existing
    Request/Response machinery works unchanged under async."""
    import io as _io
    environ = {
        "REQUEST_METHOD": scope.get("method", "GET"),
        "PATH_INFO": scope.get("path", "/"),
        "QUERY_STRING": (scope.get("query_string", b"") or b"").decode("latin-1"),
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.input": _io.BytesIO(body),
        "wsgi.url_scheme": scope.get("scheme", "http"),
        "CONTENT_LENGTH": str(len(body)),
        "REMOTE_ADDR": (scope.get("client") or ["", 0])[0] or "",
    }
    for name, value in scope.get("headers", []):
        key = name.decode("latin-1").upper().replace("-", "_")
        val = value.decode("latin-1")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[key] = val
        else:
            environ["HTTP_" + key] = val
    return environ


class WebSocket:
    """A minimal ASGI WebSocket. Handlers registered with @app.websocket receive
    one of these:

        @app.websocket("/ws/<room>")
        async def chat(ws):
            await ws.accept()
            async for msg in ws:
                await ws.send("echo: " + msg)
    """
    def __init__(self, scope, receive, send, params=None):
        self.scope = scope
        self._receive = receive
        self._send = send
        self.params = params or {}
        self.accepted = False
        self.closed = False

    async def accept(self, subprotocol=None):
        msg = await self._receive()          # websocket.connect
        if msg.get("type") != "websocket.connect":
            pass
        await self._send({"type": "websocket.accept", "subprotocol": subprotocol})
        self.accepted = True

    async def receive(self):
        """Return the next text (str) or bytes message, or None if disconnected."""
        msg = await self._receive()
        if msg["type"] == "websocket.disconnect":
            self.closed = True
            return None
        return msg.get("text") if msg.get("text") is not None else msg.get("bytes")

    async def send(self, data):
        if isinstance(data, (bytes, bytearray)):
            await self._send({"type": "websocket.send", "bytes": bytes(data)})
        else:
            await self._send({"type": "websocket.send", "text": str(data)})

    async def send_json(self, obj):
        await self.send(json.dumps(obj))

    async def close(self, code=1000):
        if not self.closed:
            self.closed = True
            try:
                await self._send({"type": "websocket.close", "code": code})
            except Exception:
                pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self.receive()
        if msg is None:
            raise StopAsyncIteration
        return msg


# --------------------------------------------------------------------------- #
#  Routing
# --------------------------------------------------------------------------- #
_CONVERTERS = {"int": (r"[0-9]+", int), "str": (r"[^/]+", str),
               "path": (r".+", str), "slug": (r"[a-z0-9-]+", str)}


def _compile(pattern):
    """Turn '/user/<id:int>/posts/<slug>' into a regex + converter map."""
    conv = {}
    regex = "^"
    for part in re.split(r"(<[^>]+>)", pattern):
        if part.startswith("<") and part.endswith(">"):
            inner = part[1:-1]
            name, _, kind = inner.partition(":")
            kind = kind or "str"
            pat, fn = _CONVERTERS.get(kind, _CONVERTERS["str"])
            conv[name] = fn
            regex += "(?P<%s>%s)" % (name, pat)
        else:
            regex += re.escape(part)
    return re.compile(regex + "$"), conv


class _Route:
    def __init__(self, methods, pattern, handler, **opts):
        self.methods = set(m.upper() for m in methods)
        self.pattern = pattern
        self.regex, self.conv = _compile(pattern)
        self.handler = handler
        self.opts = opts


# --------------------------------------------------------------------------- #
#  Signed-cookie sessions (stdlib hmac — no external dep)
# --------------------------------------------------------------------------- #
class _Sessions:
    def __init__(self, secret, cookie="larz_session"):
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.cookie = cookie

    def _sign(self, raw):
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()[:32]

    def load(self, req):
        """Decode the signed cookie into a dict (no side effects — the app
        injects a fresh sid and decides when to persist)."""
        token = req.cookies.get(self.cookie, "")
        data = {}
        if "." in token:
            payload, sig = token.rsplit(".", 1)
            try:
                raw = base64.urlsafe_b64decode(payload.encode())
                if hmac.compare_digest(self._sign(raw), sig):
                    data = json.loads(raw.decode())
            except Exception:
                data = {}
        return data

    def dump(self, data):
        raw = json.dumps(data, separators=(",", ":")).encode()
        payload = base64.urlsafe_b64encode(raw).decode()
        return "%s.%s" % (payload, self._sign(raw))


# --------------------------------------------------------------------------- #
#  The app
# --------------------------------------------------------------------------- #
class Blueprint:
    """A group of routes registered under a common url prefix."""

    def __init__(self, name, prefix=""):
        self.name = name
        self.prefix = prefix.rstrip("/")
        self._pending = []

    def route(self, pattern, methods=("GET",), **opts):
        def deco(fn):
            self._pending.append((methods, pattern, fn, opts))
            return fn
        return deco

    def get(self, pattern, **opts):
        return self.route(pattern, ("GET",), **opts)

    def post(self, pattern, **opts):
        return self.route(pattern, ("POST",), **opts)


class Larz:
    def __init__(self, secret="dev-insecure-change-me", name="larz-app",
                 debug=False, templates=None):
        self.name = name
        self.debug = debug
        self.routes = []
        self.sessions = _Sessions(secret)
        self._before = []
        self._after = []
        self._error_handlers = {}
        self._on_startup = []
        self._on_shutdown = []
        self._started = False
        self._ws_routes = []      # (regex, converters, async handler)
        self.money = None     # attached by larz.money.enable(app, ...)
        self.seo = None       # attached by larz.seo.enable(app, ...)
        self.templates = None
        if templates:
            self.use_templates(templates)

    # -- registration ------------------------------------------------------- #
    def route(self, pattern, methods=("GET",), **opts):
        def deco(fn):
            self.routes.append(_Route(methods, pattern, fn, **opts))
            return fn
        return deco

    def register(self, blueprint):
        """Mount a Blueprint's routes under its prefix."""
        for methods, pattern, fn, opts in blueprint._pending:
            self.routes.append(_Route(methods, blueprint.prefix + pattern, fn, **opts))
        return self

    # -- templating --------------------------------------------------------- #
    def use_templates(self, directory, auto_reload=None, globals=None):
        from .templating import Environment
        self.templates = Environment(
            directory, auto_reload=self.debug if auto_reload is None else auto_reload,
            globals=globals)
        return self.templates

    def render(self, template_name, status=200, **ctx):
        html = self.templates.render(template_name, **ctx)
        return Response(html, status=status)

    # -- static files ------------------------------------------------------- #
    def static(self, url_prefix, directory):
        directory = os.path.abspath(directory)
        prefix = url_prefix.rstrip("/")

        @self.route(prefix + "/<path:relpath>", sitemap=False)
        def _serve(req):
            rel = req.params["relpath"]
            full = os.path.abspath(os.path.join(directory, rel))
            if not full.startswith(directory + os.sep) or not os.path.isfile(full):
                return Response("not found", status=404)          # traversal guard
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as f:
                data = f.read()
            return Response(data, headers={"Cache-Control": "public, max-age=3600"},
                            content_type=ctype)
        return self

    # -- middleware --------------------------------------------------------- #
    def use(self, mw):
        """Register middleware: a before-hook (req), an after-hook (req, resp),
        or an object/func carrying an `.after` attribute (e.g. CSRF)."""
        after = getattr(mw, "after", None)
        try:
            argc = len(inspect.signature(mw).parameters)
        except (TypeError, ValueError):
            argc = 1
        if argc >= 2 and after is None:
            self._after.append(mw)
        else:
            self._before.append(mw)
            if after:
                self._after.append(after)
        return mw

    def get(self, pattern, **opts):
        return self.route(pattern, ("GET",), **opts)

    def post(self, pattern, **opts):
        return self.route(pattern, ("POST",), **opts)

    def websocket(self, pattern):
        """Register an async WebSocket handler (ASGI only):

            @app.websocket("/ws/<room>")
            async def handler(ws): ...
        """
        regex, conv = _compile(pattern)
        def deco(fn):
            self._ws_routes.append((regex, conv, fn))
            return fn
        return deco

    def before(self, fn):
        self._before.append(fn); return fn

    def after(self, fn):
        self._after.append(fn); return fn

    def errorhandler(self, code):
        def deco(fn):
            self._error_handlers[code] = fn; return fn
        return deco

    # -- lifecycle ---------------------------------------------------------- #
    def on_startup(self, fn):
        """Run `fn()` once when the app starts serving (or first request)."""
        self._on_startup.append(fn); return fn

    def on_shutdown(self, fn):
        self._on_shutdown.append(fn); return fn

    def startup(self):
        if self._started:
            return
        self._started = True
        for fn in self._on_startup:
            fn()

    def shutdown(self):
        for fn in self._on_shutdown:
            try: fn()
            except Exception: pass

    def enable_cors(self, origins="*", methods="GET,POST,PUT,PATCH,DELETE,OPTIONS",
                    headers="Content-Type,Authorization", credentials=False):
        """Add permissive-by-default CORS headers and answer preflight OPTIONS."""
        def _cors_before(req):
            if req.method == "OPTIONS":
                return Response("", status=204, headers=self._cors_headers(
                    req, origins, methods, headers, credentials))
            return None
        def _cors_after(req, resp):
            for k, v in self._cors_headers(req, origins, methods, headers, credentials).items():
                resp.headers.setdefault(k, v)
        self._before.append(_cors_before)
        self._after.append(_cors_after)
        return self

    @staticmethod
    def _cors_headers(req, origins, methods, headers, credentials):
        origin = req.header("Origin", "")
        allow = origin if (origins == "*" and credentials) else origins
        if isinstance(origins, (list, tuple)):
            allow = origin if origin in origins else ""
        h = {"Access-Control-Allow-Origin": allow or "*",
             "Access-Control-Allow-Methods": methods,
             "Access-Control-Allow-Headers": headers}
        if credentials:
            h["Access-Control-Allow-Credentials"] = "true"
        return h

    # -- money-native decorators (metadata only; enforced in dispatch) ------ #
    def paid(self, price, sku=None, days=None, trial_days=None, plan=None):
        """Gate a route behind a one-off, subscription, or trial payment.

        price       "$9"  one-off  |  "$9/mo" / "$99/yr" subscription
        trial_days  grant a free trial on first access before charging
        plan        name of a registered plan (groups routes under one sku)
        """
        spec = {"price": price, "sku": sku, "days": days,
                "trial_days": trial_days, "plan": plan}
        def deco(fn):
            fn._larz_paid = spec
            return fn
        return deco

    def plan(self, name):
        """Gate a route on membership of a registered plan (see app.money.plan)."""
        def deco(fn):
            fn._larz_plan = name
            return fn
        return deco

    def metered(self, price, sku=None):
        """Charge per call against the caller's prepaid credit balance."""
        spec = {"price": price, "sku": sku}
        def deco(fn):
            fn._larz_metered = spec
            return fn
        return deco

    # -- dispatch ----------------------------------------------------------- #
    def _match(self, req):
        allowed = False
        for r in self.routes:
            m = r.regex.match(req.path)
            if not m:
                continue
            if req.method not in r.methods:
                allowed = True
                continue
            params = {}
            for k, v in m.groupdict().items():
                params[k] = r.conv.get(k, str)(v)
            return r, params
        return (None, "405") if allowed else (None, None)

    def _resolve(self, req):
        """Run the request pipeline up to (not including) the handler call.
        Returns a Response to short-circuit, or (handler, kwargs) to invoke.
        Shared by the sync (WSGI) and async (ASGI) dispatchers."""
        # global before-hooks (middleware) run first — they can short-circuit
        for hook in self._before:
            out = hook(req)
            if out is not None:
                return self._coerce(out)

        route, params = self._match(req)
        if route is None:
            return self._error(405 if params == "405" else 404, req)
        req.params = params
        handler = route.handler

        for guard in getattr(handler, "_larz_guards", ()):
            out = guard(req)
            if out is not None:
                return self._coerce(out)

        # --- money-native enforcement ------------------------------------- #
        if self.money:
            paid = getattr(handler, "_larz_paid", None)
            if paid:
                gate = self.money.enforce_paid(req, paid, route)
                if gate is not None:
                    return self._coerce(gate)
            metered = getattr(handler, "_larz_metered", None)
            if metered:
                gate = self.money.enforce_metered(req, metered, route)
                if gate is not None:
                    return self._coerce(gate)
            plan = getattr(handler, "_larz_plan", None)
            if plan:
                gate = self.money.enforce_plan(req, plan, route)
                if gate is not None:
                    return self._coerce(gate)

        from .params import needs_binding, bind
        kwargs = {}
        if needs_binding(handler):
            kwargs, err = bind(handler, req)
            if err is not None:
                return err
        return (handler, kwargs)

    def dispatch(self, req):
        r = self._resolve(req)
        if isinstance(r, Response):
            return r
        handler, kwargs = r
        try:
            return self._coerce(handler(req, **kwargs))
        except Exception as exc:  # noqa
            _traceback.print_exc()
            return self._error(500, req, exc)

    async def adispatch(self, req):
        """Async dispatch — awaits `async def` handlers, runs sync ones directly."""
        r = self._resolve(req)
        if isinstance(r, Response):
            return r
        handler, kwargs = r
        try:
            out = handler(req, **kwargs)
            if inspect.isawaitable(out):
                out = await out
            return self._coerce(out)
        except Exception as exc:  # noqa
            _traceback.print_exc()
            return self._error(500, req, exc)

    def _coerce(self, out):
        if isinstance(out, Response):
            return out
        if isinstance(out, tuple):
            body, status = (out + (200,))[:2]
            return Response(body, status=status)
        return Response(out)

    def _error(self, code, req, exc=None):
        h = self._error_handlers.get(code)
        if h:
            return self._coerce(h(req))
        if code == 500 and self.debug and exc is not None:
            import html as _html
            tb = _html.escape(_traceback.format_exc())
            body = ("<h1>500 — %s</h1><p>%s</p><pre style='background:#f6f6f6;"
                    "padding:1em;overflow:auto'>%s</pre>"
                    % (type(exc).__name__, _html.escape(str(exc)), tb))
            return Response(body, status=500)
        msgs = {404: "Not Found", 405: "Method Not Allowed",
                500: "Internal Server Error"}
        return Response("%d %s" % (code, msgs.get(code, "Error")), status=code)

    # -- shared request lifecycle ------------------------------------------ #
    def _prepare(self, req):
        req.session = self.sessions.load(req)
        had_cookie = self.sessions.cookie in req.cookies
        if "sid" not in req.session:
            req.session["sid"] = uuid.uuid4().hex
        return had_cookie, dict(req.session)

    def _finalize(self, req, resp, had_cookie, original_session):
        for hook in self._after:
            hook(req, resp)
        if not had_cookie or req.session != original_session:
            resp.set_cookie(self.sessions.cookie, self.sessions.dump(req.session))
        headers = [(k, v) for k, v in resp.headers.items() if k != "_set_cookie"]
        for c in resp.headers.get("_set_cookie", []):
            headers.append(("Set-Cookie", c))
        return headers

    # -- protocol entry point (WSGI or ASGI, detected by call shape) -------- #
    def __call__(self, *args):
        if (len(args) == 3 and isinstance(args[0], dict)
                and args[0].get("type") in ("http", "websocket", "lifespan")):
            return self._asgi(*args)          # returns a coroutine (awaited by server)
        return self._wsgi(*args)

    # -- WSGI --------------------------------------------------------------- #
    def _wsgi(self, environ, start_response):
        if not self._started:
            self.startup()          # lazy startup (works under gunicorn too)
        req = Request(environ)
        had_cookie, original = self._prepare(req)
        resp = self.dispatch(req)
        headers = self._finalize(req, resp, had_cookie, original)
        start_response(_status_line(resp.status), headers)
        if resp._stream is not None:
            return resp._stream
        return [resp.body]

    # -- ASGI (async core; runs on uvicorn/hypercorn) ----------------------- #
    async def _asgi(self, scope, receive, send):
        t = scope["type"]
        if t == "lifespan":
            return await self._asgi_lifespan(scope, receive, send)
        if t == "websocket":
            return await self._asgi_websocket(scope, receive, send)
        return await self._asgi_http(scope, receive, send)

    async def _asgi_lifespan(self, scope, receive, send):
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                try:
                    self.startup()
                    await send({"type": "lifespan.startup.complete"})
                except Exception as e:  # noqa
                    await send({"type": "lifespan.startup.failed", "message": str(e)})
            elif msg["type"] == "lifespan.shutdown":
                self.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _asgi_http(self, scope, receive, send):
        if not self._started:
            self.startup()
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"") or b""
            more = msg.get("more_body", False)
        environ = _scope_to_environ(scope, body)
        req = Request(environ)
        had_cookie, original = self._prepare(req)
        resp = await self.adispatch(req)
        headers = self._finalize(req, resp, had_cookie, original)
        raw_headers = [(k.encode("latin-1"), str(v).encode("latin-1")) for k, v in headers]
        await send({"type": "http.response.start", "status": resp.status,
                    "headers": raw_headers})
        if resp._stream is not None:
            stream = resp._stream
            if hasattr(stream, "__aiter__"):
                async for chunk in stream:
                    await send({"type": "http.response.body",
                                "body": chunk if isinstance(chunk, bytes) else str(chunk).encode(),
                                "more_body": True})
            else:
                for chunk in stream:
                    await send({"type": "http.response.body",
                                "body": chunk if isinstance(chunk, bytes) else str(chunk).encode(),
                                "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await send({"type": "http.response.body", "body": resp.body})

    async def _asgi_websocket(self, scope, receive, send):
        path = scope.get("path", "/")
        for regex, conv, handler in self._ws_routes:
            m = regex.match(path)
            if m:
                params = {k: conv.get(k, str)(v) for k, v in m.groupdict().items()}
                ws = WebSocket(scope, receive, send, params)
                try:
                    await handler(ws)
                except Exception:  # noqa
                    _traceback.print_exc()
                    await ws.close(1011)
                return
        # no matching route: reject the handshake
        await send({"type": "websocket.close", "code": 1000})

    # -- dev server --------------------------------------------------------- #
    def run(self, host="127.0.0.1", port=8000):
        # let plugins register their built-in routes
        if self.seo:
            self.seo.install_routes()
        print("  Larz  money-native  ->  http://%s:%d  (%d routes)"
              % (host, port, len(self.routes)))
        self.startup()
        srv = make_server(host, port, self)  # noqa
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n  bye.")
        finally:
            self.shutdown()

    def run_async(self, host="127.0.0.1", port=8000):
        """Run in async mode with the built-in zero-dependency ASGI server
        (HTTP + WebSockets). For production, use uvicorn/hypercorn — the app is a
        standard ASGI callable: `uvicorn module:app`."""
        if self.seo:
            self.seo.install_routes()
        from .aserver import serve
        serve(self, host, port)
