"""
larz.testing — a first-class in-process test client. Zero dependencies, no
sockets, no server. Drives your app through the WSGI callable directly.

    from larz.testing import Client
    c = Client(app)
    r = c.get("/")
    assert r.status == 200 and "Welcome" in r.text
    r = c.post("/signup", form={"email": "a@b.com", "password": "pw123456"})
    assert r.redirect == "/app"          # cookies persist across calls
    data = c.get("/api/me").json

`Client` keeps a cookie jar, so login state carries between requests just like a
browser. Redirects are returned (not followed) by default; pass follow=True.
"""

import io
import json as _json
from urllib.parse import urlencode, urlsplit

__all__ = ["Client", "TestResponse"]


class TestResponse:
    def __init__(self, status, headers, body):
        self.status = status                       # int, e.g. 200
        self.headers = headers                     # list of (k, v)
        self.body = body                           # bytes

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")

    @property
    def json(self):
        try:
            return _json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    @property
    def redirect(self):
        for k, v in self.headers:
            if k.lower() == "location":
                return v
        return None

    def header(self, name):
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None


class Client:
    def __init__(self, app, base="http://testserver"):
        self.app = app
        self.base = base
        self.cookies = {}

    # -- verbs -------------------------------------------------------------- #
    def get(self, path, follow=False, headers=None, **kw):
        return self.request("GET", path, follow=follow, headers=headers, **kw)

    def post(self, path, form=None, json=None, body=None, follow=False,
             headers=None, content_type=None):
        return self.request("POST", path, form=form, json=json, body=body,
                            follow=follow, headers=headers, content_type=content_type)

    # -- core --------------------------------------------------------------- #
    def request(self, method, path, form=None, json=None, body=None,
                follow=False, headers=None, content_type=None):
        parts = urlsplit(path)
        env_body = b""
        ctype = content_type
        if json is not None:
            env_body = _json.dumps(json).encode()
            ctype = ctype or "application/json"
        elif form is not None:
            env_body = urlencode(form, doseq=True).encode()
            ctype = ctype or "application/x-www-form-urlencoded"
        elif body is not None:
            env_body = body if isinstance(body, bytes) else str(body).encode()

        environ = {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": parts.path or "/",
            "QUERY_STRING": parts.query,
            "SERVER_NAME": "testserver", "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.input": io.BytesIO(env_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.url_scheme": "http",
            "CONTENT_LENGTH": str(len(env_body)),
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_USER_AGENT": "larz-testclient",
        }
        if ctype:
            environ["CONTENT_TYPE"] = ctype
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        for k, v in (headers or {}).items():
            hk = k.upper().replace("-", "_")
            if hk not in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                hk = "HTTP_" + hk
            environ[hk] = v

        captured = {}
        def start_response(status, resp_headers, exc_info=None):
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = resp_headers

        chunks = self.app(environ, start_response)
        body_out = b"".join(chunks)
        headers_out = captured["headers"]

        # persist Set-Cookie into the jar (name=value only)
        for k, v in headers_out:
            if k.lower() == "set-cookie":
                nv = v.split(";", 1)[0]
                if "=" in nv:
                    name, val = nv.split("=", 1)
                    self.cookies[name.strip()] = val.strip()

        resp = TestResponse(captured["status"], headers_out, body_out)
        if follow and resp.redirect and 300 <= resp.status < 400:
            return self.get(resp.redirect, follow=True)
        return resp

    # -- convenience -------------------------------------------------------- #
    def login(self, path="/login", email=None, password=None, **fields):
        data = dict(fields)
        if email is not None:
            data["email"] = email
        if password is not None:
            data["password"] = password
        return self.post(path, form=data)

    def reset(self):
        self.cookies = {}
        return self
