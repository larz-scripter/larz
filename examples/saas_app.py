"""
A complete mini-SaaS in one file — shows off most of Larz v0.2. No API keys.

    python3 examples/saas_app.py

Demonstrates:
  * templating (Environment / inline templates) + SEO meta tags
  * models (a Note ORM backed by sqlite)
  * a free tier + a "pro" plan with a 7-day trial (@app.plan)
  * a metered AI-style endpoint billed from prepaid credits
  * credit packs + pricing page + revenue dashboard
  * security middleware (rate limiting + bot filter)
  * blueprints for the API
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response, Blueprint, Model, Field, connect
from larz.templating import Template
import larz.money as money
import larz.security as security
from larz.seo import meta_tags

# --- app + plumbing -------------------------------------------------------- #
app = Larz(secret="saas-demo-secret", debug=True)
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin123")
app.money.plan("pro", "$9/mo",
               features=["Unlimited notes", "AI summaries", "Priority support"],
               trial_days=7)
app.money.credit_pack("small", price="$5", credit="$5", label="500 AI credits")
app.money.credit_pack("big", price="$20", credit="$25", label="2,500 AI credits (+25% bonus)")
app.money.store.add_coupon("LAUNCH50", percent_off=50, days_valid=30)

app.use(security.bot_filter())
app.use(security.RateLimiter(limit=120, window=60).hook())

# --- data ------------------------------------------------------------------ #
connect("saas_demo.db")
class Note(Model):
    subject = Field(str, index=True)
    text = Field(str)
Note.create_table()

PAGE = Template("""<!doctype html><html><head>{{ head | safe }}
<style>body{font:16px system-ui;max-width:640px;margin:2rem auto;padding:0 1rem}
a{color:#0a7}</style></head><body>{{ body | safe }}
<hr><p><a href="/">home</a> · <a href="/pro">pro</a> ·
<a href="/larz/pricing">pricing</a> · <a href="/larz/credits">credits</a></p>
</body></html>""")

def page(title, body):
    return Response(PAGE.render(
        head=meta_tags(title, "A tiny SaaS built on Larz", site_name="NoteLarz"),
        body=body))

# --- routes ---------------------------------------------------------------- #
@app.get("/")
def home(req):
    notes = Note.where(subject=req.subject).all()
    items = "".join("<li>%s</li>" % n.text for n in notes) or "<li><i>none yet</i></li>"
    return page("NoteLarz", (
        "<h1>NoteLarz</h1><p>Free: 3 notes. Pro: unlimited + AI.</p>"
        "<form method=post action=/note><input name=text placeholder='a note'>"
        "<button>Add</button></form><ul>%s</ul>"
        "<p>Try the <a href='/pro'>Pro area</a> (7-day free trial).</p>" % items))

@app.post("/note")
def add_note(req):
    if Note.count(subject=req.subject) >= 3 and not app.money.store.is_entitled(req.subject, "plan:pro"):
        return page("Upgrade", "<h1>Free limit reached</h1>"
                    "<p>Free tier is 3 notes. <a href='/pro'>Go Pro</a> for unlimited.</p>")
    Note(subject=req.subject, text=req.form.get("text", "")[:200]).save()
    return Response.redirect("/")

@app.plan("pro")
@app.get("/pro")
def pro(req):
    return page("Pro", "<h1>Pro area</h1><p>Unlocked for %s. Unlimited notes + "
                "the AI summary API below.</p>" % req.subject)

# metered AI-style endpoint (billed from prepaid credits) via a blueprint
api = Blueprint("api", prefix="/api")
@app.metered("$0.01/call")
@api.post("/summarize")
def summarize(req):
    text = (req.json() or {}).get("text", "")
    words = text.split()
    summary = " ".join(words[:12]) + ("…" if len(words) > 12 else "")
    return Response.json({"summary": summary, "charged_cents": 1,
                          "balance_cents": app.money.store.balance(req.subject)})
app.register(api)

if __name__ == "__main__":
    app.run()
