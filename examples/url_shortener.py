"""
Larz URL shortener — a real, tiny product: short links with click analytics and
a metered API for programmatic link creation.

Shows: the ORM, redirects, an owner-scoped dashboard, and a metered API endpoint
billed per created link.

Run:  python3 examples/url_shortener.py   →  http://127.0.0.1:8000
"""
import string
from larz import Larz, Response
from larz.models import Model, StrField, IntField, connect
import larz.money as money, larz.api as api

app = Larz(secret="dev", debug=True)
connect("shortener.db")
money.enable(app, base_url="http://127.0.0.1:8000")
api.enable(app)
app.money.credit_pack("api-100", price="$5", credit="$6", label="~600 API links")

_ALPHABET = string.ascii_letters + string.digits


class Link(Model):
    slug = StrField(unique=True, index=True)
    target = StrField()
    owner = StrField(index=True)
    clicks = IntField(default=0)
Link.create_table()


def _slug(n):
    from hashlib import blake2b
    import os
    return blake2b(os.urandom(8), digest_size=4).hexdigest()[:6]


def _shorten(target, owner):
    slug = _slug(6)
    while Link.where(slug=slug).first():
        slug = _slug(6)
    Link(slug=slug, target=target, owner=owner).save()
    return slug


@app.get("/")
def home(req):
    mine = Link.where(owner=req.subject).order("-clicks").all()
    rows = "".join("<li><a href='/%s'>/%s</a> → %s <b>(%d clicks)</b></li>"
                   % (l.slug, l.slug, l.target, l.clicks) for l in mine)
    return ("<h1>Larz Short</h1>"
            "<form method=post action=/new><input name=url style=width:60%% "
            "placeholder='https://example.com/very/long/link'> <button>Shorten</button></form>"
            "<h2>Your links</h2><ul>%s</ul>" % (rows or "<li>none yet</li>"))


@app.post("/new")
def new(req):
    url = req.form.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        return ("URL must start with http:// or https://", 400)
    _shorten(url, req.subject)
    return Response.redirect("/")


@app.get("/<slug:slug>")
def go(req):
    link = Link.where(slug=req.params["slug"]).first()
    if not link:
        return ("no such link", 404)
    link.clicks += 1
    link.save()
    return Response.redirect(link.target)


# --- metered API: create links programmatically, billed per link ----------- #
@app.metered("$0.01/call")
@app.validate({"url": {"type": str, "required": True}})
@app.post("/api/shorten")
def api_shorten(req):
    slug = _shorten(req.data["url"], req.subject)
    return Response.json({"slug": slug, "short_url": "%s/%s" % (app.money.base_url, slug)})


if __name__ == "__main__":
    app.run()
