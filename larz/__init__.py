"""
Larz — the money-native web framework.

A from-scratch, dependency-free Python web framework where payments, paywalls,
subscriptions, and usage-metering are first-class primitives — now with a full
SaaS backend: auth + API keys, an ORM with relationships & migrations, an
auto-admin panel, background jobs, caching, email, and more. Zero dependencies.

    pip install larz

    from larz import Larz
    import larz.money as money, larz.auth as auth

    app = Larz(secret="...")
    money.enable(app); auth.enable(app)

    @app.login_required
    @app.paid("$9/mo", trial_days=7)
    @app.get("/pro")
    def pro(req):
        return "hi " + req.user.email

    app.run()

Modules: money, auth, api, providers, security, ops, admin, seo, templating,
models. See examples/ for runnable demos.
"""

from .core import Larz, Request, Response, Blueprint
from .templating import Template, Environment
from .models import Model, Field, connect
from . import (money, seo, providers, security, templating, models,
               auth, api, ops, admin)

__version__ = "1.0.1"
__all__ = ["Larz", "Request", "Response", "Blueprint",
           "Template", "Environment", "Model", "Field", "connect",
           "money", "seo", "providers", "security", "templating", "models",
           "auth", "api", "ops", "admin", "__version__"]
