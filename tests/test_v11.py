"""v1.1 feature tests — 2FA, test client, uploads, flash, htmx, payouts,
providers, oauth. Plain python3, in-process, no pytest. Run: python3 tests/test_v11.py"""
import os, sys, io, time, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response, get_flashed_messages
from larz.models import connect
from larz.testing import Client
import larz.auth as auth
import larz.twofa as twofa
import larz.money as money
import larz.oauth as oauth
from larz.storage import LocalStorage, safe_name
from larz.providers import (SquareProvider, RazorpayProvider, MollieProvider,
                            CoinbaseCommerceProvider)

P = [0]; F = [0]
def ck(name, cond):
    if cond: P[0] += 1; print("  ok   " + name)
    else: F[0] += 1; print("  FAIL " + name)


# ---- TOTP primitives ------------------------------------------------------ #
def test_totp():
    s = twofa.generate_secret()
    ck("secret is base32-ish", len(s) >= 16 and s.isalnum() and s.upper() == s)
    code = twofa.now_code(s)
    ck("now_code is 6 digits", len(code) == 6 and code.isdigit())
    ck("verify accepts current code", twofa.verify_code(s, code))
    ck("verify rejects wrong code", not twofa.verify_code(s, "000000") or code == "000000")
    ck("verify rejects garbage", not twofa.verify_code(s, "abc"))
    # drift: a code from 30s ago still valid with window=1
    old = twofa.now_code(s, at=time.time() - 30)
    ck("verify tolerates 1-step drift", twofa.verify_code(s, old, window=1, at=time.time()))
    ck("verify rejects 3-step drift", not twofa.verify_code(s, twofa.now_code(s, at=time.time()-120), at=time.time()))
    uri = twofa.provisioning_uri(s, "a@b.com", "MyApp")
    ck("provisioning uri", uri.startswith("otpauth://totp/MyApp:a%40b.com?") and "secret=" in uri)


def test_twofa_manager():
    connect(":memory:")
    auth.User.create_table()
    app = Larz(secret="x"); auth.enable(app); twofa.enable(app, issuer="MyApp")
    u = auth.User(email="u@x.com"); u.save()
    ck("2fa off initially", not app.twofa.is_enabled(u))
    uri, secret = app.twofa.begin(u)
    ck("begin returns uri+secret", uri.startswith("otpauth://") and len(secret) >= 16)
    ck("still off before activate", not app.twofa.is_enabled(u))
    ck("activate rejects bad code", app.twofa.activate(u, "000000") is None or twofa.now_code(secret) == "000000")
    codes = app.twofa.activate(u, twofa.now_code(secret))
    ck("activate returns backup codes", isinstance(codes, list) and len(codes) == 8)
    ck("2fa now enabled", app.twofa.is_enabled(u))
    ck("verify totp", app.twofa.verify(u, twofa.now_code(secret)))
    bc = codes[0]
    ck("verify backup code once", app.twofa.verify(u, bc))
    ck("backup code is single-use", not app.twofa.verify(u, bc))
    app.twofa.disable(u)
    ck("disable turns it off", not app.twofa.is_enabled(u))


# ---- test client ---------------------------------------------------------- #
def test_client():
    app = Larz(secret="x")
    @app.get("/")
    def home(req): return "hello"
    @app.get("/api")
    def api(req): return {"ok": True, "n": 1}
    @app.post("/echo")
    def echo(req): return {"got": req.form.get("v"), "seen": req.session.get("seen")}
    @app.get("/go")
    def go(req): req.session["seen"] = "yes"; return Response.redirect("/")
    c = Client(app)
    r = c.get("/")
    ck("client GET 200", r.status == 200 and r.text == "hello")
    ck("client JSON", c.get("/api").json == {"ok": True, "n": 1})
    ck("client redirect exposed", c.get("/go").redirect == "/")
    ck("client follows redirect", c.get("/go", follow=True).text == "hello")
    r2 = c.post("/echo", form={"v": "hi"})
    ck("client POST form", r2.json["got"] == "hi")
    ck("client keeps session cookie", r2.json["seen"] == "yes")


# ---- file uploads --------------------------------------------------------- #
def test_uploads():
    tmp = tempfile.mkdtemp()
    store = LocalStorage(os.path.join(tmp, "up"))
    app = Larz(secret="x")
    @app.post("/upload")
    def up(req):
        f = req.files.get("doc")
        if not f: return ("no file", 400)
        name = store.save(f)
        return {"name": name, "size": f.size, "field": req.form.get("caption"),
                "ctype": f.content_type}
    boundary = "----larztest"
    body = (
        "--%s\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\nhello world\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"doc\"; filename=\"a.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\nFILE-CONTENTS-123\r\n"
        "--%s--\r\n" % (boundary, boundary, boundary)).encode()
    c = Client(app)
    r = c.post("/upload", body=body, content_type="multipart/form-data; boundary=" + boundary)
    j = r.json
    ck("multipart file parsed", j and j["size"] == len("FILE-CONTENTS-123"))
    ck("multipart text field parsed", j["field"] == "hello world")
    ck("multipart content-type kept", j["ctype"] == "text/plain")
    ck("file saved to disk", store.exists(j["name"]))
    ck("saved bytes correct", store.open(j["name"]).read() == b"FILE-CONTENTS-123")
    ck("safe_name strips paths", safe_name("../../etc/passwd") == "etc_passwd" or "/" not in safe_name("../../etc/passwd"))


# ---- flash + htmx --------------------------------------------------------- #
def test_flash_and_htmx():
    app = Larz(secret="x")
    @app.get("/set")
    def s(req): req.flash("saved!", "success"); return Response.redirect("/show")
    @app.get("/show")
    def show(req):
        msgs = get_flashed_messages(req, with_categories=True)
        return {"msgs": msgs}
    @app.get("/hx")
    def hx(req): return Response("<div>ok</div>").hx_trigger("refresh")
    @app.get("/whoami")
    def who(req): return {"htmx": req.htmx}
    c = Client(app)
    c.get("/set")
    r = c.get("/show")
    ck("flash message delivered", r.json["msgs"] == [["success", "saved!"]])
    ck("flash is one-shot", c.get("/show").json["msgs"] == [])
    ck("hx_trigger header set", c.get("/hx").header("HX-Trigger") == "refresh")
    ck("req.htmx false normally", c.get("/whoami").json["htmx"] is False)
    ck("req.htmx true w/ header", c.get("/whoami", headers={"HX-Request": "true"}).json["htmx"] is True)


# ---- marketplace payouts -------------------------------------------------- #
def test_payouts():
    db = os.path.join(tempfile.mkdtemp(), "m.db")
    app = Larz(secret="x"); money.enable(app, base_url="http://x", db=db)
    ids = app.money.split([("seller:42", "$8.50"), ("platform", "$1.50")], sku="order-1")
    ck("split returns ids", len(ids) == 2)
    ck("seller owed 850c", app.money.owed("seller:42") == 850)
    ck("platform owed 150c", app.money.owed("platform") == 150)
    app.money.split([("seller:42", 1000)], sku="order-2")   # cents int form
    ck("owed accumulates", app.money.owed("seller:42") == 1850)
    rows = app.money.payouts("seller:42")
    ck("payouts listed", len(rows) == 2 and rows[0]["status"] == "pending")
    app.money.mark_paid(ids[0])
    ck("mark_paid reduces owed", app.money.owed("seller:42") == 1000)


# ---- new providers -------------------------------------------------------- #
class _FakeReq:
    def __init__(self, body=b"", headers=None, form=None, jsn=None):
        self.body = body; self._h = {k.lower(): v for k, v in (headers or {}).items()}
        self.form = form or {}; self._j = jsn
    def header(self, n, d=None): return self._h.get(n.lower(), d)
    def json(self): return self._j

def test_providers():
    sq = SquareProvider("tok", "loc", signature_key="k", notification_url="https://x/wh")
    ck("square rejects bad sig", sq.parse_webhook(_FakeReq(b"{}", {"x-square-hmacsha256-signature": "nope"})) is None)
    rz = RazorpayProvider("id", "sec", webhook_secret="whsec")
    ck("razorpay rejects bad sig", rz.parse_webhook(_FakeReq(b"{}", {"x-razorpay-signature": "nope"})) is None)
    cb = CoinbaseCommerceProvider("key", webhook_secret="whsec")
    ck("coinbase rejects bad sig", cb.parse_webhook(_FakeReq(b"{}", {"X-CC-Webhook-Signature": "nope"})) is None)
    # coinbase accepts a correctly-signed confirmed charge
    import hmac, hashlib
    payload = json.dumps({"event": {"type": "charge:confirmed", "data": {
        "code": "CH1", "metadata": {"subject": "user:1", "sku": "pro"},
        "pricing": {"local": {"amount": "9.00"}}}}}).encode()
    sig = hmac.new(b"whsec", payload, hashlib.sha256).hexdigest()
    out = cb.parse_webhook(_FakeReq(payload, {"X-CC-Webhook-Signature": sig}, jsn=json.loads(payload)))
    ck("coinbase accepts good sig", out == {"subject": "user:1", "sku": "pro", "cents": 900, "payment_id": "CH1"})
    ck("providers have names", (sq.name, rz.name, MollieProvider("k").name, cb.name) == ("square", "razorpay", "mollie", "coinbase"))


# ---- oauth ---------------------------------------------------------------- #
def test_oauth():
    app = Larz(secret="x")
    oauth.enable(app, providers={"google": {"client_id": "cid", "client_secret": "cs"}},
                 base_url="https://app.test", success_path="/home")
    c = Client(app)
    r = c.get("/larz/oauth/google/login")
    loc = r.redirect or ""
    ck("oauth login redirects to google", loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
    ck("oauth login carries client_id + redirect_uri", "client_id=cid" in loc and
       "redirect_uri=https%3A%2F%2Fapp.test%2Flarz%2Foauth%2Fgoogle%2Fcallback" in loc)
    ck("oauth login sets state", "state=" in loc)
    ck("oauth unknown provider 404", c.get("/larz/oauth/nope/login").status == 404)
    ck("oauth callback bad state 400", c.get("/larz/oauth/google/callback?code=x&state=wrong").status == 400)
    ck("oauth callback missing code 400", c.get("/larz/oauth/google/callback").status == 400)


def main():
    for t in [test_totp, test_twofa_manager, test_client, test_uploads,
              test_flash_and_htmx, test_payouts, test_providers, test_oauth]:
        print("\n# " + t.__name__)
        t()
    print("\n%d passed, %d failed" % (P[0], F[0]))
    return 1 if F[0] else 0


if __name__ == "__main__":
    sys.exit(main())
