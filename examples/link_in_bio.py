"""
Larz link-in-bio — a Linktree-style product with a Pro tier.

Shows: accounts, a public per-user page, and a plan gate (Pro removes branding
and unlocks unlimited links / click stats).

Run:  python3 examples/link_in_bio.py   →  http://127.0.0.1:8000
Sign up, add links, visit /u/<you>, then hit the free-tier cap to see the upsell.
"""
from larz import Larz, Response
from larz.models import Model, StrField, IntField, connect
from larz.pricing import Pricing
import larz.money as money, larz.auth as auth

app = Larz(secret="dev", debug=True)
connect("linkbio.db")
money.enable(app, base_url="http://127.0.0.1:8000")
auth.enable(app)
(Pricing()
 .plan("free", "$0", limits={"links": 5})
 .plan("pro", "$5/mo", trial_days=7, highlight=True,
       features=["Unlimited links", "No branding", "Click stats"],
       limits={"links": None, "no_branding": True})
 .mount(app))


class Link(Model):
    user = StrField(index=True)          # the user's email/handle
    label = StrField()
    url = StrField()
    clicks = IntField(default=0)
Link.create_table()


@app.get("/")
def home(req):
    if not req.user:
        return ("<h1>Link in Bio</h1><form method=post action=/signup>"
                "<input name=email placeholder=email> <input name=password type=password> "
                "<button>Sign up</button></form>")
    return Response.redirect("/dashboard")


@app.post("/signup")
def signup(req):
    try:
        app.auth.register(req.form["email"], req.form["password"])
        app.auth.login(req, req.form["email"], req.form["password"])
    except Exception:
        return ("could not register", 400)
    return Response.redirect("/dashboard")


@app.login_required
@app.get("/dashboard")
def dashboard(req):
    handle = req.user.email
    links = Link.where(user=handle).all()
    rows = "".join("<li>%s → %s (%d clicks)</li>" % (l.label, l.url, l.clicks) for l in links)
    pro = app.feature(req, "no_branding")
    return ("<h1>Your page</h1><p>Public at <a href='/u/%s'>/u/%s</a>%s</p>"
            "<form method=post action=/add><input name=label placeholder=Label> "
            "<input name=url placeholder=https://...><button>Add link</button></form>"
            "<ul>%s</ul>" % (handle, handle, "" if pro else " — <a href='/larz/subscribe/pro'>Go Pro</a>", rows))


@app.login_required
@app.post("/add")
def add(req):
    handle = req.user.email
    n = Link.count(user=handle)
    if not app.within_limit(req, "links", n):
        return Response.redirect("/larz/subscribe/pro")     # free tier: 5 links
    Link(user=handle, label=req.form.get("label", "Link"),
         url=req.form.get("url", "#")).save()
    return Response.redirect("/dashboard")


@app.get("/u/<handle:path>")
def public(req):
    handle = req.params["handle"]
    links = Link.where(user=handle).all()
    if not links:
        return ("no such page", 404)
    body = "".join("<a href='/c/%d' style='display:block;padding:12px;margin:8px 0;"
                   "background:#f2f2f2;border-radius:10px;text-decoration:none'>%s</a>"
                   % (l.id, l.label) for l in links)
    # branding footer unless the owner is Pro
    owner = auth.User.where(email=handle).first()
    is_pro = owner and app.money.store.is_entitled("user:%s" % owner.id, "plan:pro")
    brand = "" if is_pro else "<p style='color:#aaa;font-size:12px'>Made with Larz</p>"
    return ("<div style='max-width:420px;margin:2rem auto;text-align:center;font:16px system-ui'>"
            "<h1>@%s</h1>%s%s</div>" % (handle, body, brand))


@app.get("/c/<id:int>")
def click(req):
    link = Link.get(req.params["id"])
    if not link:
        return ("gone", 404)
    link.clicks += 1
    link.save()
    return Response.redirect(link.url)


if __name__ == "__main__":
    app.run()
