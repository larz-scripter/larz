"""
A complete money-native app in ~40 lines. Runs offline — no API keys.

    python3 examples/paid_app.py

Then:
  * open  http://127.0.0.1:8000/            → free
  * open  http://127.0.0.1:8000/pro/report  → bounces you through checkout,
           then serves the paid page (MockProvider grants the entitlement)
  * POST  http://127.0.0.1:8000/api/summarize → 402 until you have credits
  * open  http://127.0.0.1:8000/give-me-credits → tops up your balance (demo)
  * open  http://127.0.0.1:8000/sitemap.xml → auto-generated
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
import larz.money as money
import larz.seo as seo

app = Larz(secret="demo-secret")
money.enable(app, base_url="http://127.0.0.1:8000")
seo.enable(app, base_url="http://127.0.0.1:8000")


@app.get("/")
def home(req):
    return ("<h1>⚡ Larz</h1><p>The money-native framework.</p>"
            "<ul>"
            "<li><a href='/pro/report'>/pro/report</a> — paid ($9)</li>"
            "<li><a href='/give-me-credits'>/give-me-credits</a> — add demo credits</li>"
            "<li><a href='/sitemap.xml'>/sitemap.xml</a> — auto SEO</li>"
            "</ul>")


@app.paid("$9")                      # one-off unlock; MockProvider in dev
@app.get("/pro/report")
def pro_report(req):
    return "<h1>📈 Pro Report</h1><p>Paid content for %s.</p>" % req.subject


@app.metered("$0.02/call")           # per-call billing against prepaid credit
@app.post("/api/summarize")
def summarize(req):
    text = (req.json() or {}).get("text", "")
    return Response.json({"summary": text[:60], "charged_cents": 2,
                          "balance_cents": app.money.store.balance(req.subject)})


@app.get("/give-me-credits")         # demo helper — real apps sell credit packs
def give_credits(req):
    app.money.store.add_credit(req.subject, 100)   # +$1.00
    return Response.redirect("/larz/credits")


if __name__ == "__main__":
    app.run()
