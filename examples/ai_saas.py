"""
Larz AI SaaS example — the Revenue Engine in one file.

A monetized AI writing tool that shows off v1.2:
  * pricing-as-code (free / pro / unlimited) → a real /larz/pricing page
  * a token-metered AI endpoint (@app.ai_metered + app.ai.charge)
  * BYOK — customers can paste their own key and stop being metered
  * referral links that pay a 20% commission when a referred user upgrades
  * a live revenue dashboard at /larz/admin/revenue

Run:  python3 examples/ai_saas.py   →  http://127.0.0.1:8000
Uses the keyless MockProvider and a fake LLM, so it runs with no accounts/keys.
"""
import os
from larz import Larz, Response
from larz.models import connect
import larz.money as money
import larz.ai as ai
import larz.referrals as referrals
import larz.analytics as analytics
from larz.pricing import Pricing

app = Larz(secret="dev-secret", debug=True)
connect("ai_saas.db")
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin")
ai.enable(app)
referrals.enable(app, reward="20%")
analytics.enable(app, token="admin")

# ---- pricing as code ----------------------------------------------------- #
(Pricing()
 .plan("free", "$0", limits={"words_per_day": 500})
 .plan("pro", "$19/mo", trial_days=7, highlight=True,
       tagline="For creators who ship daily",
       features=["Unlimited words", "Priority model"],
       limits={"words_per_day": None})
 .plan("unlimited", "$99/mo",
       features=["Everything in Pro", "No per-token metering"],
       limits={"ai_unlimited": True})
 .credit_pack("starter", price="$10", credit="$12", label="$12 of AI credit")
 .coupon("LAUNCH25", percent_off=25)
 .mount(app))

app.ai.price("larz-writer", input="$0.50/1M", output="$1.50/1M")

# a fake LLM so the demo needs no network / keys
def fake_llm(prompt):
    words = ("Here is a crisp draft based on your idea: " + prompt + ". "
             "It is clear, concise, and ready to publish.").split()
    return " ".join(words), {"in": len(prompt.split()), "out": len(words)}


@app.before
def _welcome_credit(req):
    # give first-time visitors 25¢ of free AI credit so the demo just works
    if not req.session.get("welcomed"):
        app.money.store.add_credit(req.subject, 25)
        req.session["welcomed"] = True


@app.get("/")
def home(req):
    return ("<h1>Larz Writer</h1>"
            "<form method=post action=/write><input name=prompt style=width:60%% "
            "placeholder='Write a launch tweet about...'> <button>Write</button></form>"
            "<p>Credit balance: <b>%d¢</b> · "
            "<a href=/larz/pricing>Pricing</a> · <a href=/refer>Your referral link</a> · "
            "<a href='/larz/admin/revenue?token=admin'>Revenue</a></p>"
            % app.money.store.balance(req.subject))


@app.ai_metered("larz-writer", per_minute=30)
@app.post("/write")
def write(req):
    text, usage = fake_llm(req.form.get("prompt", "something great"))
    cost = app.ai.charge(req, "larz-writer", usage["in"], usage["out"])
    return ("<p><b>Draft:</b> %s</p><p style='color:#888'>Metered %d input / %d output tokens.</p>"
            "<a href=/>← back</a>" % (text, usage["in"], usage["out"]))


@app.get("/refer")
def refer(req):
    st = app.referrals.stats(req.subject)
    return ("<h1>Refer &amp; earn</h1><p>Share your link — earn 20%% when someone upgrades:</p>"
            "<code>%s</code><p>%d referrals · %d converted · %d¢ earned</p><a href=/>← back</a>"
            % (app.referrals.url(req.subject), st["referrals"], st["converted"], st["earned_cents"]))


if __name__ == "__main__":
    app.run()
