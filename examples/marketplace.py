"""
Larz marketplace example — a money-native two-sided marketplace in one file.

Shows the v1.1 additions working together:
  * file uploads (req.files) + LocalStorage for product images
  * a paid checkout per listing (imperative paywall, dynamic price)
  * a split payout ledger: each sale credits the seller and the platform
  * a seller payouts view of what they're owed

Run:  python3 examples/marketplace.py   →  http://127.0.0.1:8000
Dev payments use the keyless MockProvider, so it works with no accounts.
"""
import os
from larz import Larz, Response
from larz.models import Model, StrField, IntField, connect
import larz.money as money
from larz.storage import LocalStorage

app = Larz(secret="dev-secret", debug=True)
connect("marketplace.db")
money.enable(app, base_url="http://127.0.0.1:8000")
images = LocalStorage("mkt_uploads", url_prefix="/img").mount(app)

PLATFORM_FEE = 0.15          # the house takes 15%


class Listing(Model):
    title = StrField()
    seller = StrField(index=True)       # e.g. "seller:alice"
    price_cents = IntField(default=0)
    image = StrField(default="")
Listing.create_table()


@app.get("/")
def home(req):
    rows = "".join(
        "<li><b>%s</b> — $%.2f by %s "
        "<a href='/buy/%d'>buy</a>%s</li>"
        % (l.title, l.price_cents / 100.0, l.seller, l.id,
           " <img src='%s' height=40>" % images.url(l.image) if l.image else "")
        for l in Listing.all(order="-id"))
    return ("<h1>Larz Market</h1><ul>%s</ul>"
            "<h2>Sell something</h2>"
            "<form method=post action=/sell enctype='multipart/form-data'>"
            "<input name=title placeholder=Title required> "
            "<input name=seller placeholder='seller:you' required> "
            "<input name=price placeholder='$9.99' required> "
            "<input type=file name=image accept='image/*'> "
            "<button>List it</button></form>"
            "<p><a href='/payouts?seller=seller:you'>My payouts</a></p>" % rows)


@app.post("/sell")
def sell(req):
    cents, _ = money.parse_price(req.form.get("price", "$0"))
    name = ""
    f = req.files.get("image")
    if f and f.size:
        name = images.save(f)
    Listing(title=req.form.get("title", "Untitled"), seller=req.form.get("seller", "seller:anon"),
            price_cents=cents, image=name).save()
    return Response.redirect("/")


@app.get("/buy/<id:int>")
def buy(req):
    lst = Listing.get(req.params["id"])
    if not lst:
        return ("no such listing", 404)
    sku = "listing:%d" % lst.id
    gate = app.money.require(req, sku=sku, cents=lst.price_cents, success_path="/bought/%d" % lst.id)
    if gate:
        return gate
    return Response.redirect("/bought/%d" % lst.id)


@app.get("/bought/<id:int>")
def bought(req):
    lst = Listing.get(req.params["id"])
    sku = "listing:%d" % lst.id
    if not app.money.entitled(req, sku):
        return Response.redirect("/buy/%d" % lst.id)
    # record the split ONCE per buyer+listing
    ref = "%s:%s" % (req.subject, sku)
    if not any(p["ref"] == ref for p in app.money.payouts()):
        fee = int(lst.price_cents * PLATFORM_FEE)
        app.money.split([(lst.seller, lst.price_cents - fee), ("platform", fee)],
                        sku=sku, ref=ref)
    return "<h1>Thanks!</h1><p>You bought <b>%s</b>.</p><a href='/'>back</a>" % lst.title


@app.get("/payouts")
def payouts(req):
    seller = req.query.get("seller", "seller:you")
    owed = app.money.owed(seller)
    rows = "".join("<li>$%.2f — %s — %s</li>"
                   % (p["cents"] / 100.0, p["sku"], p["status"])
                   for p in app.money.payouts(seller))
    return ("<h1>Payouts for %s</h1><p>Owed now: <b>$%.2f</b></p><ul>%s</ul>"
            "<a href='/'>back</a>" % (seller, owed / 100.0, rows))


if __name__ == "__main__":
    app.run()
