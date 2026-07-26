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

---

# ✅ v1.3 "Modern API" — SHIPPED

The parity release — the modern-API ergonomics people expect, still zero-dep.
33 new tests (253 total).

- **Typed request binding** (`larz/params.py`) — annotate a handler's parameters
  and Larz validates, coerces and injects them (`name: str, qty: int = 1`); bad
  input → a 422 with per-field errors. **Dataclass request bodies** validate the
  whole JSON body. `Query`/`Path`/`Body`/`Form` markers.
- **Dependency injection** — `Depends(fn)` resolves and caches per request.
- **Streaming & Server-Sent Events** — `Response.stream(iter)` and
  `Response.sse(events)` for real-time push and large downloads, over plain WSGI.
- **Typed OpenAPI + a self-contained API explorer** — `/openapi.json` now reflects
  the typed signatures; the built-in `/docs` explorer shows params (no CDN).
- **CORS** — `app.enable_cors()` with preflight handling.
- **Lifecycle** — `@app.on_startup` / `@app.on_shutdown` (also fire under gunicorn).
- Middleware now runs before routing, so it can short-circuit any request.

**Runs on your infrastructure, not ours** — zero runtime dependencies, no calls to
any Larz/vendor server, no telemetry. Proven with a full app running offline with
the network hard-blocked.

---

# ✅ v2.0 "Async + Postgres" — SHIPPED

The last two big items, both done — still zero-dependency. 21 new tests (274 total).

- **ASGI / async core** — the Larz app is now dual-protocol: it runs under WSGI
  (gunicorn) *and* ASGI (uvicorn/hypercorn), detected by call shape. Handlers can
  be `async def` (awaited) or sync (run directly). ASGI lifespan drives
  `on_startup`/`on_shutdown`.
- **WebSockets** — `@app.websocket("/ws/<room>")` async handlers with a
  `WebSocket` object (`accept`/`receive`/`send`/`send_json`/`close`, `async for`).
- **A built-in zero-dependency async server** (`larz/aserver.py`) — `app.run_async()`
  speaks HTTP/1.1 **and** RFC 6455 WebSockets over pure `asyncio`, so async mode
  needs no uvicorn install. (Production can still use any ASGI server.)
- **Streaming over ASGI** — `Response.stream()` / `Response.sse()` work under both
  protocols.
- **Pure-Python PostgreSQL driver** (`larz/pg.py`) — implements the v3 wire
  protocol with **SCRAM-SHA-256** (RFC 7677, matches the spec's test vector), md5
  and cleartext auth, and parameterized (injection-safe) queries. `connect(
  "postgres://…")` transparently switches the ORM to Postgres; **sqlite stays the
  zero-config default**. Verified end-to-end against a live PostgreSQL 15 server.

**Larz is feature-complete against its roadmap** — money-native, AI-native,
FastAPI-style ergonomics, async + WebSockets, sqlite *and* Postgres — all with
**zero runtime dependencies** and nothing that phones home.
