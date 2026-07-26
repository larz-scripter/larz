# Changelog

Full notes: https://larzos.com/larz/changelog/

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
