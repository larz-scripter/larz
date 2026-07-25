"""
A digital shop with per-product prices — shows Larz's *imperative* paywall.

Unlike @app.paid("$9") (a fixed price baked into one route), a real catalog has
many products at different prices, so you gate inside the handler with
app.money.require(...). This is the pattern that runs the real EarnifyHub
catalog on Larz (1,100+ products, per-item pricing).

    python3 examples/shop_app.py
    #  browse /  ->  /p/<slug>  ->  /download/<slug> bounces through checkout
    #  then serves; each product is entitled independently.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
import larz.money as money

# a catalog — in a real app this comes from your DB (any store; Larz doesn't care)
CATALOG = {
    "nft-beginners-guide":   {"title": "NFT Beginners Guide",   "price": 17.0},
    "altcoin-research-guide":{"title": "Altcoin Research Guide","price": 19.0},
    "ai-power-bundle":       {"title": "AI Power Bundle",       "price": 49.0},
}

app = Larz(secret="shop-demo", debug=True)
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin123")

def page(body):
    return Response("<style>body{font:16px system-ui;max-width:640px;margin:2rem auto;"
                    "padding:0 1rem}a{color:#0a7}</style>" + body +
                    "<hr><p><a href='/'>all products</a> · "
                    "<a href='/larz/admin?token=admin123'>revenue</a></p>")

@app.get("/")
def home(req):
    items = "".join("<li><a href='/p/%s'>%s</a> — $%.2f</li>"
                    % (s, p["title"], p["price"]) for s, p in CATALOG.items())
    return page("<h1>Larz Shop</h1><p>Per-product pricing via the imperative "
                "paywall.</p><ul>%s</ul>" % items)

@app.get("/p/<slug>")
def detail(req):
    p = CATALOG.get(req.params["slug"])
    if not p:
        return page("<h1>Not found</h1>"), 404
    if app.money.entitled(req, req.params["slug"]):
        cta = "<p>✅ Owned. <a href='/download/%s'>Download →</a></p>" % req.params["slug"]
    else:
        cta = "<p><a href='/download/%s'><b>Buy — $%.2f</b></a></p>" % (
            req.params["slug"], p["price"])
    return page("<h1>%s</h1><p>$%.2f</p>%s" % (p["title"], p["price"], cta))

@app.get("/download/<slug>")
def download(req):
    p = CATALOG.get(req.params["slug"])
    if not p:
        return page("<h1>Not found</h1>"), 404
    # dynamic-price paywall: not paid -> checkout; paid -> deliver
    gate = app.money.require(req, sku=req.params["slug"],
                             cents=int(p["price"] * 100), success_path=req.path)
    if gate:
        return gate
    return page("<h1>⬇ %s</h1><p>Paid — your download is ready.</p>" % p["title"])

if __name__ == "__main__":
    app.run()
