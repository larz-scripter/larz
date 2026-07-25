"""
larz.admin — an auto-generated admin panel (CRUD) over your models (zero-dep).

    import larz.admin as admin
    admin.enable(app, [User, Post], token="secret")   # /admin?token=secret

Generates list / create / edit / delete views from each model's fields. Guard it
with a shared token (shown here) or wire `guard=app.require_role("admin")`.
"""

from larz import Response
from .models import (Field, IntField, FloatField, BoolField, DateTimeField,
                     DateField, JSONField, DecimalField, ForeignKey)
import json as _json

__all__ = ["enable"]

_CSS = ("<style>body{font:15px system-ui;max-width:820px;margin:1.5rem auto;padding:0 1rem;color:#111}"
        "a{color:#06c;text-decoration:none}table{width:100%;border-collapse:collapse}"
        "td,th{text-align:left;padding:7px 10px;border-bottom:1px solid #eee;font-size:14px}"
        "input,textarea,select{width:100%;padding:8px;margin:4px 0 10px;border:1px solid #ccc;border-radius:6px}"
        "label{font-size:13px;color:#555;font-weight:600}.btn{background:#06c;color:#fff;border:0;"
        "padding:9px 16px;border-radius:7px;cursor:pointer;font-size:14px}.btn.del{background:#c33}"
        "h1{margin:.3em 0}.bar{display:flex;gap:12px;align-items:center;margin:10px 0}</style>")


def _field_input(f, value):
    name = f.name
    v = "" if value is None else value
    if isinstance(f, BoolField):
        ck = "checked" if value else ""
        return "<label>%s</label><input type=checkbox name=%s %s>" % (name, name, ck)
    if isinstance(f, (JSONField,)):
        return "<label>%s (JSON)</label><textarea name=%s rows=3>%s</textarea>" % (
            name, name, _json.dumps(value) if value is not None else "")
    if isinstance(f, ForeignKey):
        return "<label>%s (id)</label><input name=%s value='%s'>" % (name, name + "_id", v)
    if isinstance(f, (IntField, FloatField, DecimalField)):
        return "<label>%s</label><input name=%s value='%s'>" % (name, name, v)
    return "<label>%s</label><input name=%s value='%s'>" % (name, name, v)


def _coerce(f, raw):
    if isinstance(f, BoolField):
        return raw in ("on", "true", "1", True)
    if raw == "" or raw is None:
        return None
    if isinstance(f, IntField):
        return int(raw)
    if isinstance(f, FloatField):
        return float(raw)
    if isinstance(f, DecimalField):
        import decimal; return decimal.Decimal(raw)
    if isinstance(f, JSONField):
        try: return _json.loads(raw)
        except Exception: return None
    return raw


def enable(app, models, token=None, guard=None):
    reg = {m.__name__.lower(): m for m in models}
    for m in models:
        m.create_table()

    def _auth(req):
        if guard is None and token and req.query.get("token") != token:
            return Response("forbidden — add ?token=", status=403)
        return None

    def page(body):
        return Response(_CSS + body)

    def _apply_guard(fn):
        if guard:
            return guard(fn)
        def g(req):
            return _auth(req)
        fn._larz_guards = [g] + list(getattr(fn, "_larz_guards", []))
        return fn

    tok = ("?token=" + token) if token else ""

    @_apply_guard
    @app.get("/admin")
    def index(req):
        items = "".join("<li><a href='/admin/%s%s'>%s</a> (%d)</li>"
                        % (n, tok, n, m.count()) for n, m in reg.items())
        return page("<h1>Admin</h1><ul>%s</ul>" % items)

    @_apply_guard
    @app.get("/admin/<model>")
    def listing(req):
        m = reg.get(req.params["model"])
        if not m: return page("<h1>Unknown model</h1>"), 404
        cols = ["id"] + list(m._fields.keys())
        head = "".join("<th>%s</th>" % c for c in cols) + "<th></th>"
        rows = ""
        for obj in m.all(order="-id", limit=100):
            d = obj.to_dict()
            tds = "".join("<td>%s</td>" % str(d.get(c, d.get(c + "_id", "")))[:40] for c in cols)
            rows += "<tr>%s<td><a href='/admin/%s/%d%s'>edit</a></td></tr>" % (
                tds, req.params["model"], obj.id, tok)
        return page("<div class=bar><h1>%s</h1><a class=btn href='/admin/%s/new%s'>+ New</a> "
                    "<a href='/admin%s'>← all</a></div>"
                    "<table><tr>%s</tr>%s</table>" % (
                        m.__name__, req.params["model"], tok, tok, head, rows))

    @_apply_guard
    @app.get("/admin/<model>/new")
    def new_form(req):
        m = reg.get(req.params["model"])
        inputs = "".join(_field_input(f, f.default) for f in m._fields.values())
        return page("<h1>New %s</h1><form method=post action='/admin/%s/new%s'>%s"
                    "<button class=btn>Create</button></form>" % (
                        m.__name__, req.params["model"], tok, inputs))

    @_apply_guard
    @app.post("/admin/<model>/new")
    def create(req):
        m = reg.get(req.params["model"])
        kw = {}
        for name, f in m._fields.items():
            if isinstance(f, ForeignKey):
                raw = req.form.get(name + "_id")
                kw[name + "_id"] = int(raw) if raw else None
            else:
                kw[name] = _coerce(f, req.form.get(name))
        m(**kw).save()
        return Response.redirect("/admin/%s%s" % (req.params["model"], tok))

    @_apply_guard
    @app.get("/admin/<model>/<id>")
    def edit_form(req):
        m = reg.get(req.params["model"])
        obj = m.get(int(req.params["id"]))
        if not obj: return page("<h1>Not found</h1>"), 404
        inputs = ""
        for name, f in m._fields.items():
            val = getattr(obj, name + "_id" if isinstance(f, ForeignKey) else name, None)
            inputs += _field_input(f, val)
        return page("<h1>Edit %s #%d</h1>"
                    "<form method=post action='/admin/%s/%d%s'>%s<button class=btn>Save</button></form>"
                    "<form method=post action='/admin/%s/%d/delete%s'>"
                    "<button class='btn del'>Delete</button></form>" % (
                        m.__name__, obj.id, req.params["model"], obj.id, tok, inputs,
                        req.params["model"], obj.id, tok))

    @_apply_guard
    @app.post("/admin/<model>/<id>")
    def update(req):
        m = reg.get(req.params["model"])
        obj = m.get(int(req.params["id"]))
        for name, f in m._fields.items():
            if isinstance(f, ForeignKey):
                raw = req.form.get(name + "_id")
                setattr(obj, name + "_id", int(raw) if raw else None)
            else:
                setattr(obj, name, _coerce(f, req.form.get(name)))
        obj.save()
        return Response.redirect("/admin/%s%s" % (req.params["model"], tok))

    @_apply_guard
    @app.post("/admin/<model>/<id>/delete")
    def delete(req):
        m = reg.get(req.params["model"])
        obj = m.get(int(req.params["id"]))
        if obj: obj.delete()
        return Response.redirect("/admin/%s%s" % (req.params["model"], tok))

    return app
