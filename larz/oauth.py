"""
larz.oauth — social login (Google, GitHub, or any OAuth2/OIDC provider) with
zero dependencies. The whole flow runs over urllib.

    import larz.auth as auth, larz.oauth as oauth
    auth.enable(app)
    oauth.enable(app, providers={
        "google": {"client_id": "...", "client_secret": "..."},
        "github": {"client_id": "...", "client_secret": "..."},
    })
    # links appear at /larz/oauth/google/login and /larz/oauth/github/login

By default a successful login finds-or-creates a larz.auth `User` by email and
logs them in. Pass on_login(req, email, profile, provider) to customise (return
a Response to override the redirect).

Presets are provided for Google and GitHub; any other provider works by passing
authorize_url / token_url / userinfo_url / scope / email_field explicitly.
"""

import json
import secrets
import urllib.parse
import urllib.request

from larz import Response

__all__ = ["enable", "PRESETS"]

PRESETS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
        "email_field": "email",
    },
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "emails_url": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
        "email_field": "email",
    },
}


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Accept": "application/json",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    try:
        return json.loads(raw)
    except ValueError:                         # some providers return urlencoded
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def _get_json(url, token, ua="larz-oauth"):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
        "User-Agent": ua})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _default_on_login(app):
    def handler(req, email, profile, provider):
        if not getattr(app, "auth", None):
            return None
        from .auth import User
        user = User.where(email=email).first()
        if not user:
            user = User(email=email, verified=True)
            user.save()
        req.session["user"] = str(user.id)
        return None
    return handler


def enable(app, providers, base_url=None, prefix="/larz/oauth",
           success_path="/", on_login=None):
    """Register OAuth login + callback routes for each configured provider."""
    base_url = (base_url or getattr(getattr(app, "money", None), "base_url", None)
                or "http://127.0.0.1:8000").rstrip("/")
    on_login = on_login or _default_on_login(app)
    cfg = {}
    for name, opts in providers.items():
        merged = dict(PRESETS.get(name, {}))
        merged.update(opts)
        cfg[name] = merged

    def redirect_uri(name):
        return "%s%s/%s/callback" % (base_url, prefix, name)

    def login(req):
        name = req.params["provider"]
        c = cfg.get(name)
        if not c:
            return Response("unknown provider", status=404)
        state = secrets.token_urlsafe(24)
        req.session["_oauth_state"] = state
        req.session["_oauth_provider"] = name
        q = urllib.parse.urlencode({
            "client_id": c["client_id"], "redirect_uri": redirect_uri(name),
            "response_type": "code", "scope": c.get("scope", "email"),
            "state": state, "access_type": "offline", "prompt": "select_account"})
        return Response.redirect(c["authorize_url"] + "?" + q)

    def callback(req):
        name = req.params["provider"]
        c = cfg.get(name)
        if not c:
            return Response("unknown provider", status=404)
        if not req.query.get("code"):
            return Response("missing code", status=400)
        if req.query.get("state") != req.session.get("_oauth_state"):
            return Response("bad state", status=400)         # CSRF guard
        token_resp = _post_form(c["token_url"], {
            "client_id": c["client_id"], "client_secret": c["client_secret"],
            "code": req.query["code"], "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(name)})
        access = token_resp.get("access_token")
        if not access:
            return Response("token exchange failed", status=400)
        profile = _get_json(c["userinfo_url"], access)
        email = profile.get(c.get("email_field", "email"))
        if not email and c.get("emails_url"):                # GitHub: emails endpoint
            for e in _get_json(c["emails_url"], access):
                if e.get("primary") and e.get("email"):
                    email = e["email"]; break
        if not email:
            return Response("no email from provider", status=400)
        req.session.pop("_oauth_state", None)
        out = on_login(req, email, profile, name)
        return out if isinstance(out, Response) else Response.redirect(success_path)

    app.route(prefix + "/<provider>/login")(login)
    app.route(prefix + "/<provider>/callback")(callback)
    app.oauth = cfg
    return cfg
