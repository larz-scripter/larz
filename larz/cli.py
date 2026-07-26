"""
larz.cli — the `larz` command: a project scaffolder + dev helpers.

    larz new <name> [--template api|saas|ai|marketplace|minimal]
    larz new --list         list available templates
    larz run [file]         run an app (defaults to app.py)
    larz routes [file]      print the route table
    larz version

Invoked via `python -m larz ...` (see larz/__main__.py) or the `larz` console
script installed with the package.
"""

import os
import sys
import importlib.util

from . import __version__


# --------------------------------------------------------------------------- #
#  Shared project files
# --------------------------------------------------------------------------- #
_ENV = """# Copy to .env and fill in for production. Dev works with no keys.
SECRET=change-me-to-a-long-random-string
BASE_URL=http://127.0.0.1:8000
ADMIN_TOKEN=admin
# STRIPE_KEY=sk_live_...
# STRIPE_WEBHOOK_SECRET=whsec_...
"""

_GITIGNORE = "__pycache__/\n*.pyc\n*.db\n.env\nuploads/\n"

_REQS = "larz>=2.0\ngunicorn\n"

_TEST = '''"""Smoke test — python3 tests/test_app.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from larz.testing import Client
import app as A

def main():
    c = Client(A.app)
    assert c.get("/").status == 200, "home should render"
    print("ok  home renders")
    assert c.get("/larz/pricing").status in (200, 404)
    print("ok  smoke passed")

if __name__ == "__main__":
    main()
'''

_README = """# @@NAME@@

A [Larz](https://larzos.com/larz/) app (`@@TEMPLATE@@` template).

```bash
pip install -r requirements.txt
python3 app.py            # http://127.0.0.1:8000
python3 tests/test_app.py
```

Dev uses the keyless mock payment provider, so it runs with no accounts or keys.
Fill in `.env` and deploy with `gunicorn app:app` for production.
See the [docs](https://larzos.com/larz/docs/).
"""


# --------------------------------------------------------------------------- #
#  App templates  (@@NAME@@ substituted; no str.format so braces are safe)
# --------------------------------------------------------------------------- #
_MINIMAL = '''\
from larz import Larz, Response
import larz.money as money

app = Larz(secret="change-me-in-prod")
money.enable(app, base_url="http://127.0.0.1:8000")
app.money.plan("pro", "$9/mo", trial_days=7, features=["Pro features"])


@app.get("/")
def home(req):
    return ("<h1>@@NAME@@</h1><p>A money-native Larz app.</p>"
            "<p><a href='/pro'>/pro</a> (7-day trial) &middot; "
            "<a href='/larz/pricing'>pricing</a></p>")


@app.plan("pro")
@app.get("/pro")
def pro(req):
    return "<h1>Pro area</h1><p>Unlocked for %s.</p>" % req.subject


if __name__ == "__main__":
    app.run()
'''

_API = '''\
"""A paid JSON API: validation, API keys, per-call metering, auto docs."""
from larz import Larz, Response
import larz.money as money, larz.api as api, larz.auth as auth

app = Larz(secret="change-me-in-prod")
money.enable(app, base_url="http://127.0.0.1:8000")
auth.enable(app)
api.enable(app)
app.enable_docs(title="@@NAME@@ API", version="1.0")
app.money.credit_pack("bulk", price="$10", credit="$12", label="$12 of API credit")


@app.get("/")
def home(req):
    return ("<h1>@@NAME@@ API</h1><p>See <a href='/docs'>/docs</a> and "
            "<a href='/larz/credits'>buy credit</a>.</p>")


@app.api_key_required(plan="pro")
@app.validate({"text": {"type": str, "required": True, "maxlen": 5000}})
@app.metered("$0.01/call")
@app.post("/api/wordcount")
def wordcount(req):
    return Response.json({"words": len(req.data["text"].split())})


if __name__ == "__main__":
    app.run()
'''

_SAAS = '''\
"""A SaaS: accounts, pricing-as-code, a Pro plan, a metered API, analytics."""
from larz import Larz, Response
from larz.models import Model, StrField, connect
from larz.pricing import Pricing
import larz.money as money, larz.auth as auth, larz.analytics as analytics

app = Larz(secret="change-me-in-prod", debug=True)
connect("@@NAME@@.db")
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin")
auth.enable(app)
analytics.enable(app, token="admin")

(Pricing()
 .plan("free", "$0", limits={"projects": 3})
 .plan("pro", "$19/mo", trial_days=14, highlight=True,
       features=["Unlimited projects", "Priority support"],
       limits={"projects": None})
 .mount(app))


class Project(Model):
    owner = StrField(index=True)
    name = StrField()
Project.create_table()


@app.get("/")
def home(req):
    return ("<h1>@@NAME@@</h1><p><a href='/larz/pricing'>Pricing</a> &middot; "
            "<a href='/larz/admin/revenue?token=admin'>Revenue</a></p>")


@app.get("/projects")
def projects(req):
    n = Project.count(owner=req.subject)
    return {"projects": n, "can_add": app.within_limit(req, "projects", n)}


@app.post("/projects")
def new_project(req):
    n = Project.count(owner=req.subject)
    if not app.within_limit(req, "projects", n):
        return Response.redirect("/larz/subscribe/pro")
    Project(owner=req.subject, name=(req.form.get("name") or "Untitled")).save()
    return Response.redirect("/projects")


if __name__ == "__main__":
    app.run()
'''

_AI = '''\
"""A monetized AI app: pricing, per-token metering, BYOK, referrals."""
from larz import Larz, Response
from larz.models import connect
from larz.pricing import Pricing
import larz.money as money, larz.ai as ai, larz.analytics as analytics

app = Larz(secret="change-me-in-prod", debug=True)
connect("@@NAME@@.db")
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin")
ai.enable(app)
analytics.enable(app, token="admin")

(Pricing()
 .plan("free", "$0")
 .plan("pro", "$19/mo", trial_days=7, highlight=True, features=["Unlimited"])
 .credit_pack("starter", price="$10", credit="$12")
 .mount(app))
app.ai.price("@@NAME@@-model", input="$0.50/1M", output="$1.50/1M")


def call_llm(prompt):                 # swap for a real provider
    out = "Draft for: " + prompt
    return out, {"in": len(prompt.split()), "out": len(out.split())}


@app.before
def _welcome(req):
    if not req.session.get("seen"):
        app.money.store.add_credit(req.subject, 25)   # 25c free trial credit
        req.session["seen"] = True


@app.get("/")
def home(req):
    return ("<h1>@@NAME@@</h1><form method=post action=/write>"
            "<input name=prompt> <button>Write</button></form>"
            "<p>%dc credit &middot; <a href='/larz/pricing'>pricing</a></p>"
            % app.money.store.balance(req.subject))


@app.ai_metered("@@NAME@@-model", per_minute=30)
@app.post("/write")
def write(req):
    text, usage = call_llm(req.form.get("prompt", "something"))
    app.ai.charge(req, "@@NAME@@-model", usage["in"], usage["out"])
    return "<p>%s</p><a href='/'>back</a>" % text


if __name__ == "__main__":
    app.run()
'''

_MARKET = '''\
"""A marketplace: listings, per-item checkout, split payouts."""
from larz import Larz, Response
from larz.models import Model, StrField, IntField, connect
import larz.money as money

app = Larz(secret="change-me-in-prod", debug=True)
connect("@@NAME@@.db")
money.enable(app, base_url="http://127.0.0.1:8000")
FEE = 0.15


class Listing(Model):
    title = StrField()
    seller = StrField(index=True)
    price_cents = IntField(default=0)
Listing.create_table()


@app.get("/")
def home(req):
    items = "".join("<li>%s - $%.2f <a href='/buy/%d'>buy</a></li>"
                    % (l.title, l.price_cents / 100, l.id) for l in Listing.all())
    return ("<h1>@@NAME@@</h1><ul>%s</ul>"
            "<form method=post action=/sell><input name=title> "
            "<input name=seller placeholder='seller:you'> "
            "<input name=price placeholder='$9.99'><button>List</button></form>" % items)


@app.post("/sell")
def sell(req):
    cents, _ = money.parse_price(req.form.get("price", "$0"))
    Listing(title=req.form.get("title", "Item"),
            seller=req.form.get("seller", "seller:anon"), price_cents=cents).save()
    return Response.redirect("/")


@app.get("/buy/<id:int>")
def buy(req):
    lst = Listing.get(req.params["id"])
    sku = "listing:%d" % lst.id
    gate = app.money.require(req, sku=sku, cents=lst.price_cents,
                             success_path="/bought/%d" % lst.id)
    return gate or Response.redirect("/bought/%d" % lst.id)


@app.get("/bought/<id:int>")
def bought(req):
    lst = Listing.get(req.params["id"])
    fee = int(lst.price_cents * FEE)
    ref = "%s:listing:%d" % (req.subject, lst.id)
    if not any(p["ref"] == ref for p in app.money.payouts()):
        app.money.split([(lst.seller, lst.price_cents - fee), ("platform", fee)],
                        sku="listing:%d" % lst.id, ref=ref)
    return "<h1>Thanks!</h1><a href='/'>back</a>"


if __name__ == "__main__":
    app.run()
'''

TEMPLATES = {
    "minimal": ("A minimal money-native app (a Pro plan + a paywall)", _MINIMAL),
    "api": ("A paid JSON API (validation, API keys, per-call metering, docs)", _API),
    "saas": ("A SaaS (accounts, pricing-as-code, plan limits, revenue analytics)", _SAAS),
    "ai": ("A monetized AI app (per-token metering, BYOK, credit packs)", _AI),
    "marketplace": ("A two-sided marketplace (listings, checkout, split payouts)", _MARKET),
}


# --------------------------------------------------------------------------- #
def _load_app(path):
    path = path or "app.py"
    if not os.path.isfile(path):
        sys.exit("larz: no such file: %s" % path)
    spec = importlib.util.spec_from_file_location("_larz_user_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = getattr(mod, "app", None)
    if app is None:
        sys.exit("larz: %s defines no `app`" % path)
    return app


def _write(root, rel, content):
    import io
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with io.open(full, "w", encoding="utf-8") as f:
        f.write(content)


def cmd_new(args):
    if "--list" in args:
        print("Available templates:")
        for k, (desc, _) in TEMPLATES.items():
            print("  %-12s %s" % (k, desc))
        return
    template = "minimal"
    positional = []
    i = 0
    while i < len(args):
        if args[i] in ("--template", "-t"):
            template = args[i + 1] if i + 1 < len(args) else "minimal"; i += 2
        else:
            positional.append(args[i]); i += 1
    name = positional[0] if positional else "myapp"
    if template not in TEMPLATES:
        sys.exit("larz: unknown template %r (try `larz new --list`)" % template)
    if os.path.exists(name):
        sys.exit("larz: %s already exists" % name)
    app_code = TEMPLATES[template][1].replace("@@NAME@@", name)
    _write(name, "app.py", app_code)
    _write(name, "requirements.txt", _REQS)
    _write(name, ".env.example", _ENV)
    _write(name, ".gitignore", _GITIGNORE)
    _write(name, "tests/test_app.py", _TEST)
    _write(name, "README.md", _README.replace("@@NAME@@", name).replace("@@TEMPLATE@@", template))
    print("Created %s/ (%s template)" % (name, template))
    print("  cd %s" % name)
    print("  pip install -r requirements.txt")
    print("  python3 app.py        # http://127.0.0.1:8000")


def cmd_run(args):
    _load_app(args[0] if args else None).run(host="127.0.0.1", port=8000)


def cmd_routes(args):
    app = _load_app(args[0] if args else None)
    print("%-10s %-34s %s" % ("METHODS", "PATTERN", "HANDLER"))
    for r in app.routes:
        h = r.handler
        tag = ""
        if getattr(h, "_larz_paid", None): tag = " [paid]"
        elif getattr(h, "_larz_plan", None): tag = " [plan:%s]" % h._larz_plan
        elif getattr(h, "_larz_metered", None): tag = " [metered]"
        print("%-10s %-34s %s%s" % (",".join(sorted(r.methods)), r.pattern,
                                    getattr(h, "__name__", "?"), tag))
    for regex, _, h in getattr(app, "_ws_routes", []):
        print("%-10s %-34s %s" % ("WS", getattr(regex, "pattern", "?"),
                                  getattr(h, "__name__", "?")))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "version":
        print("larz " + __version__)
    elif cmd == "new":
        cmd_new(rest)
    elif cmd == "run":
        cmd_run(rest)
    elif cmd == "routes":
        cmd_routes(rest)
    else:
        sys.exit("larz: unknown command %r (try `larz help`)" % cmd)
    return 0
