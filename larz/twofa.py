"""
larz.twofa — TOTP two-factor authentication, zero-dependency (RFC 6238 / 4226).

    import larz.auth as auth, larz.twofa as twofa
    auth.enable(app); twofa.enable(app)

    # enrolment
    uri, secret = app.twofa.begin(req.user)      # show a QR of `uri`
    codes = app.twofa.activate(req.user, code)   # returns one-time backup codes

    # at login, after the password step:
    if app.twofa.is_enabled(user):
        # ...show a 6-digit challenge, then:
        if app.twofa.verify(user, code):
            app.twofa.mark_verified(req)

    @app.twofa_required            # gate sensitive routes on a passed challenge
    @app.get("/billing")
    def billing(req): ...

Everything here is standard-library only: hashlib (sha1/hmac), base64 (b32),
struct, secrets. Compatible with Google Authenticator, Authy, 1Password, etc.
"""

import hmac
import time
import json
import base64
import struct
import hashlib
import secrets

from larz import Response
from .models import Model, StrField, BoolField, IntField, DateTimeField

__all__ = ["enable", "generate_secret", "provisioning_uri", "verify_code", "now_code"]


# --------------------------------------------------------------------------- #
#  Pure TOTP primitives (RFC 6238)
# --------------------------------------------------------------------------- #
def generate_secret(length=20):
    """A fresh base32 secret (no padding), the format authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def _b32key(secret):
    pad = "=" * ((-len(secret)) % 8)
    return base64.b32decode(secret.upper() + pad, casefold=True)


def _hotp(secret, counter, digits=6):
    key = _b32key(secret)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = ((mac[off] & 0x7F) << 24 | (mac[off + 1] & 0xFF) << 16
            | (mac[off + 2] & 0xFF) << 8 | (mac[off + 3] & 0xFF)) % (10 ** digits)
    return str(code).zfill(digits)


def now_code(secret, period=30, digits=6, at=None):
    """The current 6-digit code for a secret (used to demo/test enrolment)."""
    at = time.time() if at is None else at
    return _hotp(secret, int(at // period), digits)


def verify_code(secret, code, period=30, digits=6, window=1, at=None):
    """True if `code` is valid now (± `window` steps of clock drift)."""
    if not code or not str(code).isdigit():
        return False
    code = str(code).zfill(digits)
    at = time.time() if at is None else at
    counter = int(at // period)
    for drift in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret, counter + drift, digits), code):
            return True
    return False


def provisioning_uri(secret, account_name, issuer="Larz", period=30, digits=6):
    """otpauth:// URI to render as a QR for authenticator apps."""
    from urllib.parse import quote, urlencode
    # standard otpauth label: "Issuer:account" with the colon separator literal
    label = "%s:%s" % (quote(issuer, safe=""), quote(account_name, safe=""))
    q = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                   "digits": digits, "period": period})
    return "otpauth://totp/%s?%s" % (label, q)


# --------------------------------------------------------------------------- #
#  Storage model
# --------------------------------------------------------------------------- #
class TwoFactor(Model):
    user_id = IntField(index=True)
    secret = StrField(default="")
    enabled = BoolField(default=False)
    backup_codes = StrField(default="")      # json list of unused one-time codes
    created = DateTimeField(auto_now=True)


# --------------------------------------------------------------------------- #
#  App integration
# --------------------------------------------------------------------------- #
class _TwoFactorManager:
    def __init__(self, app, issuer, challenge_path):
        self.app = app
        self.issuer = issuer
        self.challenge_path = challenge_path
        TwoFactor.create_table()

    def _row(self, user):
        return TwoFactor.where(user_id=user.id).first()

    def begin(self, user, backup_count=8):
        """Create (or reset) a *pending* secret for a user and return
        (provisioning_uri, secret). 2FA is not active until activate()."""
        row = self._row(user) or TwoFactor(user_id=user.id)
        row.secret = generate_secret()
        row.enabled = False
        row.backup_codes = ""
        row.save()
        uri = provisioning_uri(row.secret, getattr(user, "email", str(user.id)), self.issuer)
        return uri, row.secret

    def activate(self, user, code, backup_count=8):
        """Confirm enrolment with a code from the app; on success turn 2FA on
        and return a fresh list of one-time backup codes (show them once)."""
        row = self._row(user)
        if not row or not row.secret:
            return None
        if not verify_code(row.secret, code):
            return None
        codes = ["%08d" % secrets.randbelow(10 ** 8) for _ in range(backup_count)]
        row.backup_codes = json.dumps(codes)
        row.enabled = True
        row.save()
        return codes

    def is_enabled(self, user):
        row = self._row(user)
        return bool(row and row.enabled)

    def verify(self, user, code):
        """Verify a TOTP code OR consume a one-time backup code."""
        row = self._row(user)
        if not row or not row.enabled:
            return False
        if verify_code(row.secret, code):
            return True
        try:
            codes = json.loads(row.backup_codes or "[]")
        except Exception:
            codes = []
        code = str(code).strip()
        if code in codes:
            codes.remove(code)                      # single use
            row.backup_codes = json.dumps(codes)
            row.save()
            return True
        return False

    def disable(self, user):
        row = self._row(user)
        if row:
            row.delete()

    # -- session challenge state --------------------------------------------- #
    def mark_verified(self, req):
        req.session["2fa_ok"] = True

    def clear(self, req):
        req.session.pop("2fa_ok", None)

    def passed(self, req):
        return bool(req.session.get("2fa_ok"))


def enable(app, issuer="Larz", challenge_path="/2fa"):
    """Attach app.twofa and register the @app.twofa_required guard.

    A route marked @app.twofa_required is served only when the current user
    either has no 2FA enabled, or has passed the challenge this session;
    otherwise the request is redirected to `challenge_path`.
    """
    mgr = _TwoFactorManager(app, issuer, challenge_path)
    app.twofa = mgr

    def twofa_required(fn):
        def guard(req):
            user = getattr(req, "user", None)
            if user is None:
                return None                         # login_required handles auth
            if mgr.is_enabled(user) and not mgr.passed(req):
                return Response.redirect(challenge_path)
            return None
        guards = getattr(fn, "_larz_guards", [])
        guards.append(guard)
        fn._larz_guards = guards
        return fn

    app.twofa_required = twofa_required
    return mgr
