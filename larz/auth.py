"""
larz.auth — identity for Larz apps (zero-dep).

    from larz import Larz
    import larz.auth as auth
    app = Larz(secret="...")
    auth.enable(app)                      # default User model + session auth

    @app.login_required
    @app.get("/dashboard")
    def dash(req):
        return "hi " + req.user.email

Includes: scrypt password hashing, register/login/logout, `req.user`,
`@app.login_required`, `@app.require_role(...)`, API keys (`@app.api_key_required`,
optionally plan-gated), and signed time-limited tokens for email verification /
password reset.
"""

import os
import hmac
import time
import base64
import hashlib
import secrets

from larz import Response
from .models import Model, StrField, BoolField, DateTimeField, IntField, connect  # noqa

__all__ = ["enable", "hash_password", "verify_password", "User", "ApiKey"]


# --------------------------------------------------------------------------- #
#  Password hashing (stdlib scrypt)
# --------------------------------------------------------------------------- #
def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$%s$%s" % (base64.b64encode(salt).decode(),
                             base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Default models
# --------------------------------------------------------------------------- #
class User(Model):
    email = StrField(unique=True, index=True)
    password_hash = StrField(default="")
    role = StrField(default="user")
    verified = BoolField(default=False)
    created = DateTimeField(auto_now=True)

    def set_password(self, pw):
        self.password_hash = hash_password(pw)

    def check_password(self, pw):
        return verify_password(pw, self.password_hash)


class ApiKey(Model):
    key_hash = StrField(unique=True, index=True)
    user_id = IntField(default=None)
    plan = StrField(default="")           # optional: gates @app.metered/plan
    label = StrField(default="")
    active = BoolField(default=True)
    created = DateTimeField(auto_now=True)


def _hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


# --------------------------------------------------------------------------- #
#  Signed time-limited tokens (email verify / password reset)
# --------------------------------------------------------------------------- #
class _Tokens:
    def __init__(self, secret):
        self.secret = secret.encode() if isinstance(secret, str) else secret

    def make(self, purpose, subject, ttl=3600):
        exp = int(time.time()) + ttl
        msg = "%s:%s:%d" % (purpose, subject, exp)
        sig = hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()[:24]
        return base64.urlsafe_b64encode(("%s:%s" % (msg, sig)).encode()).decode()

    def verify(self, purpose, token):
        try:
            raw = base64.urlsafe_b64decode(token.encode()).decode()
            p, subject, exp, sig = raw.split(":")
            if p != purpose or int(exp) < time.time():
                return None
            msg = "%s:%s:%s" % (p, subject, exp)
            good = hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()[:24]
            return subject if hmac.compare_digest(good, sig) else None
        except Exception:
            return None


# --------------------------------------------------------------------------- #
#  The auth engine attached to app.auth
# --------------------------------------------------------------------------- #
class _Auth:
    def __init__(self, app, user_model, secret):
        self.app = app
        self.User = user_model
        self.tokens = _Tokens(secret or app.sessions.secret)
        user_model.create_table()
        ApiKey.create_table()
        app.before(self._load_user)

    def _load_user(self, req):
        req.user = None
        req.api_key = None
        uid = req.session.get("uid")
        if uid:
            req.user = self.User.get(uid)
        return None

    # -- account ops ------------------------------------------------------- #
    def register(self, email, password, **extra):
        if self.User.where(email=email).exists():
            raise ValueError("email already registered")
        u = self.User(email=email, **extra)
        u.set_password(password)
        return u.save()

    def login(self, req, email, password):
        u = self.User.where(email=email).first()
        if u and u.check_password(password):
            req.session["uid"] = u.id
            req.user = u
            return u
        return None

    def logout(self, req):
        req.session.pop("uid", None)
        req.user = None

    def current(self, req):
        return getattr(req, "user", None)

    # -- tokens ------------------------------------------------------------ #
    def make_verify_token(self, user, ttl=86400):
        return self.tokens.make("verify", str(user.id), ttl)

    def confirm_verify_token(self, token):
        uid = self.tokens.verify("verify", token)
        if uid:
            u = self.User.get(int(uid))
            if u:
                u.update(verified=True)
            return u
        return None

    def make_reset_token(self, user, ttl=3600):
        return self.tokens.make("reset", str(user.id), ttl)

    def reset_password(self, token, new_password):
        uid = self.tokens.verify("reset", token)
        if uid:
            u = self.User.get(int(uid))
            if u:
                u.set_password(new_password); u.save()
            return u
        return None

    # -- API keys ---------------------------------------------------------- #
    def issue_api_key(self, user=None, plan="", label=""):
        raw = "lk_" + secrets.token_urlsafe(24)
        ApiKey(key_hash=_hash_key(raw), user_id=user.id if user else None,
               plan=plan, label=label).save()
        return raw          # shown once; only the hash is stored

    def resolve_api_key(self, raw):
        k = ApiKey.where(key_hash=_hash_key(raw or ""), active=True).first()
        return k


def enable(app, user_model=None, secret=None):
    """Turn on auth. Adds `req.user`, decorators, and account helpers on app.auth."""
    um = user_model or User
    engine = _Auth(app, um, secret)
    app.auth = engine

    def login_required(fn):
        def guard(req):
            if not getattr(req, "user", None):
                if "application/json" in (req.header("Accept") or ""):
                    return Response.json({"error": "authentication required"}, status=401)
                return Response.redirect("/login")
            return None
        fn._larz_guards = list(getattr(fn, "_larz_guards", [])) + [guard]
        return fn
    app.login_required = login_required

    def require_role(role):
        def deco(fn):
            def guard(req):
                u = getattr(req, "user", None)
                if not u:
                    return Response.json({"error": "authentication required"}, status=401)
                if u.role != role:
                    return Response.json({"error": "forbidden"}, status=403)
                return None
            fn._larz_guards = list(getattr(fn, "_larz_guards", [])) + [guard]
            return fn
        return deco
    app.require_role = require_role

    def api_key_required(fn=None, plan=None):
        def deco(f):
            def guard(req):
                raw = (req.header("Authorization") or "").replace("Bearer ", "") \
                      or req.query.get("api_key", "")
                k = engine.resolve_api_key(raw)
                if not k:
                    return Response.json({"error": "invalid api key"}, status=401)
                if plan and k.plan != plan:
                    return Response.json({"error": "plan '%s' required" % plan}, status=403)
                req.api_key = k
                if k.user_id:
                    req.user = engine.User.get(k.user_id)
                return None
            f._larz_guards = list(getattr(f, "_larz_guards", [])) + [guard]
            return f
        return deco(fn) if fn else deco
    app.api_key_required = api_key_required

    return engine
