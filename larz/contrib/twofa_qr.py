"""
larz.contrib.twofa_qr — two-factor enrollment QR, powered by larztotp + larzqr.

The framework has 2FA; this gives users the *scannable* enrollment experience.
With the `auth` extra, ``app.twofa_enroll(email)`` returns a fresh TOTP secret
plus an SVG QR code any authenticator app can scan — no image library involved.

    from larz.contrib import twofa_qr
    twofa_qr.enable(app, issuer="MyApp")

    secret, svg = app.twofa_enroll(req.user.email)   # store secret, show svg
    ok = app.twofa_verify(secret, req.form["code"])  # at login
"""
from . import require


def _libs():
    require("larztotp", "auth")
    require("larzqr", "auth")
    import larztotp
    import larzqr
    return larztotp, larzqr


def enrollment(account, issuer=None, secret=None, error_correction="M"):
    """Return ``(secret, svg)`` — a TOTP secret and a scannable enrollment QR as
    an SVG string. Omit ``secret`` to generate a fresh one, or pass an existing
    one to re-render its QR."""
    larztotp, larzqr = _libs()
    secret = secret or larztotp.generate_secret()
    uri = larztotp.provisioning_uri(secret, account, issuer=issuer)
    svg = larzqr.QR(uri, error_correction=error_correction).to_svg()
    return secret, svg


def verify(secret, code, window=1):
    """True if ``code`` is currently valid for ``secret`` (± ``window`` steps)."""
    larztotp, _ = _libs()
    return larztotp.TOTP(secret).verify(code, window=window)


def enable(app, issuer=None):
    """Attach ``app.twofa_enroll(account)`` and ``app.twofa_verify(secret, code)``."""
    def enroll(account, secret=None):
        return enrollment(account, issuer=issuer, secret=secret)

    def vfy(secret, code, window=1):
        return verify(secret, code, window=window)

    app.twofa_enroll = enroll
    app.twofa_verify = vfy
    return app
