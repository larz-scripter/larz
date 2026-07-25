"""
larz.api — tooling for building JSON APIs (zero-dep).

    import larz.api as api
    api.enable(app)                        # adds @app.validate + doc support

    @app.validate({"text": {"type": str, "required": True, "maxlen": 500},
                   "n": {"type": int, "min": 1, "max": 10}})
    @app.post("/api/summarize")
    def summ(req):
        return {"got": req.data["text"], "n": req.data.get("n", 3)}

    app.enable_docs(title="My API", version="1.0")   # /openapi.json + /docs

Also: `api.paginate(items, req)` and a `Webhooks` framework (sign + deliver with
retry; verify inbound).
"""

import json
import time
import hmac
import hashlib
import threading
import urllib.request

from larz import Response

__all__ = ["enable", "paginate", "Webhooks"]

_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}


def _validate(schema, data):
    errors = {}
    data = data or {}
    for field, spec in schema.items():
        if not isinstance(spec, dict):
            spec = {"type": spec}
        present = field in data and data[field] is not None
        if spec.get("required") and not present:
            errors[field] = "required"
            continue
        if not present:
            continue
        val = data[field]
        t = spec.get("type")
        if t and not isinstance(val, t) and not (t is float and isinstance(val, int)):
            errors[field] = "must be %s" % _TYPES.get(t, getattr(t, "__name__", t))
            continue
        if "min" in spec and val < spec["min"]:
            errors[field] = "min %s" % spec["min"]
        if "max" in spec and val > spec["max"]:
            errors[field] = "max %s" % spec["max"]
        if "minlen" in spec and len(val) < spec["minlen"]:
            errors[field] = "min length %s" % spec["minlen"]
        if "maxlen" in spec and len(val) > spec["maxlen"]:
            errors[field] = "max length %s" % spec["maxlen"]
        if "choices" in spec and val not in spec["choices"]:
            errors[field] = "must be one of %s" % (spec["choices"],)
    return errors


def paginate(items, req, per_page=20):
    """Paginate a list (or anything len()/slice-able). Returns a JSON-ready dict."""
    try:
        page = max(1, int(req.query.get("page", 1)))
    except ValueError:
        page = 1
    per_page = int(req.query.get("per_page", per_page))
    total = len(items)
    start = (page - 1) * per_page
    window = items[start:start + per_page]
    return {"items": [i.to_dict() if hasattr(i, "to_dict") else i for i in window],
            "page": page, "per_page": per_page, "total": total,
            "pages": (total + per_page - 1) // per_page}


# --------------------------------------------------------------------------- #
#  Webhooks
# --------------------------------------------------------------------------- #
class Webhooks:
    def __init__(self, secret, retries=3):
        self.secret = secret
        self.retries = retries

    def sign(self, body_bytes):
        return hmac.new(self.secret.encode(), body_bytes, hashlib.sha256).hexdigest()

    def deliver(self, url, event, data, background=True):
        payload = json.dumps({"event": event, "data": data}).encode()
        sig = self.sign(payload)

        def _send():
            for attempt in range(self.retries):
                try:
                    r = urllib.request.Request(url, data=payload, headers={
                        "Content-Type": "application/json",
                        "X-Larz-Event": event, "X-Larz-Signature": sig})
                    urllib.request.urlopen(r, timeout=10)
                    return True
                except Exception:
                    time.sleep(2 ** attempt)
            return False
        if background:
            threading.Thread(target=_send, daemon=True).start()
            return None
        return _send()

    def verify(self, req):
        sig = req.header("X-Larz-Signature") or ""
        expected = self.sign(req.body)
        return hmac.compare_digest(sig, expected)


# --------------------------------------------------------------------------- #
#  OpenAPI generation
# --------------------------------------------------------------------------- #
def _openapi(app, title, version):
    paths = {}
    for r in app.routes:
        if r.pattern.startswith("/larz/") or r.pattern in ("/openapi.json", "/docs"):
            continue
        # convert /u/<id:int> -> /u/{id}
        import re
        p = re.sub(r"<([^:>]+)(:[^>]+)?>", r"{\1}", r.pattern)
        h = r.handler
        for m in r.methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            op = {"summary": (h.__doc__ or getattr(h, "__name__", "")).strip().split("\n")[0],
                  "responses": {"200": {"description": "OK"}}}
            tags = []
            if getattr(h, "_larz_paid", None):
                tags.append("paid")
            if getattr(h, "_larz_metered", None):
                tags.append("metered")
            for g in getattr(h, "_larz_guards", []):
                pass
            if tags:
                op["tags"] = tags
            schema = getattr(h, "_larz_schema", None)
            if schema and m in ("POST", "PUT", "PATCH"):
                props, required = {}, []
                for f, spec in schema.items():
                    if not isinstance(spec, dict):
                        spec = {"type": spec}
                    props[f] = {"type": _TYPES.get(spec.get("type"), "string")}
                    if spec.get("required"):
                        required.append(f)
                body = {"type": "object", "properties": props}
                if required:
                    body["required"] = required
                op["requestBody"] = {"content": {"application/json": {"schema": body}}}
            paths.setdefault(p, {})[m.lower()] = op
    return {"openapi": "3.0.0", "info": {"title": title, "version": version}, "paths": paths}


_DOCS_HTML = """<!doctype html><meta charset=utf-8><title>%(title)s — API</title>
<style>body{font:15px system-ui;max-width:820px;margin:2rem auto;padding:0 1rem;color:#111}
h1{margin:.2em 0}.op{border:1px solid #e3e3e3;border-radius:10px;margin:10px 0;padding:12px 16px}
.m{display:inline-block;font-weight:700;font-size:12px;padding:2px 8px;border-radius:6px;color:#fff}
.get{background:#0a7}.post{background:#06c}.put{background:#c60}.delete{background:#c33}
code{background:#f4f4f4;padding:1px 6px;border-radius:5px}.tag{background:#eef;color:#449;font-size:11px;
padding:1px 7px;border-radius:999px;margin-left:6px}</style>
<h1>%(title)s <small style=color:#888>v%(version)s</small></h1>
<p><a href="/openapi.json">openapi.json</a></p><div id=ops></div>
<script>
fetch('/openapi.json').then(r=>r.json()).then(spec=>{
 let h='';
 for(const [path,methods] of Object.entries(spec.paths)){
  for(const [m,op] of Object.entries(methods)){
   const tags=(op.tags||[]).map(t=>`<span class=tag>${t}</span>`).join('');
   h+=`<div class=op><span class="m ${m}">${m.toUpperCase()}</span> <code>${path}</code>${tags}
   <div style=color:#555;margin-top:6px>${op.summary||''}</div></div>`;
  }
 }
 document.getElementById('ops').innerHTML=h||'<p>No endpoints.</p>';
});
</script>"""


def enable(app):
    """Add @app.validate and app.enable_docs to the app."""
    def validate(schema):
        def deco(fn):
            fn._larz_schema = schema
            def guard(req):
                data = req.json() if req.method in ("POST", "PUT", "PATCH") else dict(req.query)
                errors = _validate(schema, data)
                if errors:
                    return Response.json({"error": "validation_failed", "fields": errors},
                                         status=400)
                req.data = data or {}
                return None
            fn._larz_guards = list(getattr(fn, "_larz_guards", [])) + [guard]
            return fn
        return deco
    app.validate = validate

    def enable_docs(title="Larz API", version="1.0"):
        @app.get("/openapi.json", sitemap=False)
        def _spec(req):
            return Response.json(_openapi(app, title, version))

        @app.get("/docs", sitemap=False)
        def _docs(req):
            return Response(_DOCS_HTML % {"title": title, "version": version})
        return app
    app.enable_docs = enable_docs
    return app
