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
import json
import hmac
import time
import base64
import hashlib
import uuid
from http.cookies import SimpleCookie
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

__all__ = ["Larz", "Request", "Response"]


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
        key = "HTTP_" + name.upper().replace("-", "_")
        return self.environ.get(key, default)

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

    @property
    def form(self):
        if "application/x-www-form-urlencoded" not in (self.header("Content-Type") or ""):
            return {}
        return {k: v[0] if len(v) == 1 else v
                for k, v in parse_qs(self.body.decode("utf-8")).items()}

    @property
    def subject(self):
        """Who this request is 'billed to': logged-in user, else the session id."""
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

    @classmethod
    def redirect(cls, location, status=302):
        return cls("", status=status, headers={"Location": location})

    @classmethod
    def json(cls, data, status=200):
        return cls(json.dumps(data), status=status, content_type="application/json")

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
class Larz:
    def __init__(self, secret="dev-insecure-change-me", name="larz-app"):
        self.name = name
        self.routes = []
        self.sessions = _Sessions(secret)
        self._before = []
        self._after = []
        self._error_handlers = {}
        self.money = None     # attached by larz.money.enable(app, ...)
        self.seo = None       # attached by larz.seo.enable(app, ...)

    # -- registration ------------------------------------------------------- #
    def route(self, pattern, methods=("GET",), **opts):
        def deco(fn):
            self.routes.append(_Route(methods, pattern, fn, **opts))
            return fn
        return deco

    def get(self, pattern, **opts):
        return self.route(pattern, ("GET",), **opts)

    def post(self, pattern, **opts):
        return self.route(pattern, ("POST",), **opts)

    def before(self, fn):
        self._before.append(fn); return fn

    def after(self, fn):
        self._after.append(fn); return fn

    def errorhandler(self, code):
        def deco(fn):
            self._error_handlers[code] = fn; return fn
        return deco

    # -- money-native decorators (metadata only; enforced in dispatch) ------ #
    def paid(self, price, sku=None, days=None):
        """Gate a route behind a one-off or subscription payment."""
        spec = {"price": price, "sku": sku, "days": days}
        def deco(fn):
            fn._larz_paid = spec
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

    def dispatch(self, req):
        route, params = self._match(req)
        if route is None:
            code = 405 if params == "405" else 404
            return self._error(code, req)
        req.params = params
        handler = route.handler

        # before hooks may short-circuit
        for hook in self._before:
            out = hook(req)
            if out is not None:
                return self._coerce(out)

        # --- money-native enforcement ------------------------------------- #
        paid = getattr(handler, "_larz_paid", None)
        if paid and self.money:
            gate = self.money.enforce_paid(req, paid, route)
            if gate is not None:
                return self._coerce(gate)

        metered = getattr(handler, "_larz_metered", None)
        if metered and self.money:
            gate = self.money.enforce_metered(req, metered, route)
            if gate is not None:
                return self._coerce(gate)

        try:
            return self._coerce(handler(req))
        except Exception as exc:  # noqa
            import traceback
            traceback.print_exc()
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
        msgs = {404: "Not Found", 405: "Method Not Allowed",
                500: "Internal Server Error"}
        return Response("%d %s" % (code, msgs.get(code, "Error")), status=code)

    # -- WSGI --------------------------------------------------------------- #
    def __call__(self, environ, start_response):
        req = Request(environ)
        req.session = self.sessions.load(req)
        had_cookie = self.sessions.cookie in req.cookies
        if "sid" not in req.session:
            req.session["sid"] = uuid.uuid4().hex
        original_session = dict(req.session)

        resp = self.dispatch(req)

        for hook in self._after:
            hook(req, resp)

        # persist the session if it changed OR the visitor had no cookie yet
        # (a first-time visitor's freshly-minted sid must be sent back, or the
        #  subject would change on every request and entitlements wouldn't stick)
        if not had_cookie or req.session != original_session:
            resp.set_cookie(self.sessions.cookie, self.sessions.dump(req.session))

        headers = []
        set_cookies = resp.headers.pop("_set_cookie", [])
        for k, v in resp.headers.items():
            headers.append((k, v))
        for c in set_cookies:
            headers.append(("Set-Cookie", c))

        start_response(_status_line(resp.status), headers)
        return [resp.body]

    # -- dev server --------------------------------------------------------- #
    def run(self, host="127.0.0.1", port=8000):
        # let plugins register their built-in routes
        if self.seo:
            self.seo.install_routes()
        print("  Larz  money-native  ->  http://%s:%d  (%d routes)"
              % (host, port, len(self.routes)))
        srv = make_server(host, port, self)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n  bye.")
