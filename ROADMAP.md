# ✅ Larz v1.0 — SHIPPED

All four expansion packs below are built, tested (110 checks), and released.
See the README for the full feature list.

---

# Larz v1.0 roadmap

Building Larz from a money-native micro-framework into a full, batteries-included
SaaS framework — while keeping zero runtime dependencies and the money-native
thesis at the center. Four expansion packs, built in dependency order.

## 🅒 Data (foundation) — `larz/models.py`, `larz/db.py`
- Rich field types: `DateTimeField`, `JSONField`, `DecimalField`, `FloatField`, `BoolField`
- Relationships: `ForeignKey` + related lookups
- Query builder: `field__gt/gte/lt/lte/in/like/contains`, `order`, `limit/offset`, `.page()`, `.count()`, `.exists()`
- Transactions, model hooks (`before_save`/`after_save`), unique + index
- Lightweight migrations (auto-create + schema tracking)

## 🅐 Auth + API — `larz/auth.py`, `larz/api.py`
- `User` model + scrypt password hashing; `register`/`login`/`logout`; `@app.login_required`; `current_user`
- Email verification + password-reset tokens
- **API keys** tied to a user + plan/quota; `@app.api_key_required`
- **RBAC** roles; `@app.require_role`
- Request validation (`@app.validate(schema)`), **auto OpenAPI** at `/openapi.json` + Swagger UI at `/docs`
- Pagination helpers; **webhooks framework** (sign, deliver, retry)

## 🅑 Money, deepened — `larz/money.py`, `larz/billing.py`
- Subscription lifecycle: cancel-at-period-end, `past_due`/dunning, grace period, proration helper
- Entitlement events: `on_grant` / `on_revoke` / `on_renew`
- **Invoices/receipts** (record + HTML receipt + email) ; **customer portal** (`/larz/account`)
- Tiered/volume usage pricing; renewal-reminder emails
- Marketplace **payouts ledger** (split records)
- More providers: Paddle, LemonSqueezy, Paystack, PayPal, pay-with-LARZ

## 🅓 Frontend & Ops — `larz/*`
- Templating: `{% extends %}`/`{% block %}`, filters
- **Forms** + validation + flash messages + CSRF-integrated
- **HTMX** helpers (partials + trigger headers)
- **Background jobs** (`@app.job`, `app.enqueue`) + **scheduler** (`@app.schedule(cron)`)
- **Email** (SMTP + templates), **caching** (`@app.cache`, TTL)
- Config (`.env`), structured logging, `/healthz` + `/metrics`, production-server recipe
- **Auto admin panel** — CRUD over models, auth-protected

## Deferred to post-1.0 (honest scope)
- Full **ASGI/async core** (a parallel runtime — rewrite-scale; the money model is sync-friendly)
- **Postgres** ORM adapter (sqlite ships; backend is pluggable)

Target: **v1.0.0** — every pack's core built, tested (no pytest), documented, PyPI-released.

---

# ✅ v1.1 — SHIPPED

Six additions, all zero-dependency, 52 new tests (164 total, CI green):

- **TOTP 2FA** (`larz/twofa.py`) — RFC 6238 authenticator codes + one-time backup
  codes; `app.twofa.begin/activate/verify`, `@app.twofa_required`. Works with
  Google Authenticator, Authy, 1Password.
- **Social login** (`larz/oauth.py`) — Google / GitHub / any OAuth2 provider over
  urllib; `/larz/oauth/<provider>/login` finds-or-creates a `User`.
- **File uploads** — `req.files` (stdlib multipart parser) + `req.htmx`,
  `req.flash()` / `get_flashed_messages()`, `Response.hx_trigger()`.
- **Storage** (`larz/storage.py`) — `LocalStorage` with safe names, mounting, and a
  pluggable backend interface.
- **Marketplace payouts** — `app.money.split(...)` records a seller/platform split
  ledger; `app.money.owed(party)`, `payouts()`, `mark_paid()`.
- **Four more providers** — Square, Razorpay, Mollie, Coinbase Commerce (10 → 14).
- **Test client** (`larz/testing.py`) — `Client(app)` in-process WSGI client with a
  cookie jar (`get/post/json/redirect`, `follow=True`, `login()`).

---

# ✅ v1.2 "The Revenue Engine" — SHIPPED

Larz is now **revenue-native and AI-native**: not just *take* a payment, but run the
whole money lifecycle. 56 new tests (220 total), zero dependencies.

- **Pricing-as-code** (`larz/pricing.py`) — declare plans, usage prices, credit packs
  and coupons as one `Pricing()` object; it generates the styled `/larz/pricing`
  page, checkout, entitlements and metered prices. `/larz/subscribe/<plan>`.
- **Feature flags & limits by plan** — `app.feature(req, "api")`,
  `app.within_limit(req, "projects", n)`, `app.current_plan(req)`.
- **Revenue analytics** (`larz/analytics.py`) — MRR, ARR, ARPU, LTV, churn and a
  30-day chart at `/larz/admin/revenue`; `app.money.metrics()` for the raw numbers.
- **Dunning & recovery** — `app.money.run_dunning()` retries lapsed renewals on a
  schedule and revokes after a grace period; `on_payment_failed` hook.
- **Referrals & affiliates** (`larz/referrals.py`) — referral links + automatic
  commissions to the referrer's payout ledger when a referred user pays.
- **AI monetization** (`larz/ai.py`) — `@app.ai_metered` + `app.ai.charge()` bill
  tokens with **sub-cent accuracy**, **BYOK** keys encrypted at rest
  (`larz/crypto.py`), per-plan rate limits, and an optional metered proxy.
- **Authenticated encryption** (`larz/crypto.py`) — `Cipher` for secrets at rest
  (HMAC-SHA256-CTR + encrypt-then-MAC, standard-library only).

Next: **v1.3 "Modern API"** (typed-signature validation, DI, WebSockets/SSE,
Swagger/ReDoc) → **v2.0 "Async"** (ASGI core).
