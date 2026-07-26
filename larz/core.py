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
           "get_flashed_messages"]


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

    @classmethod
    def redirect(cls, location, status=302):
        return cls("", status=status, headers={"Location": location})

    @classmethod
    def json(cls, data, status=200):
        return cls(json.dumps(data), status=status, content_type="application/json")

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

    def before(self, fn):
        self._before.append(fn); return fn

    def after(self, fn):
        self._after.append(fn); return fn

    def errorhandler(self, code):
        def deco(fn):
            self._error_handlers[code] = fn; return fn
        return deco

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

        # per-route guards (auth, API keys, validation, …) attached by decorators
        for guard in getattr(handler, "_larz_guards", ()):
            out = guard(req)
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

        plan = getattr(handler, "_larz_plan", None)
        if plan and self.money:
            gate = self.money.enforce_plan(req, plan, route)
            if gate is not None:
                return self._coerce(gate)

        try:
            return self._coerce(handler(req))
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
