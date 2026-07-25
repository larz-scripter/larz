# Deploying a Larz app to production

Larz apps are **WSGI callables** with **zero dependencies**, so deployment is
boring in the best way — any WSGI server runs them.

## Quick production run

```bash
pip install larz gunicorn
gunicorn -c deploy/gunicorn.conf.py app:app      # app.py exposes `app`
```

`app` must be your `Larz` instance. In development you use `app.run()`; in
production you hand `app` to gunicorn/uWSGI/waitress instead.

## systemd

```bash
sudo cp deploy/larz.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now larz
```

## Docker

```bash
docker build -f deploy/Dockerfile -t myapp .
docker run -p 8000:8000 --env-file .env myapp
```

## Behind nginx (TLS, static files)

Use `deploy/nginx.conf` as a starting point; terminate TLS at nginx (or Caddy)
and proxy to gunicorn on `127.0.0.1:8000`.

## Production checklist

- [ ] Set a strong `secret` on `Larz(secret=...)` — from an env var, never hard-coded
- [ ] Point `money.enable(db=...)` / `models.connect(...)` at **durable** storage
      (a real path, not `/tmp`; or Postgres in production)
- [ ] Add `app.use(security.RateLimiter(...))` and `app.use(security.bot_filter())`
- [ ] Enable `csrf_protect` for form POSTs
- [ ] Set your real payment provider (Stripe/Paddle/…) — `MockProvider` is dev-only
- [ ] Configure each provider's webhook URL to `/larz/webhook/<provider>`
- [ ] Serve over HTTPS (providers require it; sessions/cookies want it)

## Background jobs & the scheduler in production

`@app.job` and `@app.schedule(...)` run **per gunicorn worker**. Two safe patterns:

1. **One scheduler worker** — run the web app with N workers *without* schedules,
   and a single separate process that only imports the app + registers schedules.
2. **Leader lock** — gate scheduled tasks on a DB/Redis lock so only one worker
   fires them.

For heavy job volume, move to a dedicated queue (the `@app.job` API is a thin
in-process default; swap `app.jobs` for a Redis/RQ-backed queue with the same
`enqueue()` signature).

## Scaling notes / honest limits

- The built-in dev server (`app.run()`) is single-threaded — **never** use it in
  production; use gunicorn.
- The default SQLite ORM is great to a point; for high write concurrency use
  Postgres (the ORM backend is pluggable).
- Larz is **WSGI (sync)**. For very high-concurrency I/O-bound workloads, an
  ASGI/async core is on the roadmap; today, scale horizontally with more workers.
