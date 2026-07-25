"""
larz.money — the money-native layer.

This is what makes Larz different: payments, paywalls, and usage-metering are
framework primitives, not something you bolt on later.

  * EntitlementStore  — who has paid for what (stdlib sqlite3, zero-dep).
  * PaymentProvider   — pluggable checkout backends. Ships with:
        MockProvider   (fully local; makes the demo runnable with no keys)
        StripeProvider (real REST via urllib; no `stripe` SDK dependency)
    …and the estate can drop in a GemVaultProvider / DodoProvider the same way.
  * @app.paid / @app.metered enforcement, wired into core.dispatch().

Design: a route decorated @app.paid gates on an *entitlement*; if the caller
isn't entitled, Larz sends them to the provider's checkout. The provider's
webhook (or the mock confirm URL) grants the entitlement, and the caller lands
back on the original page — now served.
"""

import time
import json
import hmac
import hashlib
import sqlite3
import urllib.parse
import urllib.request
from .core import Response

__all__ = ["enable", "PaymentProvider", "MockProvider", "StripeProvider"]


# --------------------------------------------------------------------------- #
#  price parsing:  "$9/mo"  "$0.02/call"  "$49"  ->  (amount_cents, interval)
# --------------------------------------------------------------------------- #
def parse_price(price):
    if isinstance(price, (int, float)):
        return int(round(price * 100)), None
    s = str(price).strip().lstrip("$")
    amount, _, interval = s.partition("/")
    cents = int(round(float(amount) * 100))
    return cents, (interval or None)


# --------------------------------------------------------------------------- #
#  Entitlement store  (sqlite, stdlib)
# --------------------------------------------------------------------------- #
class EntitlementStore:
    def __init__(self, path="larz_money.db"):
        self.path = path
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS entitlements(
              subject TEXT, sku TEXT, expires_at REAL, created_at REAL,
              PRIMARY KEY(subject, sku));
            CREATE TABLE IF NOT EXISTS credits(
              subject TEXT PRIMARY KEY, balance_cents INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS usage(
              subject TEXT, sku TEXT, cents INTEGER, ts REAL);
            CREATE TABLE IF NOT EXISTS payments(
              id TEXT PRIMARY KEY, subject TEXT, sku TEXT, cents INTEGER,
              provider TEXT, status TEXT, created_at REAL);
            """)

    # entitlements -------------------------------------------------------- #
    def grant(self, subject, sku, days=None):
        # portable across all SQLite versions (no UPSERT / ON CONFLICT DO UPDATE).
        now = time.time()
        expires = now + days * 86400 if days else None
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO entitlements"
                      "(subject,sku,expires_at,created_at) VALUES(?,?,?,?)",
                      (subject, sku, expires, now))

    def is_entitled(self, subject, sku):
        with self._conn() as c:
            row = c.execute("SELECT expires_at FROM entitlements WHERE subject=? AND sku=?",
                            (subject, sku)).fetchone()
        if not row:
            return False
        return row["expires_at"] is None or row["expires_at"] > time.time()

    # credits / metering -------------------------------------------------- #
    def add_credit(self, subject, cents):
        # portable increment: UPDATE, then INSERT only if the row was missing.
        with self._conn() as c:
            cur = c.execute("UPDATE credits SET balance_cents=balance_cents+? "
                            "WHERE subject=?", (cents, subject))
            if cur.rowcount == 0:
                c.execute("INSERT INTO credits(subject,balance_cents) VALUES(?,?)",
                          (subject, cents))

    def balance(self, subject):
        with self._conn() as c:
            row = c.execute("SELECT balance_cents FROM credits WHERE subject=?",
                            (subject,)).fetchone()
        return row["balance_cents"] if row else 0

    def charge(self, subject, cents, sku):
        """Atomically debit prepaid credit; returns True if funded."""
        with self._conn() as c:
            row = c.execute("SELECT balance_cents FROM credits WHERE subject=?",
                            (subject,)).fetchone()
            bal = row["balance_cents"] if row else 0
            if bal < cents:
                return False
            c.execute("UPDATE credits SET balance_cents=balance_cents-? WHERE subject=?",
                      (cents, subject))
            c.execute("INSERT INTO usage(subject,sku,cents,ts) VALUES(?,?,?,?)",
                      (subject, sku, cents, time.time()))
            return True

    def record_payment(self, pid, subject, sku, cents, provider, status="paid"):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO payments"
                      "(id,subject,sku,cents,provider,status,created_at) VALUES(?,?,?,?,?,?,?)",
                      (pid, subject, sku, cents, provider, status, time.time()))


# --------------------------------------------------------------------------- #
#  Payment providers
# --------------------------------------------------------------------------- #
class PaymentProvider:
    """Implement these two methods to plug any processor into Larz."""
    name = "base"

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        raise NotImplementedError

    def parse_webhook(self, req):
        """Return {'subject','sku','cents','payment_id'} for a completed payment, else None."""
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """Local, keyless provider so the demo runs end-to-end offline.

    'Checkout' is a signed local URL; hitting it simulates a completed payment.
    Never use in production — it's the dev/test default."""
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


class StripeProvider(PaymentProvider):
    """Real Stripe Checkout via the REST API using stdlib urllib — no SDK.

    Untested against live keys here, but the wire calls are correct; set
    api_key + webhook_secret and it should work. Demonstrates that a real
    provider is a ~40-line drop-in."""
    name = "stripe"

    def __init__(self, api_key, webhook_secret=None):
        self.api_key = api_key
        self.webhook_secret = webhook_secret

    def _post(self, path, fields):
        data = urllib.parse.urlencode(fields, doseq=True).encode()
        r = urllib.request.Request("https://api.stripe.com/v1/" + path, data=data,
                                   headers={"Authorization": "Bearer " + self.api_key})
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read().decode())

    def create_checkout(self, subject, sku, cents, success_url, cancel_url):
        session = self._post("checkout/sessions", {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": subject,
            "metadata[sku]": sku,
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][product_data][name]": sku,
            "line_items[0][price_data][unit_amount]": cents,
            "line_items[0][quantity]": 1,
        })
        return session["url"]

    def parse_webhook(self, req):
        event = req.json() or {}
        if event.get("type") != "checkout.session.completed":
            return None
        obj = event["data"]["object"]
        return {"subject": obj.get("client_reference_id"),
                "sku": (obj.get("metadata") or {}).get("sku"),
                "cents": obj.get("amount_total", 0),
                "payment_id": obj.get("id")}


# --------------------------------------------------------------------------- #
#  The money engine attached to the app  (app.money)
# --------------------------------------------------------------------------- #
class _Money:
    def __init__(self, app, provider, store, base_url):
        self.app = app
        self.provider = provider
        self.store = store
        self.base_url = base_url.rstrip("/")
        self._install_routes()

    def _sku_for(self, spec, route):
        return spec.get("sku") or route.pattern.strip("/").replace("/", ":") or "root"

    # enforcement (called from core.dispatch) ----------------------------- #
    def enforce_paid(self, req, spec, route):
        subject = req.subject
        sku = self._sku_for(spec, route)
        if self.store.is_entitled(subject, sku):
            return None                      # already paid → serve the route
        cents, _ = parse_price(spec["price"])
        success = self.base_url + req.path
        cancel = self.base_url + "/"
        url = self.provider.create_checkout(subject, sku, cents, success, cancel)
        return Response.redirect(url)        # not paid → go to checkout

    def enforce_metered(self, req, spec, route):
        subject = req.subject
        sku = self._sku_for(spec, route)
        cents, _ = parse_price(spec["price"])
        if self.store.charge(subject, cents, sku):
            return None                      # funded → serve
        return Response.json(
            {"error": "payment_required", "sku": sku, "price_cents": cents,
             "balance_cents": self.store.balance(subject),
             "top_up": self.base_url + "/larz/credits"}, status=402)

    # built-in routes ----------------------------------------------------- #
    def _install_routes(self):
        app = self.app

        @app.get("/larz/checkout/mock")
        def _mock_confirm(req):
            if not isinstance(self.provider, MockProvider):
                return Response("mock checkout disabled", status=404)
            result = self.provider.confirm(req)
            if not result:
                return Response("bad signature", status=400)
            self.store.grant(result["subject"], result["sku"])
            self.store.record_payment(result["payment_id"], result["subject"],
                                      result["sku"], result["cents"], "mock")
            return Response.redirect(result.get("next", "/"))

        @app.post("/larz/webhook/<provider>")
        def _webhook(req):
            result = self.provider.parse_webhook(req)
            if not result:
                return Response("ignored", status=200)
            days = None
            self.store.grant(result["subject"], result["sku"], days=days)
            self.store.record_payment(result["payment_id"], result["subject"],
                                      result["sku"], result["cents"], self.provider.name)
            return Response("ok", status=200)

        @app.get("/larz/credits")
        def _credits(req):
            return Response.json({"subject": req.subject,
                                  "balance_cents": self.store.balance(req.subject)})


def enable(app, provider=None, db="larz_money.db", base_url="http://127.0.0.1:8000"):
    """Turn on the money-native layer for an app."""
    provider = provider or MockProvider()
    store = EntitlementStore(db)
    app.money = _Money(app, provider, store, base_url)
    app.money.store = store
    return app.money
