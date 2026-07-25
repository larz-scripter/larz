"""
Feature tests for Larz v0.2 — money (subscriptions/trials/coupons/plans/packs/
dashboard), templating, models, and security middleware. Plain `python3`.
"""
import sys, os, io, json, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response, Blueprint
from larz.templating import Template
from larz.models import Model, Field, connect
import larz.money as money
import larz.security as security

PASS = [0]; FAIL = [0]
def check(name, cond):
    if cond: PASS[0] += 1; print("  ok   " + name)
    else:    FAIL[0] += 1; print("  FAIL " + name)


class Client:
    def __init__(self, app):
        self.app = app; self.cookie = None
    def request(self, method, path, body=b"", headers=None, follow=True, _d=0):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        q = ""
        if "?" in path: path, q = path.split("?", 1)
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": q,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
               "REMOTE_ADDR": "10.0.0.1", "HTTP_USER_AGENT": "test-agent"}
        for k, v in (headers or {}).items():
            env["HTTP_" + k.upper().replace("-", "_")] = v
        if self.cookie: env["HTTP_COOKIE"] = self.cookie
        sh = {}
        def sr(status, hs): sh["c"] = int(status.split()[0]); sh["h"] = hs
        raw = b"".join(self.app(env, sr))
        for k, v in sh["h"]:
            if k == "Set-Cookie": self.cookie = v.split(";")[0]
        loc = dict(sh["h"]).get("Location")
        if follow and sh["c"] in (301, 302, 303) and loc and _d < 6:
            p = loc.split("://", 1)[-1]; p = p[p.find("/"):] if "/" in p else "/"
            return self.request("GET", p, follow=True, _d=_d + 1)
        return sh["c"], raw


# ---- templating ---------------------------------------------------------- #
def test_templating():
    check("tpl escape", Template("{{x}}").render(x="<b>") == "&lt;b&gt;")
    check("tpl safe", Template("{{x|safe}}").render(x="<b>") == "<b>")
    check("tpl for+if",
          Template("{% for i in xs %}{% if i%2 %}{{i}}{% endif %}{% endfor %}"
                   ).render(xs=[1, 2, 3, 4, 5]) == "135")
    check("tpl set", Template("{% set y = a*2 %}{{y}}").render(a=21) == "42")


# ---- models -------------------------------------------------------------- #
def test_models():
    connect(":memory:")
    class Item(Model):
        name = Field(str); qty = Field(int, default=1); active = Field(bool, default=True)
    Item.drop_table(); Item.create_table()
    i = Item(name="widget", qty=3).save()
    check("model insert id", i.id == 1)
    check("model get", Item.get(1).name == "widget")
    i.update(qty=9)
    check("model update", Item.get(1).qty == 9)
    Item(name="gadget", qty=0, active=False).save()
    check("model where", len(Item.where(active=True).all()) == 1)
    check("model count", Item.count() == 2)
    check("model order", [x.name for x in Item.all(order="-qty")] == ["widget", "gadget"])


# ---- blueprints ---------------------------------------------------------- #
def test_blueprints():
    app = Larz(secret="t")
    bp = Blueprint("api", prefix="/api/v1")
    @bp.get("/ping")
    def ping(req): return "pong"
    app.register(bp)
    c = Client(app)
    check("blueprint prefixed route", c.request("GET", "/api/v1/ping")[1] == b"pong")
    check("blueprint bare path 404", c.request("GET", "/ping")[0] == 404)


# ---- subscriptions + trials ---------------------------------------------- #
def test_subscription_and_trial(db):
    app = Larz(secret="t")
    money.enable(app, db=db, base_url="http://x")
    @app.paid("$9/mo", trial_days=7)
    @app.get("/pro")
    def pro(req): return "PRO"
    c = Client(app)
    # first hit: free trial granted, served WITHOUT checkout redirect
    code, body = c.request("GET", "/pro", follow=False)
    check("trial serves immediately (200)", code == 200 and body == b"PRO")
    subj = json.loads(c.request("GET", "/larz/credits?format=json")[1])["subject"]
    exp = app.money.store.expires_at(subj, "pro")
    check("trial sets ~7d expiry", exp and 6.5 * 86400 < (exp - time.time()) < 7.5 * 86400)
    # simulate trial expiry -> must go through checkout, which grants 30d sub
    app.money.store.grant(subj, "pro", days=-1)   # force-expire
    check("expired trial not entitled", not app.money.store.is_entitled(subj, "pro"))
    code, body = c.request("GET", "/pro", follow=True)   # follows mock checkout
    check("post-checkout serves (200)", code == 200 and body == b"PRO")
    exp2 = app.money.store.expires_at(subj, "pro")
    check("subscription ~30d", exp2 and 29 * 86400 < (exp2 - time.time()) < 31 * 86400)


# ---- plans --------------------------------------------------------------- #
def test_plans(db):
    app = Larz(secret="t")
    m = money.enable(app, db=db, base_url="http://x")
    m.plan("pro", "$19/mo", features=["A", "B"])
    @app.plan("pro")
    @app.get("/dash")
    def dash(req): return "DASH"
    c = Client(app)
    code, body = c.request("GET", "/dash", follow=True)   # not a plan member -> checkout -> granted
    check("plan checkout then serve", code == 200 and body == b"DASH")
    code2, _ = c.request("GET", "/dash", follow=False)
    check("plan membership sticks", code2 == 200)
    check("pricing page lists plan", b"Pro" in c.request("GET", "/larz/pricing")[1])


# ---- coupons ------------------------------------------------------------- #
def test_coupons(db):
    store = money.EntitlementStore(db)
    store.add_coupon("HALF", percent_off=50)
    store.add_coupon("FIVEOFF", amount_off_cents=500)
    check("coupon percent", store.apply_coupon("HALF", 1000) == (500, True))
    check("coupon amount", store.apply_coupon("FIVEOFF", 1000) == (500, True))
    check("coupon unknown", store.apply_coupon("NOPE", 1000) == (1000, False))
    store.add_coupon("ONCE", percent_off=10, max_redemptions=1)
    check("coupon before redemption", store.apply_coupon("ONCE", 1000)[1] is True)
    store.redeem_coupon("ONCE")
    check("coupon exhausted after max", store.apply_coupon("ONCE", 1000)[1] is False)


# ---- credit packs -------------------------------------------------------- #
def test_credit_packs(db):
    app = Larz(secret="t")
    m = money.enable(app, db=db, base_url="http://x")
    m.credit_pack("starter", price="$5", credit="$6", label="Starter pack")
    c = Client(app)
    subj = json.loads(c.request("GET", "/larz/credits?format=json")[1])["subject"]
    check("balance starts 0", app.money.store.balance(subj) == 0)
    c.request("GET", "/larz/credits/buy/starter", follow=True)   # checkout -> webhook grants credit
    check("credit pack adds 600c", app.money.store.balance(subj) == 600)


# ---- dashboard ----------------------------------------------------------- #
def test_dashboard(db):
    app = Larz(secret="t")
    m = money.enable(app, db=db, base_url="http://x", admin_token="sekret")
    app.money.store.record_payment("p1", "sub1", "pro", 900, "mock")
    app.money.store.record_payment("p2", "sub2", "pro", 1900, "mock")
    c = Client(app)
    check("admin blocks without token", c.request("GET", "/larz/admin")[0] == 403)
    body = c.request("GET", "/larz/admin?token=sekret")[1]
    check("admin shows revenue $28.00", b"$28.00" in body)


# ---- security ------------------------------------------------------------ #
def test_rate_limit():
    app = Larz(secret="t")
    app.use(security.RateLimiter(limit=3, window=60).hook())
    @app.get("/x")
    def x(req): return "ok"
    c = Client(app)
    codes = [c.request("GET", "/x", follow=False)[0] for _ in range(5)]
    check("rate limit allows 3 then 429", codes == [200, 200, 200, 429, 429])


def test_bot_filter():
    app = Larz(secret="t")
    app.use(security.bot_filter())
    @app.get("/y")
    def y(req): return "ok"
    c = Client(app)
    check("human allowed", c.request("GET", "/y", headers={"User-Agent": "Mozilla/5.0"})[0] == 200)
    check("bot blocked 403", c.request("GET", "/y", headers={"User-Agent": "Googlebot/2.1"})[0] == 403)


# ---- imperative paywall (dynamic per-item prices) ------------------------ #
def test_require_dynamic(db):
    app = Larz(secret="t")
    m = money.enable(app, db=db, base_url="http://x")
    catalog = {"guide-a": 17.0, "bundle-b": 49.0}
    @app.get("/buy/<slug>")
    def buy(req):
        price = catalog[req.params["slug"]]
        gate = m.require(req, sku=req.params["slug"], cents=int(price * 100),
                         success_path=req.path)
        if gate:
            return gate
        return "DELIVER:" + req.params["slug"]
    c = Client(app)
    # different products, different prices, no per-route decoration
    code, body = c.request("GET", "/buy/guide-a", follow=True)
    check("dynamic paywall delivers after checkout", code == 200 and body == b"DELIVER:guide-a")
    subj = json.loads(c.request("GET", "/larz/credits?format=json")[1])["subject"]
    check("entitled only to purchased sku", m.store.is_entitled(subj, "guide-a")
          and not m.store.is_entitled(subj, "bundle-b"))
    check("recorded payment cents = product price", any(
        p["cents"] == 1700 for p in m.store.stats()["recent"]))


# ---- providers (GemVault webhook signature + uid packing) ---------------- #
def test_gemvault_provider():
    import hmac, hashlib
    from larz.providers import GemVaultProvider
    from larz.core import Request
    gv = GemVaultProvider(app="eh_shop", api_base="http://gv", token="tok", secret="s3cret")
    check("gemvault packs subject|sku", gv._pack("subjA", "plan:pro") == "subjA|plan:pro")
    body = json.dumps({"uid": "subjA|plan:pro", "usd_amount": 9.0,
                       "tx_hash": "0xabc"}).encode()
    good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    def make_req(sig):
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/larz/webhook/gemvault",
               "QUERY_STRING": "", "CONTENT_LENGTH": str(len(body)),
               "wsgi.input": io.BytesIO(body), "HTTP_X_GV_SIGNATURE": sig}
        return Request(env)

    res = gv.parse_webhook(make_req(good))
    check("gemvault valid webhook -> subject", res and res["subject"] == "subjA")
    check("gemvault valid webhook -> sku", res["sku"] == "plan:pro")
    check("gemvault valid webhook -> cents", res["cents"] == 900)
    check("gemvault valid webhook -> payment_id", res["payment_id"] == "0xabc")
    check("gemvault bad signature rejected", gv.parse_webhook(make_req("deadbeef")) is None)


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    def db(n): return os.path.join(d, n + ".db")
    test_templating()
    test_models()
    test_blueprints()
    test_subscription_and_trial(db("sub"))
    test_plans(db("plan"))
    test_coupons(db("coup"))
    test_credit_packs(db("pack"))
    test_dashboard(db("dash"))
    test_rate_limit()
    test_bot_filter()
    test_gemvault_provider()
    test_require_dynamic(db("require"))
    print("\n  %d passed, %d failed" % (PASS[0], FAIL[0]))
    sys.exit(1 if FAIL[0] else 0)
