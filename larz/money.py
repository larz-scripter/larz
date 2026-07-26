"""
larz.money — the money-native layer.

What makes Larz different: payments, paywalls, subscriptions, trials, coupons,
usage-metering, credit packs, and a revenue dashboard are framework primitives.

    money.enable(app, provider=StripeProvider(...), base_url="https://you.com")

    @app.paid("$9")                       one-off unlock
    @app.paid("$9/mo", trial_days=7)      subscription with a free trial
    @app.plan("pro")                      gate on a named plan (app.money.plan)
    @app.metered("$0.02/call")            per-call billing from prepaid credit

Built-in routes (auto-registered):
    /larz/pricing              plans + credit packs, with checkout links
    /larz/credits              balance + buyable credit packs
    /larz/checkout/mock        dev checkout (MockProvider)
    /larz/webhook/<provider>   payment webhook -> grant entitlement / add credit
    /larz/admin?token=...      revenue dashboard (MRR, sales, usage)
"""

import time
import sqlite3
from .core import Response
# providers live in their own module now; re-export for backwards-compat.
from .providers import (PaymentProvider, MockProvider, StripeProvider,      # noqa
                        GemVaultProvider, DodoProvider, CryptoProvider)

__all__ = ["enable", "parse_price", "EntitlementStore",
           "PaymentProvider", "MockProvider", "StripeProvider",
           "GemVaultProvider", "DodoProvider", "CryptoProvider"]

_INTERVAL_DAYS = {"day": 1, "wk": 7, "week": 7, "mo": 30, "month": 30,
                  "yr": 365, "year": 365}


def _fmt_ts(ts):
    if not ts:
        return "—"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def parse_price(price):
    """'$9/mo' -> (900, 'mo');  '$0.02/call' -> (2, 'call');  4.5 -> (450, None)."""
    if isinstance(price, (int, float)):
        return int(round(price * 100)), None
    s = str(price).strip().lstrip("$")
    amount, _, interval = s.partition("/")
    return int(round(float(amount) * 100)), (interval or None)


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
            CREATE TABLE IF NOT EXISTS trials(
              subject TEXT, sku TEXT, started_at REAL, PRIMARY KEY(subject, sku));
            CREATE TABLE IF NOT EXISTS coupons(
              code TEXT PRIMARY KEY, percent_off INTEGER DEFAULT 0,
              amount_off_cents INTEGER DEFAULT 0, expires_at REAL,
              max_redemptions INTEGER, redemptions INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sku_meta(sku TEXT PRIMARY KEY, interval TEXT);
            CREATE TABLE IF NOT EXISTS invoices(
              id TEXT PRIMARY KEY, subject TEXT, sku TEXT, cents INTEGER,
              provider TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS payouts(
              id INTEGER PRIMARY KEY AUTOINCREMENT, party TEXT, sku TEXT,
              cents INTEGER, ref TEXT, status TEXT DEFAULT 'pending',
              created_at REAL, paid_at REAL);
            CREATE TABLE IF NOT EXISTS dunning(
              subject TEXT, sku TEXT, first_failed REAL, attempts INTEGER DEFAULT 0,
              last_notified REAL, status TEXT DEFAULT 'retrying',
              PRIMARY KEY(subject, sku));
            """)

    # subscriptions / dunning --------------------------------------------- #
    def active_entitlements(self):
        """All currently-valid entitlements (subject, sku, expires_at, created_at)."""
        now = time.time()
        with self._conn() as c:
            rows = c.execute("SELECT subject, sku, expires_at, created_at FROM entitlements "
                             "WHERE expires_at IS NULL OR expires_at > ?", (now,)).fetchall()
        return [dict(r) for r in rows]

    def expired_subscriptions(self):
        """Recurring subscriptions whose term has lapsed and were not renewed —
        i.e. a plan sku (interval still set, non-empty) past its expires_at."""
        now = time.time()
        with self._conn() as c:
            rows = c.execute(
                "SELECT e.subject, e.sku, e.expires_at FROM entitlements e "
                "JOIN sku_meta m ON m.sku = e.sku "
                "WHERE m.interval IS NOT NULL AND m.interval != '' "
                "AND e.expires_at IS NOT NULL AND e.expires_at < ?", (now,)).fetchall()
        return [dict(r) for r in rows]

    def dunning_get(self, subject, sku):
        with self._conn() as c:
            row = c.execute("SELECT * FROM dunning WHERE subject=? AND sku=?",
                            (subject, sku)).fetchone()
        return dict(row) if row else None

    def dunning_upsert(self, subject, sku, first_failed, attempts, status="retrying"):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO dunning"
                      "(subject,sku,first_failed,attempts,last_notified,status)"
                      " VALUES(?,?,?,?,?,?)",
                      (subject, sku, first_failed, attempts, time.time(), status))

    def dunning_clear(self, subject, sku):
        with self._conn() as c:
            c.execute("DELETE FROM dunning WHERE subject=? AND sku=?", (subject, sku))

    # marketplace payouts / split ledger ---------------------------------- #
    def record_payout(self, party, cents, sku=None, ref=None):
        with self._conn() as c:
            cur = c.execute("INSERT INTO payouts(party,sku,cents,ref,status,created_at)"
                            " VALUES(?,?,?,?,'pending',?)",
                            (party, sku, int(cents), ref, time.time()))
            return cur.lastrowid

    def list_payouts(self, party=None, status=None):
        q = "SELECT * FROM payouts"
        conds, args = [], []
        if party:
            conds.append("party=?"); args.append(party)
        if status:
            conds.append("status=?"); args.append(status)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def payout_owed(self, party):
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(SUM(cents),0) s FROM payouts "
                            "WHERE party=? AND status='pending'", (party,)).fetchone()
        return row["s"]

    def mark_payout_paid(self, payout_id):
        with self._conn() as c:
            c.execute("UPDATE payouts SET status='paid', paid_at=? WHERE id=?",
                      (time.time(), payout_id))

    def payout_totals(self):
        with self._conn() as c:
            rows = c.execute(
                "SELECT party, COALESCE(SUM(CASE WHEN status='pending' THEN cents END),0) owed, "
                "COALESCE(SUM(cents),0) total FROM payouts GROUP BY party "
                "ORDER BY owed DESC").fetchall()
        return [dict(r) for r in rows]

    # invoices ------------------------------------------------------------ #
    def record_invoice(self, iid, subject, sku, cents, provider):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO invoices"
                      "(id,subject,sku,cents,provider,created_at) VALUES(?,?,?,?,?,?)",
                      (iid, subject, sku, cents, provider, time.time()))

    def list_invoices(self, subject):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM invoices WHERE subject=? "
                             "ORDER BY created_at DESC", (subject,)).fetchall()
        return [dict(r) for r in rows]

    def get_invoice(self, iid):
        with self._conn() as c:
            row = c.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        return dict(row) if row else None

    # sku metadata (so the webhook knows a sku's billing interval) --------- #
    def set_interval(self, sku, interval):
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO sku_meta(sku,interval) VALUES(?,?)",
                      (sku, interval))

    def get_interval(self, sku):
        with self._conn() as c:
            row = c.execute("SELECT interval FROM sku_meta WHERE sku=?", (sku,)).fetchone()
        return row["interval"] if row else None

    # entitlements -------------------------------------------------------- #
    def grant(self, subject, sku, days=None):
        now = time.time()
        expires = now + days * 86400 if days else None
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO entitlements"
                      "(subject,sku,expires_at,created_at) VALUES(?,?,?,?)",
                      (subject, sku, expires, now))

    def revoke(self, subject, sku):
        with self._conn() as c:
            c.execute("DELETE FROM entitlements WHERE subject=? AND sku=?", (subject, sku))

    def is_entitled(self, subject, sku):
        with self._conn() as c:
            row = c.execute("SELECT expires_at FROM entitlements WHERE subject=? AND sku=?",
                            (subject, sku)).fetchone()
        if not row:
            return False
        return row["expires_at"] is None or row["expires_at"] > time.time()

    def expires_at(self, subject, sku):
        with self._conn() as c:
            row = c.execute("SELECT expires_at FROM entitlements WHERE subject=? AND sku=?",
                            (subject, sku)).fetchone()
        return row["expires_at"] if row else None

    # trials -------------------------------------------------------------- #
    def trial_available(self, subject, sku):
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM trials WHERE subject=? AND sku=?",
                            (subject, sku)).fetchone()
        return row is None

    def start_trial(self, subject, sku, days):
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO trials(subject,sku,started_at) VALUES(?,?,?)",
                      (subject, sku, time.time()))
        self.grant(subject, sku, days=days)

    # credits / metering -------------------------------------------------- #
    def add_credit(self, subject, cents):
        with self._conn() as c:
            cur = c.execute("UPDATE credits SET balance_cents=balance_cents+? WHERE subject=?",
                            (cents, subject))
            if cur.rowcount == 0:
                c.execute("INSERT INTO credits(subject,balance_cents) VALUES(?,?)",
                          (subject, cents))

    def balance(self, subject):
        with self._conn() as c:
            row = c.execute("SELECT balance_cents FROM credits WHERE subject=?",
                            (subject,)).fetchone()
        return row["balance_cents"] if row else 0

    def charge(self, subject, cents, sku):
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

    # coupons ------------------------------------------------------------- #
    def add_coupon(self, code, percent_off=0, amount_off_cents=0,
                   days_valid=None, max_redemptions=None):
        expires = time.time() + days_valid * 86400 if days_valid else None
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO coupons"
                      "(code,percent_off,amount_off_cents,expires_at,max_redemptions,redemptions)"
                      " VALUES(?,?,?,?,?,COALESCE((SELECT redemptions FROM coupons WHERE code=?),0))",
                      (code, percent_off, amount_off_cents, expires, max_redemptions, code))

    def apply_coupon(self, code, cents):
        """Return (discounted_cents, ok). Does not consume a redemption."""
        if not code:
            return cents, False
        with self._conn() as c:
            row = c.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
        if not row:
            return cents, False
        if row["expires_at"] and row["expires_at"] < time.time():
            return cents, False
        if (row["max_redemptions"] is not None
                and row["redemptions"] >= row["max_redemptions"]):
            return cents, False
        out = cents
        if row["percent_off"]:
            out = int(round(out * (100 - row["percent_off"]) / 100.0))
        out -= row["amount_off_cents"] or 0
        return max(0, out), True

    def redeem_coupon(self, code):
        with self._conn() as c:
            c.execute("UPDATE coupons SET redemptions=redemptions+1 WHERE code=?", (code,))

    def revenue_series(self, days=30):
        """Daily paid revenue (cents) for the last `days` days, oldest first."""
        now = time.time()
        start = now - days * 86400
        buckets = [0] * days
        with self._conn() as c:
            rows = c.execute("SELECT cents, created_at FROM payments "
                             "WHERE status='paid' AND created_at >= ?", (start,)).fetchall()
        for r in rows:
            idx = int((r["created_at"] - start) // 86400)
            if 0 <= idx < days:
                buckets[idx] += r["cents"]
        return buckets

    # analytics ----------------------------------------------------------- #
    def stats(self):
        with self._conn() as c:
            rev = c.execute("SELECT COALESCE(SUM(cents),0) s, COUNT(*) n FROM payments "
                            "WHERE status='paid'").fetchone()
            active_subs = c.execute(
                "SELECT COUNT(*) n FROM entitlements WHERE expires_at IS NOT NULL "
                "AND expires_at > ?", (time.time(),)).fetchone()["n"]
            outstanding = c.execute("SELECT COALESCE(SUM(balance_cents),0) s "
                                    "FROM credits").fetchone()["s"]
            recent = c.execute("SELECT id,subject,sku,cents,provider,created_at "
                               "FROM payments WHERE status='paid' "
                               "ORDER BY created_at DESC LIMIT 10").fetchall()
            top_usage = c.execute("SELECT sku, COUNT(*) calls, COALESCE(SUM(cents),0) cents "
                                  "FROM usage GROUP BY sku ORDER BY cents DESC LIMIT 10").fetchall()
        return {"revenue_cents": rev["s"], "payments": rev["n"],
                "active_subscriptions": active_subs,
                "outstanding_credit_cents": outstanding,
                "recent": [dict(r) for r in recent],
                "top_usage": [dict(r) for r in top_usage]}


# --------------------------------------------------------------------------- #
class _Money:
    def __init__(self, app, provider, store, base_url, admin_token=None):
        self.app = app
        self.provider = provider
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.plans = {}        # name -> {sku, cents, interval, features, trial_days}
        self.packs = {}        # name -> {price_cents, credit_cents, label}
        self.usages = {}       # name -> (cents, unit)  (metered prices)
        self.pricing_page = None   # optional custom /larz/pricing renderer (set by use_pricing)
        self._on_grant = []    # callbacks(subject, sku)
        self._on_revoke = []
        self._on_payment_failed = []
        self._on_payment = []  # callbacks(subject, sku, cents) — every settled payment
        self._install_routes()

    # -- entitlement events ------------------------------------------------ #
    def on_grant(self, fn):
        self._on_grant.append(fn); return fn

    def on_revoke(self, fn):
        self._on_revoke.append(fn); return fn

    def on_payment_failed(self, fn):
        """Called (subject, sku) when a subscription is dropped after dunning."""
        self._on_payment_failed.append(fn); return fn

    def on_payment(self, fn):
        """Called (subject, sku, cents) on every settled payment (drives referrals)."""
        self._on_payment.append(fn); return fn

    def cancel(self, subject, sku, immediate=False):
        """Cancel a subscription. Default = at period end (keep until expiry);
        immediate=True revokes access now."""
        if immediate:
            self.store.revoke(subject, sku)
        else:
            # stop it renewing: clear the stored interval so the next webhook
            # (if any) grants no further time; access remains until expires_at.
            self.store.set_interval(sku, "")
        for fn in self._on_revoke:
            try: fn(subject, sku)
            except Exception: pass

    # registration -------------------------------------------------------- #
    def plan(self, name, price, features=None, trial_days=None, limits=None, rank=None):
        cents, interval = parse_price(price)
        self.plans[name] = {"sku": "plan:" + name, "cents": cents, "interval": interval,
                            "features": features or [], "trial_days": trial_days,
                            "price": price, "limits": limits or {},
                            "rank": rank if rank is not None else len(self.plans)}
        return self

    # -- plan-aware entitlements: current plan, features, limits ----------- #
    def current_plan(self, req):
        """The plan this caller is on: the highest-ranked plan they're actively
        entitled to, or — if none — the cheapest free ($0) plan as an implicit
        free tier. None only when no plan applies at all."""
        active = [p for p in self.plans.values()
                  if self.store.is_entitled(req.subject, p["sku"])]
        if active:
            return max(active, key=lambda p: p["rank"])
        free = [p for p in self.plans.values() if p["cents"] == 0]
        return min(free, key=lambda p: p["rank"]) if free else None

    def feature(self, req, name):
        """True if the caller's active plan grants feature `name` (by being in the
        plan's features list, or a truthy entry in its limits)."""
        p = self.current_plan(req)
        if not p:
            return False
        return name in p["features"] or bool(p["limits"].get(name))

    def plan_limit(self, req, key, default=0):
        """The numeric limit for `key` on the caller's active plan. None means
        unlimited; the default applies when they have no plan."""
        p = self.current_plan(req)
        if not p:
            return default
        return p["limits"].get(key, default)

    def within_limit(self, req, key, count, default=0):
        """True if `count` is under the caller's plan limit for `key`
        (None limit = unlimited)."""
        lim = self.plan_limit(req, key, default)
        return True if lim is None else count < lim

    # -- dunning: retry & recover failed subscription renewals ------------- #
    def run_dunning(self, grace_days=7, schedule=(0, 3, 7), on_notify=None):
        """Scan for subscriptions whose renewal lapsed and drive recovery: notify
        the customer on each scheduled day, and after `grace_days` revoke access
        and fire on_payment_failed. Call from a scheduled job:

            @app.schedule("0 9 * * *")     # daily 9am
            def dun(): app.money.run_dunning(on_notify=send_reminder_email)

        Idempotent — safe to run repeatedly. Returns {'notified', 'revoked'}.
        """
        notified = revoked = 0
        now = time.time()
        for sub in self.store.expired_subscriptions():
            subject, sku, expired_at = sub["subject"], sub["sku"], sub["expires_at"]
            row = self.store.dunning_get(subject, sku)
            first = row["first_failed"] if row else expired_at
            attempts = row["attempts"] if row else 0
            if row and row["status"] != "retrying":
                continue
            days = (now - first) / 86400.0
            # send any scheduled reminders now due
            while attempts < len(schedule) and days >= schedule[attempts]:
                attempts += 1
                if on_notify:
                    try: on_notify(subject, sku, attempts)
                    except Exception: pass
                notified += 1
            if days >= grace_days:
                self.store.revoke(subject, sku)
                self.store.dunning_upsert(subject, sku, first, attempts, status="revoked")
                for fn in self._on_payment_failed:
                    try: fn(subject, sku)
                    except Exception: pass
                revoked += 1
            else:
                self.store.dunning_upsert(subject, sku, first, attempts, status="retrying")
        return {"notified": notified, "revoked": revoked}

    def clear_dunning(self, subject, sku):
        """Call when a renewal succeeds to reset a subscription's dunning state."""
        self.store.dunning_clear(subject, sku)

    # -- revenue metrics (MRR, ARR, ARPU, LTV, churn) ---------------------- #
    def _monthly_cents(self, plan):
        c, iv = plan["cents"], plan["interval"]
        if iv in ("mo", "month"):  return c
        if iv in ("yr", "year"):   return c / 12.0
        if iv in ("wk", "week"):   return c * 52 / 12.0
        if iv == "day":            return c * 30.0
        return 0.0                                  # one-off / non-recurring

    def metrics(self):
        """Revenue metrics computed from your own payment data. Cents throughout."""
        plan_by_sku = {p["sku"]: p for p in self.plans.values()}
        active = self.store.active_entitlements()
        mrr = 0.0
        subscribers = 0
        per_plan = {}
        for e in active:
            p = plan_by_sku.get(e["sku"])
            if not p or not p["interval"]:
                continue
            subscribers += 1
            m = self._monthly_cents(p)
            mrr += m
            per_plan.setdefault(p["sku"], {"name": None, "count": 0, "mrr_cents": 0.0})
            per_plan[p["sku"]]["count"] += 1
            per_plan[p["sku"]]["mrr_cents"] += m
        for name, p in self.plans.items():
            if p["sku"] in per_plan:
                per_plan[p["sku"]]["name"] = name
        base = self.store.stats()
        # churn estimate: subs dropped after dunning in the last 30 days
        now = time.time()
        with self.store._conn() as c:
            churned_30 = c.execute(
                "SELECT COUNT(*) n FROM dunning WHERE status='revoked' "
                "AND last_notified > ?", (now - 30 * 86400,)).fetchone()["n"]
            new_30 = c.execute(
                "SELECT COUNT(*) n FROM payments WHERE status='paid' "
                "AND created_at > ?", (now - 30 * 86400,)).fetchone()["n"]
            rev_30 = c.execute(
                "SELECT COALESCE(SUM(cents),0) s FROM payments WHERE status='paid' "
                "AND created_at > ?", (now - 30 * 86400,)).fetchone()["s"]
        denom = subscribers + churned_30
        churn_rate = (churned_30 / denom) if denom else 0.0
        arpu = (mrr / subscribers) if subscribers else 0.0
        ltv = (arpu / churn_rate) if churn_rate else None
        return {
            "mrr_cents": round(mrr), "arr_cents": round(mrr * 12),
            "active_subscribers": subscribers,
            "arpu_cents": round(arpu), "ltv_cents": round(ltv) if ltv else None,
            "churn_rate": round(churn_rate, 4), "churned_30d": churned_30,
            "new_payments_30d": new_30, "revenue_30d_cents": rev_30,
            "total_revenue_cents": base["revenue_cents"], "payments": base["payments"],
            "outstanding_credit_cents": base["outstanding_credit_cents"],
            "per_plan": sorted(per_plan.values(), key=lambda x: -x["mrr_cents"]),
            "recent": base["recent"], "top_usage": base["top_usage"],
        }

    def credit_pack(self, name, price, credit, label=None):
        pcents, _ = parse_price(price)
        ccents, _ = parse_price(credit)
        self.packs[name] = {"price_cents": pcents, "credit_cents": ccents,
                            "label": label or name, "price": price}
        return self

    # -- marketplace: split a sale into seller/platform payouts ------------- #
    def split(self, splits, sku=None, ref=None):
        """Record a marketplace split ledger for one sale. `splits` is a list of
        (party, amount) where amount is "$8.50" or an integer number of cents.
        Returns the list of created payout ids. Money-native marketplaces use
        this to track what each seller is owed:

            app.money.split([("seller:42", "$8.50"), ("platform", "$1.50")],
                            sku=order.id)
        """
        ids = []
        for party, amount in splits:
            cents = amount if isinstance(amount, int) else parse_price(amount)[0]
            ids.append(self.store.record_payout(party, cents, sku=sku, ref=ref))
        return ids

    def payouts(self, party=None, status=None):
        return self.store.list_payouts(party=party, status=status)

    def owed(self, party):
        """Cents currently owed to a party (sum of pending payouts)."""
        return self.store.payout_owed(party)

    def mark_paid(self, payout_id):
        self.store.mark_payout_paid(payout_id)

    def _sku_for(self, spec, route):
        if spec.get("plan"):
            return "plan:" + spec["plan"]
        return spec.get("sku") or route.pattern.strip("/").replace("/", ":") or "root"

    # -- imperative API (for dynamic prices — catalogs, per-item checkout) -- #
    def entitled(self, req, sku):
        """True if this caller has paid for `sku`."""
        return self.store.is_entitled(req.subject, sku)

    def require(self, req, sku, price=None, cents=None, success_path=None):
        """Imperative paywall for dynamic prices. Returns None if the caller is
        already entitled to `sku` (proceed to serve), else a redirect Response
        to checkout. Use inside a handler when the price isn't known until
        request time (e.g. a product catalog):

            gate = app.money.require(req, sku=p.slug, price=p.price)
            if gate: return gate
            return deliver(p)
        """
        if self.store.is_entitled(req.subject, sku):
            return None
        if cents is None:
            cents, interval = parse_price(price)
            if interval:
                self.store.set_interval(sku, interval)
        return Response.redirect(
            self._checkout(req, req.subject, sku, cents, success_path))

    def _checkout(self, req, subject, sku, cents, success_path=None):
        cents2, ok = self.store.apply_coupon(req.query.get("coupon"), cents)
        success = self.base_url + (success_path or req.path)
        cancel = self.base_url + "/larz/pricing"
        return self.provider.create_checkout(subject, sku, cents2, success, cancel)

    # enforcement (called from core.dispatch) ----------------------------- #
    def enforce_paid(self, req, spec, route):
        subject = req.subject
        sku = self._sku_for(spec, route)
        if self.store.is_entitled(subject, sku):
            return None
        cents, interval = parse_price(spec["price"])
        if interval:
            self.store.set_interval(sku, interval)   # remember for the webhook
        # free trial on first access
        if spec.get("trial_days") and self.store.trial_available(subject, sku):
            self.store.start_trial(subject, sku, spec["trial_days"])
            return None
        return Response.redirect(self._checkout(req, subject, sku, cents))

    def enforce_plan(self, req, plan_name, route):
        plan = self.plans.get(plan_name)
        if not plan:
            return Response("unknown plan '%s'" % plan_name, status=500)
        subject = req.subject
        if self.store.is_entitled(subject, plan["sku"]):
            return None
        if plan["interval"]:
            self.store.set_interval(plan["sku"], plan["interval"])
        if plan["trial_days"] and self.store.trial_available(subject, plan["sku"]):
            self.store.start_trial(subject, plan["sku"], plan["trial_days"])
            return None
        return Response.redirect(self._checkout(req, subject, plan["sku"], plan["cents"]))

    def enforce_metered(self, req, spec, route):
        subject = req.subject
        sku = self._sku_for(spec, route)
        cents, _ = parse_price(spec["price"])
        if self.store.charge(subject, cents, sku):
            return None
        return Response.json(
            {"error": "payment_required", "sku": sku, "price_cents": cents,
             "balance_cents": self.store.balance(subject),
             "top_up": self.base_url + "/larz/credits"}, status=402)

    # webhook handling: grant entitlement, or add credit for credit-packs -- #
    def _fulfil(self, result, provider_name):
        subject, sku, cents = result["subject"], result["sku"], result["cents"]
        if sku and sku.startswith("credits:"):
            pack = self.packs.get(sku.split(":", 1)[1])
            self.store.add_credit(subject, pack["credit_cents"] if pack else cents)
        else:
            interval = self.store.get_interval(sku)
            if not interval:
                plan = next((p for p in self.plans.values() if p["sku"] == sku), None)
                interval = plan["interval"] if plan else None
            days = _INTERVAL_DAYS.get(interval) if interval else None
            self.store.grant(subject, sku, days=days)
            self.store.dunning_clear(subject, sku)      # a paid renewal recovers it
            for fn in self._on_grant:
                try: fn(subject, sku)
                except Exception: pass
        self.store.record_payment(result["payment_id"], subject, sku, cents, provider_name)
        self.store.record_invoice(result["payment_id"], subject, sku, cents, provider_name)
        for fn in self._on_payment:
            try: fn(subject, sku, cents)
            except Exception: pass

    # built-in routes ----------------------------------------------------- #
    def _install_routes(self):
        app = self.app

        @app.get("/larz/checkout/mock", sitemap=False)
        def _mock_confirm(req):
            if not isinstance(self.provider, MockProvider):
                return Response("mock checkout disabled", status=404)
            result = self.provider.confirm(req)
            if not result:
                return Response("bad signature", status=400)
            if req.query.get("coupon"):
                self.store.redeem_coupon(req.query.get("coupon"))
            self._fulfil(result, "mock")
            return Response.redirect(result.get("next", "/"))

        @app.post("/larz/webhook/<provider>", sitemap=False)
        def _webhook(req):
            result = self.provider.parse_webhook(req)
            if not result:
                return Response("ignored", status=200)
            self._fulfil(result, self.provider.name)
            return Response("ok", status=200)

        @app.get("/larz/credits", sitemap=False)
        def _credits(req):
            if req.query.get("format") == "json" or "application/json" in (req.header("Accept") or ""):
                return Response.json({"subject": req.subject,
                                      "balance_cents": self.store.balance(req.subject)})
            packs = "".join(
                "<li>%s — <a href='/larz/credits/buy/%s'>buy for %s</a> "
                "(+%d credits)</li>" % (p["label"], n, p["price"], p["credit_cents"])
                for n, p in self.packs.items())
            return Response(
                "<h1>Credits</h1><p>Balance: <b>%d</b> cents</p><ul>%s</ul>"
                % (self.store.balance(req.subject), packs or "<li>No packs configured</li>"))

        @app.get("/larz/credits/buy/<pack>", sitemap=False)
        def _buy_credits(req):
            pack = self.packs.get(req.params["pack"])
            if not pack:
                return Response("no such pack", status=404)
            url = self._checkout(req, req.subject, "credits:" + req.params["pack"],
                                 pack["price_cents"], success_path="/larz/credits")
            return Response.redirect(url)

        @app.get("/larz/subscribe/<plan>", sitemap=False)
        def _subscribe(req):
            plan = self.plans.get(req.params["plan"])
            if not plan:
                return Response("no such plan", status=404)
            subject = req.subject
            if self.store.is_entitled(subject, plan["sku"]):
                return Response.redirect("/larz/account")
            if plan["interval"]:
                self.store.set_interval(plan["sku"], plan["interval"])
            if plan["trial_days"] and self.store.trial_available(subject, plan["sku"]):
                self.store.start_trial(subject, plan["sku"], plan["trial_days"])
                return Response.redirect("/larz/account")
            return Response.redirect(self._checkout(req, subject, plan["sku"], plan["cents"],
                                                    success_path="/larz/account"))

        @app.get("/larz/pricing", sitemap=False)
        def _pricing(req):
            if self.pricing_page:
                return Response(self.pricing_page(req) if callable(self.pricing_page)
                                else self.pricing_page)
            rows = []
            for name, p in self.plans.items():
                feats = "".join("<li>%s</li>" % f for f in p["features"])
                trial = " (%d-day free trial)" % p["trial_days"] if p["trial_days"] else ""
                cta = ("" if p["cents"] == 0 else
                       " <a href='/larz/subscribe/%s'>Choose %s</a>" % (name, name.title()))
                rows.append("<div class='plan'><h2>%s — %s%s</h2><ul>%s</ul>%s</div>"
                            % (name.title(), p["price"], trial, feats, cta))
            return Response("<h1>Pricing</h1>" + ("".join(rows) or "<p>No plans yet</p>"))

        @app.get("/larz/account", sitemap=False)
        def _account(req):
            subject = req.subject
            with self.store._conn() as c:
                ents = c.execute("SELECT sku, expires_at FROM entitlements WHERE subject=?",
                                 (subject,)).fetchall()
            rows = "".join(
                "<li><b>%s</b> %s "
                "<form style='display:inline' method=post action='/larz/account/cancel'>"
                "<input type=hidden name=sku value='%s'><button>Cancel</button></form></li>"
                % (e["sku"], ("(active, renews %s)" % _fmt_ts(e["expires_at"]))
                   if e["expires_at"] else "(active)", e["sku"])
                for e in ents) or "<li>No active subscriptions.</li>"
            invs = "".join("<tr><td>%s</td><td>%s</td><td>$%.2f</td>"
                           "<td><a href='/larz/invoice/%s'>receipt</a></td></tr>"
                           % (_fmt_ts(i["created_at"]), i["sku"], i["cents"] / 100.0, i["id"])
                           for i in self.store.list_invoices(subject))
            return Response(
                "<h1>Your account</h1><p>Credit balance: <b>%d</b> cents</p>"
                "<h2>Subscriptions</h2><ul>%s</ul>"
                "<h2>Invoices</h2><table border=1>%s</table>"
                "<p><a href='/larz/credits'>Buy credits</a> · "
                "<a href='/larz/pricing'>Pricing</a></p>"
                % (self.store.balance(subject), rows, invs or "<tr><td>none</td></tr>"))

        @app.post("/larz/account/cancel", sitemap=False)
        def _cancel(req):
            sku = req.form.get("sku")
            if sku:
                self.cancel(req.subject, sku)
            return Response.redirect("/larz/account")

        @app.get("/larz/invoice/<iid>", sitemap=False)
        def _invoice(req):
            inv = self.store.get_invoice(req.params["iid"])
            if not inv or inv["subject"] != req.subject:
                return Response("not found", status=404)
            return Response(
                "<div style='font:15px system-ui;max-width:520px;margin:2rem auto'>"
                "<h1>Receipt</h1><table>"
                "<tr><td>Invoice</td><td><code>%s</code></td></tr>"
                "<tr><td>Item</td><td>%s</td></tr>"
                "<tr><td>Amount</td><td><b>$%.2f</b></td></tr>"
                "<tr><td>Provider</td><td>%s</td></tr>"
                "<tr><td>Date</td><td>%s</td></tr></table>"
                "<p><a href='/larz/account'>← account</a></p></div>"
                % (inv["id"], inv["sku"], inv["cents"] / 100.0, inv["provider"],
                   _fmt_ts(inv["created_at"])))

        @app.get("/larz/admin", sitemap=False)
        def _admin(req):
            if self.admin_token and req.query.get("token") != self.admin_token:
                return Response("forbidden", status=403)
            s = self.store.stats()
            recent = "".join(
                "<tr><td>%s</td><td>%s</td><td>$%.2f</td><td>%s</td></tr>"
                % (r["subject"][:12], r["sku"], r["cents"] / 100.0, r["provider"])
                for r in s["recent"])
            usage = "".join("<tr><td>%s</td><td>%d</td><td>$%.2f</td></tr>"
                            % (u["sku"], u["calls"], u["cents"] / 100.0)
                            for u in s["top_usage"])
            return Response(
                "<h1>Larz — Revenue</h1>"
                "<p>Total revenue: <b>$%.2f</b> across %d payments</p>"
                "<p>Active subscriptions: <b>%d</b></p>"
                "<p>Outstanding credit liability: $%.2f</p>"
                "<h2>Recent payments</h2><table border=1>%s</table>"
                "<h2>Top metered usage</h2><table border=1>%s</table>"
                % (s["revenue_cents"] / 100.0, s["payments"], s["active_subscriptions"],
                   s["outstanding_credit_cents"] / 100.0, recent, usage))


def enable(app, provider=None, db="larz_money.db",
           base_url="http://127.0.0.1:8000", admin_token=None):
    """Turn on the money-native layer for an app."""
    provider = provider or MockProvider()
    store = EntitlementStore(db)
    app.money = _Money(app, provider, store, base_url, admin_token=admin_token)
    # convenience aliases on the app for plan features / limits
    app.feature = app.money.feature
    app.within_limit = app.money.within_limit
    app.plan_limit = app.money.plan_limit
    app.current_plan = app.money.current_plan
    return app.money
