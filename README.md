# ⚡ Larz

**The money-native web framework.**

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
| **Money** | `@app.paid` · `@app.metered` · `@app.plan` · subscriptions & renewals · free trials · coupons · credit packs · built-in `/larz/pricing`, `/larz/credits`, and a `/larz/admin` **revenue dashboard** (MRR, sales, usage) |
| **Providers** | `MockProvider` (keyless dev) · `StripeProvider` · `GemVaultProvider` · `DodoProvider` · `CryptoProvider` — all via stdlib `urllib`, **no SDKs**. Add your own in ~40 lines. |
| **Core** | WSGI engine · typed routing (`/u/<id:int>/<slug>`) · signed-cookie sessions · blueprints · static files · debug error pages |
| **Templating** | A real `{{ }}` / `{% for %}` / `{% if %}` engine with autoescaping |
| **Models** | A tiny active-record ORM over sqlite (`Model` / `Field`, queries, ordering) |
| **Security** | Rate limiting · bot / invalid-traffic filter · CSRF · CORS |
| **SEO** | Auto `sitemap.xml` + `robots.txt` · OpenGraph/Twitter `meta_tags()` · IndexNow ping |
| **CLI** | `larz new` · `larz run` · `larz routes` |

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
python3 tests/test_core.py       # 15 checks
python3 tests/test_features.py   # 32 checks — money, templating, models, security
```

47 checks, no pytest required.

## Scaffold a new app

```bash
python3 -m larz new myapp
cd myapp && python3 -m larz run
```

## Status

v0.2.0 — real and tested, but not yet production-hardened (WSGI dev server,
in-memory rate limiting, MockProvider is dev-only). Roadmap: ASGI/async core,
production server recipe, dunning/renewal reminders, richer analytics, a hosted
docs site at [larzos.com/larz](https://larzos.com/larz).

## License

MIT.
