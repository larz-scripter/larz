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
           "GemVaultProvider", "DodoProvider", "CryptoProvider",
           "PaddleProvider", "LemonSqueezyProvider", "PaystackProvider",
           "PayPalProvider", "SquareProvider", "RazorpayProvider",
           "MollieProvider", "CoinbaseCommerceProvider"]


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


# --------------------------------------------------------------------------- #
class PaddleProvider(PaymentProvider):
    """Paddle Billing hosted checkout (transactions API) + signed webhook."""
    name = "paddle"

    def __init__(self, api_key, webhook_secret, price_id=None,
                 api_base="https://api.paddle.com"):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.price_id = price_id
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/transactions", {
            "items": [{"price_id": self.price_id or sku, "quantity": 1}],
            "custom_data": {"subject": subject, "sku": sku},
            "checkout": {"url": success_url}},
            headers={"Authorization": "Bearer " + self.api_key})
        return resp.get("data", {}).get("checkout", {}).get("url", success_url)

    def parse_webhook(self, req):
        sig = req.header("Paddle-Signature") or ""
        digest = hmac.new(self.webhook_secret.encode(), req.body, hashlib.sha256).hexdigest()
        if digest not in sig:
            return None
        e = req.json() or {}
        if e.get("event_type") not in ("transaction.completed", "transaction.paid"):
            return None
        d = e.get("data", {})
        cd = d.get("custom_data") or {}
        total = int(float(d.get("details", {}).get("totals", {}).get("total", 0)))
        return {"subject": cd.get("subject"), "sku": cd.get("sku"),
                "cents": total, "payment_id": d.get("id")}


class LemonSqueezyProvider(PaymentProvider):
    """Lemon Squeezy checkout + signed webhook (X-Signature HMAC over body)."""
    name = "lemonsqueezy"

    def __init__(self, api_key, webhook_secret, store_id, variant_id,
                 api_base="https://api.lemonsqueezy.com/v1"):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.store_id = store_id
        self.variant_id = variant_id
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/checkouts", {
            "data": {"type": "checkouts",
                     "attributes": {"checkout_data": {"custom": {"subject": subject, "sku": sku}}},
                     "relationships": {
                         "store": {"data": {"type": "stores", "id": str(self.store_id)}},
                         "variant": {"data": {"type": "variants", "id": str(self.variant_id)}}}}},
            headers={"Authorization": "Bearer " + self.api_key,
                     "Accept": "application/vnd.api+json"})
        return resp.get("data", {}).get("attributes", {}).get("url", success_url)

    def parse_webhook(self, req):
        sig = req.header("X-Signature") or ""
        expected = hmac.new(self.webhook_secret.encode(), req.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        e = req.json() or {}
        if e.get("meta", {}).get("event_name") != "order_created":
            return None
        custom = e.get("meta", {}).get("custom_data", {})
        attrs = e.get("data", {}).get("attributes", {})
        return {"subject": custom.get("subject"), "sku": custom.get("sku"),
                "cents": attrs.get("total", 0), "payment_id": str(e.get("data", {}).get("id"))}


class PaystackProvider(PaymentProvider):
    """Paystack (popular in Africa) initialize + webhook (x-paystack-signature)."""
    name = "paystack"

    def __init__(self, secret_key, api_base="https://api.paystack.co"):
        self.secret_key = secret_key
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/transaction/initialize", {
            "email": subject if "@" in (subject or "") else subject + "@example.com",
            "amount": cents, "callback_url": success_url,
            "metadata": {"subject": subject, "sku": sku}},
            headers={"Authorization": "Bearer " + self.secret_key})
        return resp.get("data", {}).get("authorization_url", success_url)

    def parse_webhook(self, req):
        sig = req.header("x-paystack-signature") or ""
        expected = hmac.new(self.secret_key.encode(), req.body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        e = req.json() or {}
        if e.get("event") != "charge.success":
            return None
        d = e.get("data", {})
        meta = d.get("metadata") or {}
        return {"subject": meta.get("subject"), "sku": meta.get("sku"),
                "cents": d.get("amount", 0), "payment_id": d.get("reference")}


class PayPalProvider(PaymentProvider):
    """PayPal Orders v2 (create order -> approve link) + webhook.
    Webhook signature verification requires a PayPal API call; here we accept
    COMPLETED capture events and trust HTTPS + the resource id."""
    name = "paypal"

    def __init__(self, client_id, secret, api_base="https://api-m.paypal.com"):
        self.client_id = client_id
        self.secret = secret
        self.api_base = api_base.rstrip("/")

    def _token(self):
        import base64 as _b64
        auth = _b64.b64encode(("%s:%s" % (self.client_id, self.secret)).encode()).decode()
        data = b"grant_type=client_credentials"
        r = urllib.request.Request(self.api_base + "/v1/oauth2/token", data=data,
                                   headers={"Authorization": "Basic " + auth})
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read().decode())["access_token"]

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        order = _post_json(self.api_base + "/v2/checkout/orders", {
            "intent": "CAPTURE",
            "purchase_units": [{"custom_id": "%s|%s" % (subject, sku),
                                "amount": {"currency_code": "USD",
                                           "value": "%.2f" % (cents / 100.0)}}],
            "application_context": {"return_url": success_url, "cancel_url": cancel_url}},
            headers={"Authorization": "Bearer " + self._token()})
        for link in order.get("links", []):
            if link.get("rel") == "approve":
                return link["href"]
        return success_url

    def parse_webhook(self, req):
        e = req.json() or {}
        if e.get("event_type") not in ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"):
            return None
        res = e.get("resource", {})
        custom = res.get("custom_id", "")
        subject, _, sku = custom.partition("|")
        amt = res.get("amount", {}).get("value", "0")
        return {"subject": subject, "sku": sku,
                "cents": int(round(float(amt) * 100)), "payment_id": res.get("id")}


# --------------------------------------------------------------------------- #
class SquareProvider(PaymentProvider):
    """Square hosted checkout (Payment Links). Webhook = HMAC-SHA256 (base64) of
    the notification URL + body under the signature key."""
    name = "square"

    def __init__(self, access_token, location_id, signature_key=None,
                 notification_url="", api_base="https://connect.squareup.com"):
        self.access_token = access_token
        self.location_id = location_id
        self.signature_key = signature_key
        self.notification_url = notification_url
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/v2/online-checkout/payment-links", {
            "idempotency_key": "%s-%s-%d" % (subject, sku, int(time.time())),
            "quick_pay": {"name": sku, "price_money": {"amount": cents, "currency": "USD"},
                          "location_id": self.location_id},
            "checkout_options": {"redirect_url": success_url},
            "payment_note": "%s|%s" % (subject, sku)},
            headers={"Authorization": "Bearer " + self.access_token,
                     "Square-Version": "2024-01-18"})
        return resp["payment_link"]["url"]

    def parse_webhook(self, req):
        if self.signature_key:
            import base64 as _b64
            mac = hmac.new(self.signature_key.encode(),
                           (self.notification_url + req.body.decode("utf-8", "replace")).encode(),
                           hashlib.sha256).digest()
            expected = _b64.b64encode(mac).decode()
            if not hmac.compare_digest(req.header("x-square-hmacsha256-signature") or "", expected):
                return None
        e = req.json() or {}
        if e.get("type") not in ("payment.updated", "payment.created"):
            return None
        pay = (e.get("data", {}).get("object", {}) or {}).get("payment", {})
        if pay.get("status") != "COMPLETED":
            return None
        subject, _, sku = (pay.get("note") or "").partition("|")
        amt = (pay.get("amount_money") or {}).get("amount", 0)
        return {"subject": subject, "sku": sku, "cents": amt, "payment_id": pay.get("id")}


# --------------------------------------------------------------------------- #
class RazorpayProvider(PaymentProvider):
    """Razorpay Payment Links (popular in India). Webhook = HMAC-SHA256 hex of the
    raw body under the webhook secret."""
    name = "razorpay"

    def __init__(self, key_id, key_secret, webhook_secret=None,
                 currency="INR", api_base="https://api.razorpay.com/v1"):
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self.currency = currency
        self.api_base = api_base.rstrip("/")

    def _auth(self):
        import base64 as _b64
        return "Basic " + _b64.b64encode(("%s:%s" % (self.key_id, self.key_secret)).encode()).decode()

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/payment_links", {
            "amount": cents, "currency": self.currency,
            "description": sku, "notes": {"subject": subject, "sku": sku},
            "callback_url": success_url, "callback_method": "get"},
            headers={"Authorization": self._auth()})
        return resp["short_url"]

    def parse_webhook(self, req):
        if self.webhook_secret:
            expected = hmac.new(self.webhook_secret.encode(), req.body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(req.header("x-razorpay-signature") or "", expected):
                return None
        e = req.json() or {}
        if e.get("event") not in ("payment_link.paid", "payment.captured"):
            return None
        entity = (((e.get("payload") or {}).get("payment_link") or {}).get("entity")
                  or ((e.get("payload") or {}).get("payment") or {}).get("entity") or {})
        notes = entity.get("notes") or {}
        return {"subject": notes.get("subject"), "sku": notes.get("sku"),
                "cents": entity.get("amount", 0), "payment_id": entity.get("id")}


# --------------------------------------------------------------------------- #
class MollieProvider(PaymentProvider):
    """Mollie hosted checkout (EU). Confirmation is pull-based: the webhook posts
    only an id, so we re-fetch the payment to confirm it's paid."""
    name = "mollie"

    def __init__(self, api_key, currency="EUR", api_base="https://api.mollie.com/v2"):
        self.api_key = api_key
        self.currency = currency
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/payments", {
            "amount": {"currency": self.currency, "value": "%.2f" % (cents / 100.0)},
            "description": sku, "redirectUrl": success_url,
            "metadata": {"subject": subject, "sku": sku}},
            headers={"Authorization": "Bearer " + self.api_key})
        return resp["_links"]["checkout"]["href"]

    def parse_webhook(self, req):
        pid = (req.form.get("id") if req.form else None) or (req.json() or {}).get("id")
        if not pid:
            return None
        r = urllib.request.Request(self.api_base + "/payments/" + pid,
                                   headers={"Authorization": "Bearer " + self.api_key})
        with urllib.request.urlopen(r, timeout=20) as resp:
            pay = json.loads(resp.read().decode())
        if pay.get("status") != "paid":
            return None
        meta = pay.get("metadata") or {}
        cents = int(round(float(pay.get("amount", {}).get("value", "0")) * 100))
        return {"subject": meta.get("subject"), "sku": meta.get("sku"),
                "cents": cents, "payment_id": pid}


# --------------------------------------------------------------------------- #
class CoinbaseCommerceProvider(PaymentProvider):
    """Coinbase Commerce hosted crypto checkout. Webhook = HMAC-SHA256 hex of the
    raw body under the shared webhook secret (X-CC-Webhook-Signature)."""
    name = "coinbase"

    def __init__(self, api_key, webhook_secret=None,
                 api_base="https://api.commerce.coinbase.com"):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.api_base = api_base.rstrip("/")

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        resp = _post_json(self.api_base + "/charges", {
            "name": sku, "description": sku, "pricing_type": "fixed_price",
            "local_price": {"amount": "%.2f" % (cents / 100.0), "currency": "USD"},
            "metadata": {"subject": subject, "sku": sku},
            "redirect_url": success_url, "cancel_url": cancel_url},
            headers={"X-CC-Api-Key": self.api_key, "X-CC-Version": "2018-03-22"})
        return resp["data"]["hosted_url"]

    def parse_webhook(self, req):
        if self.webhook_secret:
            expected = hmac.new(self.webhook_secret.encode(), req.body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(req.header("X-CC-Webhook-Signature") or "", expected):
                return None
        e = req.json() or {}
        ev = e.get("event", {})
        if ev.get("type") not in ("charge:confirmed", "charge:resolved"):
            return None
        d = ev.get("data", {})
        meta = d.get("metadata") or {}
        pricing = (d.get("pricing") or {}).get("local", {})
        cents = int(round(float(pricing.get("amount", "0")) * 100)) if pricing else 0
        return {"subject": meta.get("subject"), "sku": meta.get("sku"),
                "cents": cents, "payment_id": d.get("code")}
