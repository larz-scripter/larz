"""
larz.referrals — referrals & affiliates as a primitive. Built on the 1.1 payout
ledger, so commissions are tracked the same way seller payouts are.

    import larz.referrals as referrals
    referrals.enable(app, reward="20%")     # requires money.enable(app, ...)

    # give a user their referral link
    url = app.referrals.url(req.user_subject, base="https://you.com")

    # capture ?ref=CODE on any landing page (e.g. in a before-hook)
    @app.before
    def _ref(req): app.referrals.capture(req)

    # when a new account is created, bind them to whoever referred them
    app.referrals.attribute(new_subject, req=req)

When a referred user pays, `reward` (a percent like "20%" or a flat "$5") is
credited to the referrer's payout ledger automatically. Read progress with
app.referrals.stats(subject).
"""

import secrets

from .core import Response
from .models import Model, StrField, IntField, BoolField, DateTimeField
from .money import parse_price

__all__ = ["enable"]


class Referral(Model):
    code = StrField(unique=True, index=True)
    owner = StrField(index=True)          # referrer subject
    created = DateTimeField(auto_now=True)


class ReferralAttribution(Model):
    referred = StrField(unique=True, index=True)   # one referrer per referred subject
    referrer = StrField(index=True)
    code = StrField(default="")
    commissioned = BoolField(default=False)
    created = DateTimeField(auto_now=True)


class _Referrals:
    def __init__(self, app, reward, cookie, first_only):
        self.app = app
        self.reward = reward
        self.cookie = cookie
        self.first_only = first_only
        Referral.create_table()
        ReferralAttribution.create_table()

    # -- referrer side ----------------------------------------------------- #
    def code_for(self, subject):
        row = Referral.where(owner=subject).first()
        if row:
            return row.code
        code = secrets.token_urlsafe(6)
        while Referral.where(code=code).first():
            code = secrets.token_urlsafe(6)
        Referral(code=code, owner=subject).save()
        return code

    link = code_for                      # alias

    def url(self, subject, base=""):
        base = (base or self.app.money.base_url).rstrip("/")
        return "%s/?ref=%s" % (base, self.code_for(subject))

    def owner_of(self, code):
        row = Referral.where(code=code).first()
        return row.owner if row else None

    # -- visitor / referred side ------------------------------------------- #
    def capture(self, req):
        """Store a ?ref=CODE from the URL in the session for later attribution."""
        code = req.query.get("ref")
        if code and self.owner_of(code):
            req.session[self.cookie] = code
        return req.session.get(self.cookie)

    def attribute(self, referred_subject, req=None, code=None):
        """Bind a (new) referred subject to their referrer, once. Uses `code` or
        the code captured in the session. No self-referrals, no re-binding."""
        code = code or (req.session.get(self.cookie) if req is not None else None)
        if not code:
            return None
        referrer = self.owner_of(code)
        if not referrer or referrer == referred_subject:
            return None
        if ReferralAttribution.where(referred=referred_subject).first():
            return None
        ReferralAttribution(referred=referred_subject, referrer=referrer, code=code).save()
        return referrer

    # -- commission (wired to money.on_payment) ---------------------------- #
    def _commission(self, subject, sku, cents):
        att = ReferralAttribution.where(referred=subject).first()
        if not att or att.referrer == subject:
            return
        if self.first_only and att.commissioned:
            return
        reward = self.reward
        if isinstance(reward, str) and reward.strip().endswith("%"):
            amount = int(round(cents * float(reward.strip().rstrip("%")) / 100.0))
        else:
            amount = parse_price(reward)[0]
        if amount <= 0:
            return
        self.app.money.store.record_payout(att.referrer, amount, sku="referral",
                                           ref="ref:%s:%s" % (subject, sku))
        att.commissioned = True
        att.save()

    # -- reporting --------------------------------------------------------- #
    def stats(self, subject):
        refs = ReferralAttribution.where(referrer=subject).all()
        earned = sum(p["cents"] for p in self.app.money.payouts(subject)
                     if p["sku"] == "referral")
        return {"code": self.code_for(subject),
                "referrals": len(refs),
                "converted": sum(1 for r in refs if r.commissioned),
                "earned_cents": earned,
                "owed_cents": self.app.money.owed(subject)}


def enable(app, reward="20%", cookie="ref", first_only=True):
    if not getattr(app, "money", None):
        raise RuntimeError("call money.enable(app, ...) before referrals.enable(app)")
    mgr = _Referrals(app, reward, cookie, first_only)
    app.referrals = mgr
    app.money.on_payment(mgr._commission)     # auto-credit referrers on payment
    return mgr
