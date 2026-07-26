"""
larz.pricing — pricing-as-code. Declare your whole pricing model as one object;
Larz wires up the plans, credit packs, coupons, metered prices, entitlements, and
a real pricing page from it.

    from larz.pricing import Pricing
    import larz.money as money
    money.enable(app, base_url="https://you.com")

    pricing = (Pricing()
        .plan("free", "$0",     limits={"projects": 3})
        .plan("pro",  "$19/mo", trial_days=14, highlight=True,
              features=["API access", "Priority support"],
              limits={"projects": None, "api": True})
        .usage("api_call", "$0.002/call")
        .credit_pack("bulk", price="$20", credit="$25", label="1,000 calls")
        .coupon("LAUNCH50", percent_off=50))
    pricing.mount(app)                 # or: use_pricing(app, pricing)

`mount` registers everything with app.money and installs a styled /larz/pricing
page whose buttons link to /larz/subscribe/<plan>. One object is the single source
of truth for what you charge — the page, the checkout, the entitlements, and the
metered prices all come from it.
"""

from .core import Response
from .money import parse_price

__all__ = ["Pricing", "use_pricing"]


class Pricing:
    def __init__(self, currency="USD"):
        self.currency = currency
        self._plans = []
        self._usages = []
        self._packs = []
        self._coupons = []

    # -- declarations (chainable) ------------------------------------------ #
    def plan(self, name, price, features=None, trial_days=None, limits=None,
             highlight=False, tagline=None):
        self._plans.append(dict(name=name, price=price, features=features or [],
                                trial_days=trial_days, limits=limits or {},
                                highlight=highlight, tagline=tagline))
        return self

    def usage(self, name, price, label=None):
        self._usages.append(dict(name=name, price=price, label=label or name))
        return self

    def credit_pack(self, name, price, credit, label=None):
        self._packs.append(dict(name=name, price=price, credit=credit, label=label))
        return self

    def coupon(self, code, percent_off=0, amount_off=None, days_valid=None,
               max_redemptions=None):
        self._coupons.append(dict(code=code, percent_off=percent_off,
                                  amount_off=amount_off, days_valid=days_valid,
                                  max_redemptions=max_redemptions))
        return self

    # -- apply ------------------------------------------------------------- #
    def mount(self, app):
        return use_pricing(app, self)

    # introspection (handy for tests / your own pages)
    def as_dict(self):
        return {"plans": self._plans, "usages": self._usages,
                "packs": self._packs, "coupons": self._coupons}


def _plan_page(pricing):
    def render(req):
        cards = ""
        for i, p in enumerate(pricing._plans):
            feats = "".join("<li>%s</li>" % f for f in p["features"])
            for k, v in p["limits"].items():
                if v is None:
                    feats += "<li>Unlimited %s</li>" % k
                elif v is True:
                    feats += "<li>%s</li>" % k.replace("_", " ").title()
                elif v:
                    feats += "<li>Up to %s %s</li>" % (v, k)
            trial = ("<div class='pc-trial'>%d-day free trial</div>" % p["trial_days"]) \
                if p["trial_days"] else ""
            cents = parse_price(p["price"])[0]
            cta = ("<span class='pc-cur'>You're on the free plan</span>" if cents == 0
                   else "<a class='pc-btn' href='/larz/subscribe/%s'>Choose %s</a>"
                   % (p["name"], p["name"].title()))
            cards += (
                "<div class='pc-card%s'>%s<h2>%s</h2><div class='pc-price'>%s</div>%s"
                "<ul>%s</ul>%s</div>" % (
                    " pc-hi" if p["highlight"] else "",
                    "<div class='pc-badge'>Most popular</div>" if p["highlight"] else "",
                    p["name"].title(), p["price"],
                    ("<p class='pc-tag'>%s</p>" % p["tagline"]) if p["tagline"] else "",
                    feats, cta))
        css = (
            "body{font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
            "background:#0b0e14;color:#e6eaf2;margin:0;padding:40px 16px}"
            ".pc-wrap{max-width:960px;margin:0 auto;text-align:center}"
            "h1{font-size:34px;margin:0 0 6px}.pc-sub{color:#9aa5b8;margin:0 0 32px}"
            ".pc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));"
            "gap:18px;text-align:left}"
            ".pc-card{position:relative;background:#151a26;border:1px solid #232a3a;"
            "border-radius:16px;padding:24px;display:flex;flex-direction:column}"
            ".pc-hi{border-color:#4ade80;box-shadow:0 12px 40px -18px rgba(74,222,128,.6)}"
            ".pc-badge{position:absolute;top:-11px;left:24px;background:#4ade80;color:#04120d;"
            "font-size:11px;font-weight:800;padding:3px 10px;border-radius:20px}"
            ".pc-card h2{margin:0 0 4px;font-size:20px}"
            ".pc-price{font-size:26px;font-weight:800;color:#4ade80;margin:0 0 10px}"
            ".pc-tag{color:#9aa5b8;font-size:13px;margin:0 0 10px}"
            ".pc-trial{color:#22d3ee;font-size:13px;font-weight:600;margin-bottom:10px}"
            ".pc-card ul{list-style:none;padding:0;margin:0 0 18px;flex:1}"
            ".pc-card li{padding:5px 0 5px 22px;position:relative;font-size:14px}"
            ".pc-card li:before{content:'✓';position:absolute;left:0;color:#4ade80;font-weight:800}"
            ".pc-btn{display:block;text-align:center;background:#4ade80;color:#04120d;"
            "font-weight:800;padding:11px;border-radius:10px;text-decoration:none}"
            ".pc-cur{display:block;text-align:center;color:#9aa5b8;font-size:14px;padding:11px}")
        return ("<!doctype html><meta charset=utf-8><meta name=viewport "
                "content='width=device-width,initial-scale=1'><title>Pricing</title>"
                "<style>%s</style><div class='pc-wrap'><h1>Pricing</h1>"
                "<p class='pc-sub'>Simple, transparent plans.</p>"
                "<div class='pc-grid'>%s</div></div>" % (css, cards))
    return render


def use_pricing(app, pricing):
    """Apply a Pricing object to an app: register plans / packs / coupons / metered
    prices with app.money and install a styled /larz/pricing page."""
    if not getattr(app, "money", None):
        raise RuntimeError("call money.enable(app, ...) before use_pricing(app, ...)")
    m = app.money
    for i, p in enumerate(pricing._plans):
        m.plan(p["name"], p["price"], features=p["features"],
               trial_days=p["trial_days"], limits=p["limits"], rank=i)
    for pk in pricing._packs:
        m.credit_pack(pk["name"], pk["price"], pk["credit"], label=pk["label"])
    for cp in pricing._coupons:
        m.store.add_coupon(cp["code"], percent_off=cp["percent_off"],
                           amount_off_cents=parse_price(cp["amount_off"])[0] if cp["amount_off"] else 0,
                           days_valid=cp["days_valid"], max_redemptions=cp["max_redemptions"])
    for u in pricing._usages:
        cents, unit = parse_price(u["price"])
        m.usages[u["name"]] = (cents, unit)
    m.pricing = pricing
    m.pricing_page = _plan_page(pricing)
    return pricing
