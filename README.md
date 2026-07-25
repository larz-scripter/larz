# ⚡ Larz

**The money-native web framework.**

Larz is a small, from-scratch, dependency-free Python web framework where
**payments, paywalls, and usage-metering are first-class primitives** — not
something you bolt on with three libraries and a weekend.

Flask makes routes easy. Django makes data easy. **Larz makes getting paid easy.**

```python
from larz import Larz
import larz.money as money

app = Larz(secret="change-me")
money.enable(app)                      # payments on. Zero config, zero keys in dev.

@app.paid("$9")                        # one-off unlock
@app.get("/pro/report")
def report(req):
    return "<h1>Pro report for %s</h1>" % req.subject

@app.metered("$0.02/call")             # per-call billing against prepaid credit
@app.post("/api/summarize")
def summarize(req):
    return {"summary": summarize_text(req.json()["text"])}

app.run()
```

That's the whole app. No Stripe SDK, no webhook boilerplate, no `if user.is_paid`
checks scattered through your views. A request to `/pro/report` from someone who
hasn't paid is **automatically** redirected through checkout; when they come
back, they're served. A request to `/api/summarize` with no credit gets a clean
`402 Payment Required`.

## Why

Every other framework treats money as an afterthought — the thing you wire up
last, badly, copy-pasted from a payments tutorial. But for indie hackers,
solopreneurs, and anyone shipping a product to *make a living*, monetization
isn't the afterthought — it's the point. Larz puts it in the core.

## What you get

| Primitive | What it does |
|---|---|
| `@app.paid(price)` | Gate a route behind a one-off or subscription payment. Auto checkout + entitlement. |
| `@app.metered(price)` | Charge per call against a prepaid credit balance. Clean `402` when empty. |
| Pluggable providers | `MockProvider` (keyless dev), `StripeProvider` (real, via stdlib — no SDK). Drop in your own in ~40 lines. |
| Auto SEO | `/sitemap.xml` + `/robots.txt` generated from your routes; one-call IndexNow ping. |
| Sessions | Signed-cookie sessions out of the box (stdlib `hmac`). |
| Typed routing | `/user/<id:int>/posts/<slug>` with converters. |

## Design principles

* **Zero dependencies.** Pure Python standard library. `sqlite3` for the
  entitlement store, `urllib` for provider calls, `wsgiref` for the dev server.
  Runs anywhere `python3` runs — including RAM-constrained boxes and phones.
* **Provider-agnostic.** The framework never hard-codes a processor. Ship with
  Stripe, swap in your own (GemVault, Dodo, Paddle, crypto) by implementing two
  methods: `create_checkout` and `parse_webhook`.
* **Flag, don't guess.** Money state lives in one auditable SQLite store you own.

## Run the demo (no API keys)

```bash
python3 examples/paid_app.py
# open http://127.0.0.1:8000/  and click through the paid routes
```

## Test

```bash
python3 tests/test_core.py      # 15 checks, no pytest required
```

## Status

v0.1.0 — early, but real: routing, sessions, paywall, metering, providers, and
SEO all work and are tested. Not yet production-hardened (WSGI dev server only,
in-memory rate limiting, MockProvider is dev-only). Roadmap: subscription
renewals, a real GemVault/Dodo provider, ASGI/async core, credit-pack checkout.

## License

MIT.
