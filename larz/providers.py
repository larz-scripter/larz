"""
larz.providers — pluggable payment backends.

A provider implements two methods:

    create_checkout(subject, sku, cents, success_url, cancel_url) -> checkout_url
    parse_webhook(req) -> {subject, sku, cents, payment_id} | None

That's the whole contract. Ships with:

    MockProvider    keyless, fully local — the dev/test default.
    StripeProvider  real Stripe Checkout via stdlib urllib (no `stripe` SDK).
    GemVaultProvider / DodoProvider   estate rails (hosted checkout + webhook).
    CryptoProvider  address-based crypto, confirmed by webhook.

None of these pull a third-party dependency: everything goes over urllib.
"""

import time
import json
import hmac
import hashlib
import urllib.parse
import urllib.request

__all__ = ["PaymentProvider", "MockProvider", "StripeProvider",
           "GemVaultProvider", "DodoProvider", "CryptoProvider"]


class PaymentProvider:
    name = "base"

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        raise NotImplementedError

    def parse_webhook(self, req):
        raise NotImplementedError


def _post_form(url, fields, headers=None):
    data = urllib.parse.urlencode(fields, doseq=True).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _post_json(url, obj, headers=None):
    data = json.dumps(obj).encode()
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------- #
class MockProvider(PaymentProvider):
    """Local, keyless provider so apps run end-to-end with no external service.
    'Checkout' is a signed local URL; visiting it simulates a completed payment.
    Dev/test only."""
    name = "mock"

    def __init__(self, secret="mock"):
        self.secret = secret.encode()

    def _sig(self, subject, sku, cents):
        raw = ("%s|%s|%d" % (subject, sku, cents)).encode()
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()[:16]

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        q = urllib.parse.urlencode({
            "subject": subject, "sku": sku, "cents": cents,
            "sig": self._sig(subject, sku, cents), "next": success_url})
        return "/larz/checkout/mock?" + q

    def confirm(self, req):
        q = req.query
        subject, sku, cents = q.get("subject"), q.get("sku"), int(q.get("cents", 0))
        if self._sig(subject, sku, cents) != q.get("sig"):
            return None
        return {"subject": subject, "sku": sku, "cents": cents,
                "payment_id": "mock_%d" % int(time.time()), "next": q.get("next", "/")}


# --------------------------------------------------------------------------- #
class StripeProvider(PaymentProvider):
    """Real Stripe Checkout via REST + stdlib urllib. No SDK dependency."""
    name = "stripe"

    def __init__(self, api_key, webhook_secret=None):
        self.api_key = api_key
        self.webhook_secret = webhook_secret

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        session = _post_form("https://api.stripe.com/v1/checkout/sessions", {
            "mode": "payment", "success_url": success_url, "cancel_url": cancel_url,
            "client_reference_id": subject, "metadata[sku]": sku,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][product_data][name]": sku,
            "line_items[0][price_data][unit_amount]": cents,
            "line_items[0][quantity]": 1,
        }, headers={"Authorization": "Bearer " + self.api_key})
        return session["url"]

    def _verify_sig(self, req):
        if not self.webhook_secret:
            return True
        header = req.header("Stripe-Signature") or ""
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        t, v1 = parts.get("t"), parts.get("v1")
        if not (t and v1):
            return False
        signed = ("%s." % t).encode() + req.body
        expected = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)

    def parse_webhook(self, req):
        if not self._verify_sig(req):
            return None
        event = req.json() or {}
        if event.get("type") != "checkout.session.completed":
            return None
        obj = event["data"]["object"]
        return {"subject": obj.get("client_reference_id"),
                "sku": (obj.get("metadata") or {}).get("sku"),
                "cents": obj.get("amount_total", 0), "payment_id": obj.get("id")}


# --------------------------------------------------------------------------- #
class GemVaultProvider(PaymentProvider):
    """Estate rail: GemVault multi-merchant hub (card via Dodo + crypto).

    Verified against the estate's live GemVault `mkt.py`:
      * create : POST {api}/api/mkt/dodo/checkout  (token-authed, server-to-server)
                 body {app, uid, amount, return_url} -> {checkout_url}
                 (`app` must be in GemVault's DODO_ALLOWED_APPS allowlist)
      * webhook: X-GV-Signature = HMAC-SHA256(raw_body, secret) hex,
                 body carries {uid, usd_amount, tx_hash|session_id}

    GemVault only round-trips `uid`, so Larz packs `subject|sku` into it and
    unpacks on the way back — giving the framework its (subject, sku) grant."""
    name = "gemvault"

    def __init__(self, app, api_base, token, secret):
        self.app_name = app                 # your GemVault merchant/app id
        self.api_base = api_base.rstrip("/")
        self.token = token                  # server-to-server auth token
        self.secret = secret                # webhook HMAC secret

    @staticmethod
    def _pack(subject, sku):
        return "%s|%s" % (subject, sku)

    @staticmethod
    def _unpack(uid):
        subject, _, sku = (uid or "").partition("|")
        return subject, sku

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/api/mkt/dodo/checkout", {
            "app": self.app_name,
            "uid": self._pack(subject, sku),
            "amount": round(cents / 100.0, 2),
            "return_url": success_url},
            headers={"Authorization": "Bearer " + self.token})
        return resp["checkout_url"]

    def parse_webhook(self, req):
        sig = req.header("X-GV-Signature") or ""
        expected = hmac.new(self.secret.encode(), req.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        e = req.json() or {}
        subject, sku = self._unpack(e.get("uid"))
        pid = e.get("tx_hash") or e.get("session_id") or e.get("id")
        return {"subject": subject, "sku": sku,
                "cents": int(round(float(e.get("usd_amount", 0)) * 100)),
                "payment_id": pid}


# --------------------------------------------------------------------------- #
class DodoProvider(PaymentProvider):
    """Estate rail: Dodo Payments card provider (X-GV-Signature-style webhook)."""
    name = "dodo"

    def __init__(self, api_key, webhook_secret, api_base="https://api.dodopayments.com"):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/checkouts", {
            "reference_id": subject, "metadata": {"sku": sku},
            "amount": cents, "currency": "USD",
            "return_url": success_url, "cancel_url": cancel_url},
            headers={"Authorization": "Bearer " + self.api_key})
        return resp["checkout_url"]

    def parse_webhook(self, req):
        sig = req.header("X-Dodo-Signature") or ""
        expected = hmac.new(self.webhook_secret.encode(), req.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        e = req.json() or {}
        if e.get("type") != "payment.succeeded":
            return None
        d = e.get("data", {})
        return {"subject": d.get("reference_id"), "sku": (d.get("metadata") or {}).get("sku"),
                "cents": d.get("amount", 0), "payment_id": d.get("id")}


# --------------------------------------------------------------------------- #
class CryptoProvider(PaymentProvider):
    """Address-based crypto: hands the buyer a pay-to address; a watcher webhook
    confirms on-chain settlement. Provider-agnostic (NOWPayments-style body)."""
    name = "crypto"

    def __init__(self, api_key, ipn_secret, api_base="https://api.nowpayments.io/v1"):
        self.api_key = api_key
        self.ipn_secret = ipn_secret
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/invoice", {
            "price_amount": round(cents / 100.0, 2), "price_currency": "usd",
            "order_id": "%s:%s" % (subject, sku),
            "success_url": success_url, "cancel_url": cancel_url},
            headers={"x-api-key": self.api_key})
        return resp["invoice_url"]

    def parse_webhook(self, req):
        sig = req.header("x-nowpayments-sig") or ""
        expected = hmac.new(self.ipn_secret.encode(), req.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        e = req.json() or {}
        if e.get("payment_status") not in ("finished", "confirmed"):
            return None
        subject, _, sku = (e.get("order_id") or "").partition(":")
        return {"subject": subject, "sku": sku,
                "cents": int(round(float(e.get("price_amount", 0)) * 100)),
                "payment_id": str(e.get("payment_id"))}
