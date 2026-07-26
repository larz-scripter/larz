# Changelog

Full notes: https://larzos.com/larz/changelog/

## 2.3.0
- **`larz.contrib` — opt-in Larz Stack adapters.** The core stays zero-dependency;
  install extras to light up richer features:
  `pip install larz[money|auth|ai|data|ops|full]`.
  - `contrib.pdf` → `app.invoice()` / `app.receipt()` return real PDF downloads (larzpdf)
  - `contrib.ledger` → `app.ledger` + `record_sale/refund/fee` double-entry books (larzledger)
  - `contrib.twofa_qr` → `app.twofa_enroll()` returns a scannable 2FA QR (larztotp + larzqr)
  - `contrib.agents` → `app.agent()` / `app.ask()` tool-calling AI agents (larzagent)
  - `contrib.require()` / `available()` degrade gracefully with a clear install hint.
- **SEO internal-linking engine in `larz.seo`**: `ContentGraph` (related, link_map,
  breadcrumbs, orphans, validate, sitemap), `interlink()` (safe auto-linking of the
  first keyword mention), `json_ld()` (Article/Course/FAQ/Breadcrumb), and
  `breadcrumbs_html`/`related_html` renderers — programmatic SEO with a real link graph.
- New example: `examples/full_stack_saas.py` (money + ledger + PDF invoice + 2FA QR).

## 2.1.0
- `larz new` project scaffolder (templates: minimal/api/saas/ai/marketplace)
- Real-world examples (url_shortener, link_in_bio); benchmarks (bench/)
- Implicit free tier: unentitled users fall back to the cheapest $0 plan

## 2.0.0
- ASGI/async core + WebSockets + built-in zero-dep async server (app.run_async)
- Pure-Python PostgreSQL driver (SCRAM-SHA-256); connect("postgres://…")

## 1.3.0
- Typed request binding from signatures, dataclass bodies, Depends() DI
- Streaming & Server-Sent Events, CORS, lifecycle hooks

## 1.2.0
- Pricing-as-code, revenue analytics, dunning, referrals, AI token metering

## 1.1.0
- TOTP 2FA, social login (OAuth), file uploads, marketplace payouts, +4 providers, test client

## 1.0.0
- First stable release: the money-native core, 10 payment providers, auth, ORM, admin, jobs, API toolkit
