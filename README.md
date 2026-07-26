# ⚡ Larz


> **New in 1.2 — the Revenue Engine** — pricing-as-code, revenue analytics (MRR/ARR/churn/LTV), dunning & recovery, referrals/affiliates, and **AI monetization** (token metering with BYOK). Larz is now revenue-native *and* AI-native. See the [changelog](https://larzos.com/larz/changelog/).

> **New in 1.1** — TOTP 2FA, social login (Google/GitHub), file uploads, a marketplace payout ledger, four more payment providers (Square, Razorpay, Mollie, Coinbase Commerce), and a first-class test client. See the [changelog](https://larzos.com/larz/changelog/).
[![PyPI](https://img.shields.io/pypi/v/larz.svg)](https://pypi.org/project/larz/)
[![Python](https://img.shields.io/pypi/pyversions/larz.svg)](https://pypi.org/project/larz/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](pyproject.toml)

**The money-native web framework.**

```bash
pip install larz
```

Larz is a small, from-scratch, **dependency-free** Python web framework where
**payments, paywalls, subscriptions, trials, and usage-metering are first-class
primitives** — not something you bolt on with three libraries and a weekend.

Flask makes routes easy. Django makes data easy. **Larz makes getting paid easy.**

```python
from larz import Larz
import larz.money as money

app = Larz(secret="change-me")
money.enable(app)                          # payments on. Zero keys in dev.

@app.paid("$9/mo", trial_days=7)           # subscription with a free trial
@app.get("/pro/report")
def report(req):
    return "<h1>Pro report for %s</h1>" % req.subject

@app.metered("$0.02/call")                 # per-call billing from prepaid credit
@app.post("/api/summarize")
def summarize(req):
    return {"summary": run(req.json()["text"])}

app.run()
```

A request to `/pro/report` from someone who hasn't paid is **automatically**
sent through checkout (or given their free trial); when they come back, they're
served. A request to `/api/summarize` with no credit gets a clean `402`. You
never write `if user.is_paid`.

## Batteries included — all pure stdlib

| Area | What you get |
|---|---|
| **Money** | `@app.paid` · `@app.metered` · `@app.plan` · subscriptions, renewals, **cancel + dunning** · free trials · coupons · credit packs · **invoices/receipts** · **customer portal** (`/larz/account`) · entitlement events · `/larz/admin` **revenue dashboard** |
| **Providers** | Mock · **Stripe · Paddle · Lemon Squeezy · Paystack · PayPal** · GemVault · Dodo · Crypto — all via stdlib `urllib`, **no SDKs**. Add your own in ~40 lines. |
| **Auth** | `larz.auth` — users, scrypt passwords, `@app.login_required`, `req.user`, **API keys** (`@app.api_key_required`, plan-gated), **RBAC** (`@app.require_role`), email-verify + password-reset tokens |
| **API tooling** | `@app.validate(schema)` · **auto OpenAPI** (`/openapi.json` + `/docs`) · `paginate()` · a **webhooks** framework (sign, deliver, retry) |
| **ORM** | `larz.models` — relationships (`ForeignKey`), field types (datetime/json/decimal…), query operators (`views__gt`, `title__like`), pagination, hooks, transactions, auto-migrations |
| **Admin** | `larz.admin` — an auto-generated **CRUD admin panel** over your models |
| **Ops** | `larz.ops` — `@app.cache` · **background jobs** (`@app.job`) · **cron scheduler** (`@app.schedule`) · email (SMTP) · `.env` config · `/healthz` + `/metrics` |
| **Templating** | `{{ }}` / `{% for %}` / `{% if %}` · **inheritance** (`{% extends %}`/`{% block %}`) · **filters** (`|upper`, `|currency`, `|date`…) · autoescaping |
| **Core** | WSGI engine · typed routing · signed-cookie sessions · blueprints · static files · debug pages |
| **Security · SEO · CLI** | Rate limiting, bot filter, CSRF, CORS · auto sitemap/robots + OpenGraph + IndexNow · `larz new / run / routes` |

## Why

Every other framework treats money as an afterthought — the thing you wire up
last, badly, copy-pasted from a payments tutorial. But for indie hackers,
solopreneurs, and anyone shipping a product to *make a living*, monetization
isn't the afterthought — it's the point. Larz puts it in the core.

## Use it for

* **Paid / metered APIs** — AI wrappers, data APIs (`@app.metered` per call)
* **Content paywalls** — reports, courses, paid newsletters (`@app.paid`)
* **Digital products** — one-off unlocks and downloads
* **Micro-SaaS** — a free tier + a Pro plan with a trial (`@app.plan`)

## Design principles

* **Zero dependencies.** Pure Python standard library — `sqlite3`, `urllib`,
  `wsgiref`. Runs anywhere `python3` runs, including constrained boxes and phones.
* **Provider-agnostic.** The framework never hard-codes a processor. Implement
  `create_checkout` + `parse_webhook` and any processor drops in.
* **Portable by default.** No modern-SQLite assumptions, no C extensions.

## Run the demos (no API keys)

```bash
python3 examples/paid_app.py     # the essentials: paid + metered routes
python3 examples/saas_app.py     # a full mini-SaaS: plans, trials, credits,
                                 # templates, ORM, dashboard, security
```

Then open http://127.0.0.1:8000/ and click through. Visit `/larz/admin?token=admin123`
on the SaaS demo to see the revenue dashboard.

## Test

```bash
python3 tests/test_core.py       # 15 — routing, sessions, paywall, metering
python3 tests/test_features.py   # 43 — money, providers, templating, security
python3 tests/test_v1.py         # 52 — ORM, auth, API, admin, ops, billing
```

110 checks, no pytest required.

## Scaffold a new app

```bash
python3 -m larz new myapp
cd myapp && python3 -m larz run
```

## Status

**v1.0.0** — real and tested, but not yet production-hardened (WSGI dev server,
in-memory rate limiting, MockProvider is dev-only). Roadmap: ASGI/async core,
production server recipe, dunning/renewal reminders, richer analytics, a hosted
docs site at [larzos.com/larz](https://larzos.com/larz).

## License

MIT.
