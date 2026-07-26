"""v1.2 Revenue Engine tests — pricing-as-code, feature/limits, dunning, metrics,
analytics, crypto, referrals, AI monetization. Plain python3, in-process, no pytest."""
import os, sys, io, time, json, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.models import connect
from larz.testing import Client
import larz.money as money
import larz.pricing as pricing_mod
from larz.pricing import Pricing
import larz.analytics as analytics
import larz.referrals as referrals
import larz.ai as ai
from larz.crypto import Cipher

P = [0]; F = [0]
def ck(name, cond):
    if cond: P[0] += 1; print("  ok   " + name)
    else: F[0] += 1; print("  FAIL " + name)

def fresh_app(**kw):
    connect(":memory:")
    db = os.path.join(tempfile.mkdtemp(), "m.db")
    app = Larz(secret="test-secret")
    money.enable(app, base_url="http://x", db=db, admin_token="adm")
    return app


# ---- pricing-as-code ------------------------------------------------------ #
def test_pricing():
    app = fresh_app()
    pr = (Pricing()
          .plan("free", "$0", limits={"projects": 3})
          .plan("pro", "$19/mo", trial_days=14, highlight=True,
                features=["Priority"], limits={"projects": None, "api": True})
          .usage("api_call", "$0.002/call")
          .credit_pack("bulk", price="$20", credit="$25", label="Bulk")
          .coupon("LAUNCH50", percent_off=50))
    pr.mount(app)
    ck("plans registered", set(app.money.plans) == {"free", "pro"})
    ck("plan limits stored", app.money.plans["pro"]["limits"]["projects"] is None)
    ck("usage price registered", app.money.usages["api_call"] == (0, "call") or app.money.usages["api_call"][0] == 0)
    ck("credit pack registered", "bulk" in app.money.packs)
    ck("coupon applies 50%", app.money.store.apply_coupon("LAUNCH50", 1000) == (500, True))
    c = Client(app)
    r = c.get("/larz/pricing")
    ck("pricing page renders cards", r.status == 200 and "Most popular" in r.text and "$19/mo" in r.text)
    ck("subscribe redirects to checkout/trial", c.get("/larz/subscribe/pro").status in (302, 303))
    ck("subscribe unknown plan 404", c.get("/larz/subscribe/nope").status == 404)


# ---- feature flags & limits ---------------------------------------------- #
def test_features():
    app = fresh_app()
    app.money.plan("free", "$0", limits={"projects": 3}, rank=0)
    app.money.plan("pro", "$19/mo", features=["Priority"], limits={"projects": None, "api": True}, rank=1)
    class R: subject = "user:7"
    r = R()
    # unentitled users fall back to the implicit free tier ($0 plan)
    ck("free tier: no pro feature", app.feature(r, "api") is False)
    ck("free tier: uses free plan's limit", app.plan_limit(r, "projects", default=1) == 3)
    ck("free tier: current is free", app.current_plan(r)["price"] == "$0")
    app.money.store.grant("user:7", "plan:pro")
    ck("pro: current plan", app.current_plan(r)["price"] == "$19/mo")
    ck("pro: feature api", app.feature(r, "api") is True)
    ck("pro: within unlimited", app.within_limit(r, "projects", 9999) is True)
    app.money.store.revoke("user:7", "plan:pro")
    app.money.store.grant("user:7", "plan:free")
    ck("free: 2<3 ok", app.within_limit(r, "projects", 2) is True)
    ck("free: 3<3 no", app.within_limit(r, "projects", 3) is False)


# ---- dunning -------------------------------------------------------------- #
def test_dunning():
    app = fresh_app()
    app.money.plan("pro", "$19/mo")
    app.money.store.set_interval("plan:pro", "mo")
    app.money.store.grant("user:1", "plan:pro", days=-1)     # already expired
    failed = []
    app.money.on_payment_failed(lambda s, k: failed.append(s))
    notes = []
    r1 = app.money.run_dunning(grace_days=7, schedule=(0, 3, 7),
                               on_notify=lambda s, k, a: notes.append(a))
    ck("dunning notifies day-0", r1["notified"] == 1 and notes == [1])
    ck("not yet revoked", r1["revoked"] == 0 and not failed)
    # simulate 8 days elapsed by backdating first_failed
    row = app.money.store.dunning_get("user:1", "plan:pro")
    app.money.store.dunning_upsert("user:1", "plan:pro", time.time() - 8 * 86400,
                                   row["attempts"], status="retrying")
    r2 = app.money.run_dunning(grace_days=7)
    ck("dunning revokes after grace", r2["revoked"] == 1 and failed == ["user:1"])
    ck("access revoked", not app.money.store.is_entitled("user:1", "plan:pro"))
    # a successful renewal clears dunning state
    app.money.store.dunning_upsert("user:2", "plan:pro", time.time(), 1)
    app.money.clear_dunning("user:2", "plan:pro")
    ck("renewal clears dunning", app.money.store.dunning_get("user:2", "plan:pro") is None)


# ---- metrics -------------------------------------------------------------- #
def test_metrics():
    app = fresh_app()
    app.money.plan("pro", "$20/mo", rank=1)
    app.money.plan("team", "$100/mo", rank=2)
    app.money.store.set_interval("plan:pro", "mo"); app.money.store.set_interval("plan:team", "mo")
    app.money.store.grant("u1", "plan:pro", days=30)
    app.money.store.grant("u2", "plan:pro", days=30)
    app.money.store.grant("u3", "plan:team", days=30)
    m = app.money.metrics()
    ck("MRR = 2*2000 + 10000", m["mrr_cents"] == 14000)
    ck("ARR = MRR*12", m["arr_cents"] == 14000 * 12)
    ck("3 active subscribers", m["active_subscribers"] == 3)
    ck("ARPU = MRR/3", m["arpu_cents"] == round(14000 / 3))
    ck("per-plan breakdown", len(m["per_plan"]) == 2)
    ck("revenue series length", len(app.money.store.revenue_series(30)) == 30)


# ---- analytics dashboard -------------------------------------------------- #
def test_analytics():
    app = fresh_app()
    app.money.plan("pro", "$20/mo", rank=1); app.money.store.set_interval("plan:pro", "mo")
    app.money.store.grant("u1", "plan:pro", days=30)
    analytics.enable(app, token="adm")
    c = Client(app)
    ck("revenue dash gated", c.get("/larz/admin/revenue").status == 403)
    r = c.get("/larz/admin/revenue?token=adm")
    ck("revenue dash renders", r.status == 200 and "MRR" in r.text and "$20.00" in r.text)
    j = c.get("/larz/admin/revenue?token=adm&format=json").json
    ck("revenue json", j and j["mrr_cents"] == 2000)


# ---- crypto --------------------------------------------------------------- #
def test_crypto():
    c = Cipher("master-secret")
    t = c.encrypt("sk-live-123")
    ck("crypto roundtrip", c.decrypt(t) == "sk-live-123")
    ck("crypto rejects tamper", c.decrypt(t[:-4] + "AAAA") is None)
    ck("crypto rejects wrong key", Cipher("other").decrypt(t) is None)
    ck("crypto rejects junk", c.decrypt("not-base64!!") is None)
    ck("two encryptions differ (nonce)", c.encrypt("x") != c.encrypt("x"))


# ---- referrals ------------------------------------------------------------ #
def test_referrals():
    app = fresh_app()
    referrals.enable(app, reward="20%")
    code = app.referrals.code_for("user:alice")
    ck("referral code stable", app.referrals.code_for("user:alice") == code)
    ck("referral url", app.referrals.url("user:alice").endswith("?ref=" + code))
    # a referred user is attributed then pays -> alice earns 20%
    class Req:
        def __init__(self): self.session = {}; self.query = {}
    rq = Req(); rq.query = {"ref": code}
    app.referrals.capture(rq)
    ck("capture stores in session", rq.session.get("ref") == code)
    bound = app.referrals.attribute("user:bob", req=rq)
    ck("attribute binds to referrer", bound == "user:alice")
    ck("no self-referral", app.referrals.attribute("user:alice", code=code) is None)
    ck("no double-bind", app.referrals.attribute("user:bob", code=code) is None)
    # simulate bob paying $50 -> commission 20% = $10 to alice
    app.money._fulfil({"subject": "user:bob", "sku": "plan:pro", "cents": 5000,
                       "payment_id": "pay1"}, "mock")
    ck("commission credited to referrer", app.money.owed("user:alice") == 1000)
    st = app.referrals.stats("user:alice")
    ck("stats: 1 referral, converted, earned", st["referrals"] == 1 and st["converted"] == 1 and st["earned_cents"] == 1000)
    # first_only: bob paying again does not double-commission
    app.money._fulfil({"subject": "user:bob", "sku": "plan:pro", "cents": 5000,
                       "payment_id": "pay2"}, "mock")
    ck("first_only: no second commission", app.money.owed("user:alice") == 1000)


# ---- AI monetization ------------------------------------------------------ #
def test_ai():
    app = fresh_app()
    ai.enable(app)
    app.ai.price("gpt-4o", input="$2.50/1M", output="$10/1M")
    ck("input price 2.5 micro/tok", abs(app.ai.prices["gpt-4o"][0] - 2.5) < 1e-9)
    ck("cost of 1000 out tokens = 10000 micros", app.ai.cost_micros("gpt-4o", 0, 1000) == 10000)
    # charge sub-cent then cross a cent
    app.money.store.add_credit("user:z", 100)      # $1.00
    class R: subject = "user:z"
    r = R()
    app.ai.charge(r, "gpt-4o", 0, 500)             # 5000 micros, no cent yet
    ck("sub-cent: balance unchanged", app.money.store.balance("user:z") == 100)
    app.ai.charge(r, "gpt-4o", 0, 500)             # +5000 = 10000 micros = 1 cent
    ck("whole cent debited", app.money.store.balance("user:z") == 99)
    u = app.ai.usage("user:z")
    ck("usage tracked", u["calls"] == 2 and u["out_tokens"] == 1000 and u["spent_micros"] == 10000)
    # BYOK: encrypted, and metering is skipped
    app.ai.set_byok("user:z", "sk-user-key")
    ck("byok stored encrypted", app.ai.get_byok("user:z") == "sk-user-key")
    raw = ai.AIKey.where(subject="user:z").first().enc
    ck("byok not plaintext", "sk-user-key" not in raw)
    before = app.money.store.balance("user:z")
    app.ai.charge(r, "gpt-4o", 1000, 1000)
    ck("byok skips metering", app.money.store.balance("user:z") == before)

def test_ai_guard():
    app = fresh_app(); ai.enable(app)
    @app.ai_metered("gpt-4o", per_minute=2)
    @app.post("/api/ai")
    def ep(req): return {"ok": True}
    @app.get("/whoami")
    def who(req): return {"s": req.subject}
    c = Client(app)
    ck("no credit -> 402", c.post("/api/ai").status == 402)
    sid = c.get("/whoami").json["s"]               # learn this client's subject
    app.money.store.add_credit(sid, 50)            # 50 cents
    ck("with credit -> 200 (1)", c.post("/api/ai").status == 200)
    ck("with credit -> 200 (2)", c.post("/api/ai").status == 200)
    ck("rate limit -> 429 (3rd)", c.post("/api/ai").status == 429)
    # BYOK client with zero credit still passes the guard
    c2 = Client(app)
    sid2 = c2.get("/whoami").json["s"]
    app.ai.set_byok(sid2, "sk-x")
    ck("byok passes guard w/ 0 credit", c2.post("/api/ai").status == 200)


def main():
    for t in [test_pricing, test_features, test_dunning, test_metrics,
              test_analytics, test_crypto, test_referrals, test_ai, test_ai_guard]:
        print("\n# " + t.__name__)
        t()
    print("\n%d passed, %d failed" % (P[0], F[0]))
    return 1 if F[0] else 0


if __name__ == "__main__":
    sys.exit(main())
