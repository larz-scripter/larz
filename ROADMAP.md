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
- OAuth login providers, TOTP 2FA (extension points provided)

Target: **v1.0.0** — every pack's core built, tested (no pytest), documented, PyPI-released.
