"""
larz.security — production-minded middleware, zero-dep.

  * RateLimiter    — per-IP (or per-subject) sliding-window limits.
  * bot_filter     — drop obvious bots / invalid traffic before it hits handlers
                     (volume-and-behaviour based, not UA sniffing).
  * csrf           — double-submit-cookie CSRF protection for unsafe methods.
  * cors           — permissive-by-config CORS headers.

Each returns a hook you attach with app.before(...) / app.after(...), or a
Response to short-circuit the request.
"""

import time
import hmac
import hashlib
from collections import deque, defaultdict
from .core import Response

__all__ = ["RateLimiter", "bot_filter", "csrf_protect", "cors"]


# --------------------------------------------------------------------------- #
#  Rate limiting (sliding window, in-memory)
# --------------------------------------------------------------------------- #
class RateLimiter:
    def __init__(self, limit=60, window=60, by="ip"):
        self.limit = limit
        self.window = window
        self.by = by
        self._hits = defaultdict(deque)

    def _key(self, req):
        if self.by == "subject":
            return req.subject or req.remote_addr
        return req.remote_addr or "?"

    def __call__(self, req):
        """A before-hook: usable directly with app.use(RateLimiter(...))."""
        now = time.time()
        key = self._key(req)
        dq = self._hits[key]
        while dq and dq[0] <= now - self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            retry = int(self.window - (now - dq[0])) + 1
            return Response(
                {"error": "rate_limited", "retry_after": retry},
                status=429, headers={"Retry-After": str(retry)})
        dq.append(now)
        return None

    def hook(self):
        """Backwards-compatible: returns the before-hook (same as passing self)."""
        return self.__call__


# --------------------------------------------------------------------------- #
#  Bot / invalid-traffic filter
# --------------------------------------------------------------------------- #
_BOT_UA = ("bot", "crawl", "spider", "slurp", "curl/", "wget", "python-requests",
           "scrapy", "headlesschrome", "phantom", "semrush", "ahrefs", "bytespider",
           "gptbot", "ccbot", "dotbot", "mj12", "petalbot", "masscan", "zgrab")


def bot_filter(block_empty_ua=True, extra=()):
    patterns = tuple(p.lower() for p in _BOT_UA) + tuple(p.lower() for p in extra)

    def before(req):
        ua = (req.user_agent or "").lower()
        if block_empty_ua and not ua:
            return Response("forbidden", status=403)
        for p in patterns:
            if p in ua:
                return Response("forbidden", status=403)
        return None
    return before


# --------------------------------------------------------------------------- #
#  CSRF (double-submit cookie)
# --------------------------------------------------------------------------- #
_SAFE = {"GET", "HEAD", "OPTIONS"}


def csrf_protect(secret, cookie="larz_csrf", header="X-CSRF-Token"):
    secret = secret.encode() if isinstance(secret, str) else secret

    def _token(sid):
        return hmac.new(secret, sid.encode(), hashlib.sha256).hexdigest()[:32]

    def before(req):
        sid = req.session.get("sid", "")
        expected = _token(sid)
        req.csrf_token = expected                 # handlers can embed it in forms
        if req.method in _SAFE:
            return None
        sent = req.header(header) or req.form.get("csrf_token") or ""
        if not hmac.compare_digest(sent, expected):
            return Response({"error": "csrf_failed"}, status=403)
        return None

    def after(req, resp):
        sid = req.session.get("sid", "")
        resp.set_cookie(cookie, _token(sid), http_only=False)

    before.after = after
    return before


# --------------------------------------------------------------------------- #
#  CORS
# --------------------------------------------------------------------------- #
def cors(origin="*", methods="GET,POST,PUT,DELETE,OPTIONS", headers="Content-Type,X-CSRF-Token"):
    def after(req, resp):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = methods
        resp.headers["Access-Control-Allow-Headers"] = headers
    return after
