"""
Tests for Larz v1.0 expansion (data, auth, api, billing, ops). Plain python3.
Grows as each pack lands. Run: python3 tests/test_v1.py
"""
import sys, os, io, json, tempfile, datetime, decimal
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = [0]; FAIL = [0]
def check(name, cond):
    if cond: PASS[0] += 1; print("  ok   " + name)
    else:    FAIL[0] += 1; print("  FAIL " + name)


class Client:
    def __init__(self, app): self.app = app; self.cookie = None
    def request(self, method, path, body=b"", headers=None, follow=True, _d=0):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        q = ""
        if "?" in path: path, q = path.split("?", 1)
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": q,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
               "REMOTE_ADDR": "127.0.0.1", "HTTP_USER_AGENT": "test"}
        for k, v in (headers or {}).items():
            ek = k.upper().replace("-", "_")
            env[ek if ek in ("CONTENT_TYPE", "CONTENT_LENGTH") else "HTTP_" + ek] = v
        if self.cookie: env["HTTP_COOKIE"] = self.cookie
        sh = {}
        def sr(s, h): sh["c"] = int(s.split()[0]); sh["h"] = h
        raw = b"".join(self.app(env, sr))
        for k, v in sh["h"]:
            if k == "Set-Cookie": self.cookie = v.split(";")[0]
        loc = dict(sh["h"]).get("Location")
        if follow and sh["c"] in (301, 302, 303) and loc and _d < 6:
            p = loc.split("://", 1)[-1]; p = p[p.find("/"):] if "/" in p else "/"
            return self.request("GET", p, follow=True, _d=_d + 1)
        return sh["c"], raw


# ============================ DATA / ORM ============================ #
def test_orm():
    from larz.models import (Model, StrField, IntField, FloatField, TextField,
                             JSONField, DecimalField, DateTimeField, BoolField,
                             ForeignKey, connect, transaction)
    connect(":memory:")
    class User(Model):
        name = StrField(unique=True); age = IntField(default=0)
    class Post(Model):
        title = StrField(); views = IntField(default=0, index=True)
        tags = JSONField(default=None); price = DecimalField(default=None)
        created = DateTimeField(auto_now=True); pinned = BoolField(default=False)
        author = ForeignKey(User)
    User.drop_table(); Post.drop_table(); User.create_table(); Post.create_table()

    u = User.create(name="Ada", age=36)
    p = Post.create(title="Hello", views=150, tags=["x", "y"],
                    price=decimal.Decimal("9.99"), author=u)
    Post.create(title="Low", views=10, author=u)

    check("field types roundtrip (json/decimal)",
          Post.get(p.id).tags == ["x", "y"] and Post.get(p.id).price == decimal.Decimal("9.99"))
    check("auto_now datetime", isinstance(Post.get(p.id).created, datetime.datetime))
    check("foreign key access", Post.get(p.id).author.name == "Ada")
    check("query __gt", [x.title for x in Post.where(views__gt=100).all()] == ["Hello"])
    check("query __like", [x.title for x in Post.where(title__like="H%").all()] == ["Hello"])
    check("query __in + order", [x.views for x in Post.where(views__in=[10, 150]).order("-views").all()] == [150, 10])
    check("pagination", len(Post.where().page(1, per_page=1).all()) == 1)
    check("count/exists", Post.count() == 2 and Post.where(views__gt=100).exists())
    check("fk filter", Post.count(author=u) == 2)
    # hooks
    calls = []
    class Widget(Model):
        name = StrField()
        def before_save(self): calls.append("before")
        def after_save(self): calls.append("after")
    Widget.create_table(); Widget.create(name="w")
    check("model hooks fire", calls == ["before", "after"])
    # transaction rollback
    class Acct(Model):
        bal = IntField(default=0)
    Acct.create_table(); a = Acct.create(bal=100)
    try:
        with transaction():
            a.update(bal=50); raise RuntimeError("boom")
    except RuntimeError:
        pass
    check("transaction rollback", Acct.get(a.id).bal == 100)


# ============================ AUTH ============================ #
def _form(d): return "&".join("%s=%s" % kv for kv in d.items()).encode()
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}

def test_auth():
    from larz import Larz, Response
    from larz.models import connect
    import larz.auth as auth
    connect(":memory:")
    app = Larz(secret="t"); auth.enable(app)

    @app.post("/register")
    def reg(req):
        return {"id": app.auth.register(req.form["email"], req.form["password"]).id}
    @app.post("/login")
    def login(req):
        u = app.auth.login(req, req.form["email"], req.form["password"])
        return {"ok": True} if u else Response.json({"ok": False}, status=401)
    @app.login_required
    @app.get("/me")
    def me(req): return {"email": req.user.email}
    @app.require_role("admin")
    @app.get("/admin")
    def admin(req): return "ok"
    @app.api_key_required
    @app.get("/api/x")
    def apix(req): return {"plan": req.api_key.plan}

    c = Client(app)
    check("register", c.request("POST", "/register", body=_form({"email": "a@b.co", "password": "pw123456"}), headers=_FORM)[0] == 200)
    check("protected route redirects when anon", c.request("GET", "/me", follow=False)[0] == 302)
    check("login", c.request("POST", "/login", body=_form({"email": "a@b.co", "password": "pw123456"}), headers=_FORM)[0] == 200)
    check("session auth works", c.request("GET", "/me", follow=False)[0] == 200)
    check("wrong password rejected", c.request("POST", "/login", body=_form({"email": "a@b.co", "password": "bad"}), headers=_FORM)[0] == 401)
    check("role gate 403", c.request("GET", "/admin", follow=False)[0] == 403)
    u = app.auth.User.where(email="a@b.co").first()
    raw = app.auth.issue_api_key(u, plan="pro")
    check("api key missing -> 401", c.request("GET", "/api/x", follow=False)[0] == 401)
    check("api key valid", b'"plan": "pro"' in c.request("GET", "/api/x", headers={"Authorization": "Bearer " + raw}, follow=False)[1])
    check("password hashing", u.check_password("pw123456") and not u.check_password("x"))
    # api-key metering: billed to the key, not the session
    import larz.money as _money
    from larz.auth import ApiKey, _hash_key
    _money.enable(app, base_url="http://x", db=os.path.join(tempfile.mkdtemp(), "ak.db"))
    @app.api_key_required
    @app.metered("$0.02/call")
    @app.post("/api/meter")
    def meter(req): return {"ok": True}
    raw2 = app.auth.issue_api_key(plan="pro")     # userless key -> billed by key
    ak = ApiKey.where(key_hash=_hash_key(raw2)).first()
    app.money.store.add_credit("apikey:%s" % ak.id, 5)
    hdr = {"Authorization": "Bearer " + raw2}
    check("api-key metered 200 when funded", c.request("POST", "/api/meter", headers=hdr, follow=False)[0] == 200)
    check("api-key metered debits the key", app.money.store.balance("apikey:%s" % ak.id) == 3)
    tok = app.auth.make_reset_token(u)
    app.auth.reset_password(tok, "newpw999")
    check("password reset token", app.auth.User.get(u.id).check_password("newpw999"))
    check("bad token rejected", app.auth.reset_password("garbage", "x") is None)


# ============================ API ============================ #
def test_api():
    from larz import Larz
    import larz.api as api
    app = Larz(secret="t"); api.enable(app)
    @app.validate({"text": {"type": str, "required": True, "maxlen": 20},
                   "n": {"type": int, "min": 1, "max": 5}})
    @app.post("/echo")
    def echo(req): return {"got": req.data["text"]}
    @app.get("/list")
    def lst(req): return api.paginate(list(range(1, 101)), req, per_page=10)
    app.enable_docs(title="T", version="1")
    c = Client(app)
    J = {"Content-Type": "application/json"}
    check("validation passes", c.request("POST", "/echo", body={"text": "hi", "n": 3}, headers=J)[0] == 200)
    check("validation: required", c.request("POST", "/echo", body={"n": 3}, headers=J)[0] == 400)
    check("validation: maxlen", c.request("POST", "/echo", body={"text": "x" * 99}, headers=J)[0] == 400)
    check("validation: range", c.request("POST", "/echo", body={"text": "hi", "n": 99}, headers=J)[0] == 400)
    pg = json.loads(c.request("GET", "/list?page=2&per_page=10")[1])
    check("pagination", pg["items"][0] == 11 and pg["total"] == 100 and pg["pages"] == 10)
    spec = json.loads(c.request("GET", "/openapi.json")[1])
    check("openapi generated", "/echo" in spec["paths"] and "requestBody" in spec["paths"]["/echo"]["post"])
    check("docs page renders", c.request("GET", "/docs")[0] == 200)


# ============================ MONEY (deepened) ============================ #
def test_money_deep(db):
    from larz import Larz
    import larz.money as money
    app = Larz(secret="t"); m = money.enable(app, db=db, base_url="http://x")
    events = []
    m.on_grant(lambda s, k: events.append(("grant", k)))
    m.on_revoke(lambda s, k: events.append(("revoke", k)))
    @app.paid("$9/mo")
    @app.get("/pro")
    def pro(req): return "PRO"
    c = Client(app)
    c.request("GET", "/pro", follow=True)
    subj = json.loads(c.request("GET", "/larz/credits?format=json")[1])["subject"]
    check("on_grant event fired", ("grant", "pro") in events)
    check("invoice recorded", len(m.store.list_invoices(subj)) == 1)
    acct = c.request("GET", "/larz/account")[1].decode()
    check("customer portal renders", "Your account" in acct and "/larz/invoice/" in acct)
    c.request("POST", "/larz/account/cancel", body=b"sku=pro",
              headers={"Content-Type": "application/x-www-form-urlencoded"})
    check("cancel fires on_revoke", ("revoke", "pro") in events)
    inv = m.store.list_invoices(subj)[0]
    check("receipt page", b"Receipt" in c.request("GET", "/larz/invoice/" + inv["id"])[1])

def test_providers():
    from larz.providers import (PaddleProvider, LemonSqueezyProvider,
                                PaystackProvider, PayPalProvider)
    from larz.core import Request
    import hmac, hashlib
    # paystack webhook (sha512 over body)
    ps = PaystackProvider("sk_test")
    body = json.dumps({"event": "charge.success",
                       "data": {"metadata": {"subject": "u1", "sku": "pro"},
                                "amount": 900, "reference": "ref1"}}).encode()
    sig = hmac.new(b"sk_test", body, hashlib.sha512).hexdigest()
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/", "QUERY_STRING": "",
           "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
           "HTTP_X_PAYSTACK_SIGNATURE": sig}
    res = ps.parse_webhook(Request(env))
    check("paystack webhook parses", res and res["subject"] == "u1" and res["cents"] == 900)
    check("provider count", len([PaddleProvider, LemonSqueezyProvider,
                                 PaystackProvider, PayPalProvider]) == 4)


# ============================ OPS ============================ #
def test_ops():
    from larz import Larz
    import larz.ops as ops
    import time as _t
    app = Larz(secret="t"); ops.enable(app)
    calls = [0]
    @app.cache(ttl=30)
    @app.get("/x")
    def x(req): calls[0] += 1; return "v%d" % calls[0]
    results = []
    @app.job
    def bg(v): results.append(v)
    c = Client(app)
    r1 = c.request("GET", "/x")[1]; r2 = c.request("GET", "/x")[1]
    check("cache serves stored response", r1 == r2 and calls[0] == 1)
    bg.enqueue("a"); _t.sleep(0.4)
    check("background job runs", results == ["a"])
    check("healthz", json.loads(c.request("GET", "/healthz")[1])["status"] == "ok")
    check("metrics", "jobs_processed" in json.loads(c.request("GET", "/metrics")[1]))
    check("cron matcher", ops._cron_match("0 3 * * *", _t.struct_time((2026,7,25,3,0,0,4,206,0))))


# ============================ ADMIN ============================ #
def test_admin():
    from larz import Larz
    from larz.models import Model, StrField, IntField, connect
    import larz.admin as admin
    connect(":memory:")
    class Thing(Model):
        name = StrField(); qty = IntField(default=0)
    app = Larz(secret="t"); admin.enable(app, [Thing], token="tk")
    c = Client(app)
    F = {"Content-Type": "application/x-www-form-urlencoded"}
    enc = lambda d: "&".join("%s=%s" % kv for kv in d.items()).encode()
    check("admin gated by token", c.request("GET", "/admin", follow=False)[0] == 403)
    check("admin index with token", c.request("GET", "/admin?token=tk")[0] == 200)
    c.request("POST", "/admin/thing/new?token=tk", body=enc({"name": "W", "qty": "5"}), headers=F, follow=False)
    check("admin create", Thing.count() == 1)
    t = Thing.all()[0]
    c.request("POST", "/admin/thing/%d?token=tk" % t.id, body=enc({"name": "W2", "qty": "9"}), headers=F, follow=False)
    check("admin edit", Thing.get(t.id).name == "W2")
    c.request("POST", "/admin/thing/%d/delete?token=tk" % t.id, follow=False)
    check("admin delete", Thing.count() == 0)


# ============================ TEMPLATING (v1) ============================ #
def test_templating_v1():
    from larz.templating import Template, Environment
    check("filter upper", Template("{{ x|upper }}").render(x="hi") == "HI")
    check("filter currency", Template("{{ p|currency }}").render(p=9.5) == "$9.50")
    check("filter default", Template("{{ x|default('n') }}").render(x="") == "n")
    check("filter chain + safe", Template("{{ h|upper|safe }}").render(h="<b>") == "<B>")
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "base.html"), "w") as f:
        f.write("<h>{% block head %}dh{% endblock %}</h><b>{% block body %}bb{% endblock %}</b>")
    env = Environment(directory=d)
    out = env.from_string('{% extends "base.html" %}{% block body %}Hi {{ n }}{% endblock %}').render(n="Ada")
    check("template inheritance overrides block", "Hi Ada" in out)
    check("template inheritance keeps parent default", "dh" in out)


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    test_orm()
    test_auth()
    test_api()
    test_money_deep(os.path.join(d, "md.db"))
    test_providers()
    test_ops()
    test_admin()
    test_templating_v1()
    print("\n  %d passed, %d failed" % (PASS[0], FAIL[0]))
    sys.exit(1 if FAIL[0] else 0)
