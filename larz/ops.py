"""
larz.ops — production/DX batteries (zero-dep).

    import larz.ops as ops
    ops.enable(app)                      # /healthz, /metrics, cache, jobs, scheduler

    @app.cache(ttl=60)
    @app.get("/expensive")
    def expensive(req): ...

    @app.job                             # background queue
    def send_welcome(email): ...
    send_welcome.enqueue("a@b.com")

    @app.schedule("0 3 * * *")           # cron: daily at 03:00
    def nightly(): ...

    ops.load_env(".env")                 # populate os.environ
    ops.send_email(smtp, "to@x.com", "Hi", "<b>body</b>")
"""

import os
import time
import queue
import smtplib
import threading
from email.mime.text import MIMEText

from larz import Response

__all__ = ["enable", "load_env", "send_email", "Cache"]


# --------------------------------------------------------------------------- #
#  Config: .env loader
# --------------------------------------------------------------------------- #
def load_env(path=".env"):
    if not os.path.exists(path):
        return {}
    loaded = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)
            loaded[k.strip()] = v
    return loaded


# --------------------------------------------------------------------------- #
#  Cache (in-memory TTL; pluggable via a dict-like backend)
# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._d.get(key)
            if not item:
                return None
            value, exp = item
            if exp and exp < time.time():
                self._d.pop(key, None)
                return None
            return value

    def set(self, key, value, ttl=None):
        with self._lock:
            self._d[key] = (value, time.time() + ttl if ttl else None)

    def clear(self):
        with self._lock:
            self._d.clear()


# --------------------------------------------------------------------------- #
#  Email (SMTP)
# --------------------------------------------------------------------------- #
def send_email(smtp, to, subject, html_body, sender=None):
    """smtp = {"host","port","user","password","tls":True}. Returns True/False."""
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender or smtp.get("user", "")
    msg["To"] = to
    try:
        s = smtplib.SMTP(smtp["host"], smtp.get("port", 587), timeout=15)
        if smtp.get("tls", True):
            s.ehlo(); s.starttls(); s.ehlo()
        if smtp.get("user"):
            s.login(smtp["user"], smtp["password"])
        s.sendmail(msg["From"], [to], msg.as_string())
        s.quit()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Background jobs + scheduler
# --------------------------------------------------------------------------- #
class _Jobs:
    def __init__(self, workers=2):
        self.q = queue.Queue()
        self.processed = 0
        for _ in range(workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            fn, args, kw = self.q.get()
            try:
                fn(*args, **kw)
            except Exception:
                pass
            finally:
                self.processed += 1
                self.q.task_done()

    def enqueue(self, fn, *args, **kw):
        self.q.put((fn, args, kw))


def _cron_match(expr, t):
    """Minimal 5-field cron: minute hour day-of-month month day-of-week."""
    fields = expr.split()
    if len(fields) != 5:
        return False
    vals = [t.tm_min, t.tm_hour, t.tm_mday, t.tm_mon, t.tm_wday == 0 and 7 or t.tm_wday]
    # normalize dow: cron 0/7 = Sunday; python tm_wday Mon=0..Sun=6
    dow = 0 if t.tm_wday == 6 else t.tm_wday + 1
    vals[4] = dow
    for f, v in zip(fields, vals):
        if f == "*":
            continue
        ok = False
        for part in f.split(","):
            if part.startswith("*/"):
                if v % int(part[2:]) == 0:
                    ok = True
            elif "-" in part:
                lo, hi = map(int, part.split("-"))
                if lo <= v <= hi:
                    ok = True
            elif int(part) == v:
                ok = True
        if not ok:
            return False
    return True


class _Scheduler:
    def __init__(self):
        self.tasks = []          # (cron_expr, fn)
        self._started = False

    def add(self, expr, fn):
        self.tasks.append((expr, fn))
        self._start()

    def _start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        last_min = None
        while True:
            t = time.localtime()
            key = (t.tm_year, t.tm_yday, t.tm_hour, t.tm_min)
            if key != last_min:
                last_min = key
                for expr, fn in self.tasks:
                    if _cron_match(expr, t):
                        try: fn()
                        except Exception: pass
            time.sleep(20)


# --------------------------------------------------------------------------- #
def enable(app, cache=None, workers=2):
    """Wire cache, background jobs, scheduler, and /healthz + /metrics."""
    app.cache_store = cache or Cache()
    app.jobs = _Jobs(workers=workers)
    app.scheduler = _Scheduler()
    app._started_at = time.time()

    from larz import Response as _Resp

    def _cache_key(req, keyfn):
        return keyfn(req) if keyfn else (req.method + " " + req.path + "?" +
            "&".join("%s=%s" % kv for kv in sorted(req.query.items())))

    def cache(ttl=60, key=None):
        def deco(fn):
            def guard(req):
                k = _cache_key(req, key)
                hit = app.cache_store.get(k)
                if hit is not None:
                    body, status, ct = hit
                    return _Resp(body, status=status, content_type=ct)
                req._cache_key = (k, ttl)      # signal the after-hook to store
                return None
            # cache guard runs FIRST so a hit skips other guards/handler
            fn._larz_guards = [guard] + list(getattr(fn, "_larz_guards", []))
            return fn
        return deco
    app.cache = cache

    @app.after
    def _store_cache(req, resp):
        ck = getattr(req, "_cache_key", None)
        if ck and 200 <= resp.status < 300:
            app.cache_store.set(ck[0], (resp.body, resp.status,
                                        resp.headers.get("Content-Type")), ck[1])

    def job(fn):
        fn.enqueue = lambda *a, **k: app.jobs.enqueue(fn, *a, **k)
        return fn
    app.job = job

    def schedule(expr):
        def deco(fn):
            app.scheduler.add(expr, fn)
            return fn
        return deco
    app.schedule = schedule

    @app.get("/healthz", sitemap=False)
    def _health(req):
        return Response.json({"status": "ok", "uptime_s": int(time.time() - app._started_at)})

    @app.get("/metrics", sitemap=False)
    def _metrics(req):
        return Response.json({
            "uptime_s": int(time.time() - app._started_at),
            "routes": len(app.routes),
            "jobs_processed": app.jobs.processed,
            "jobs_queued": app.jobs.q.qsize()})

    return app
