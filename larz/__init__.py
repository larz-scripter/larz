"""
Larz — the money-native web framework.

A small, from-scratch, dependency-free Python web framework where payments,
paywalls, subscriptions, trials, and usage-metering are first-class primitives
instead of bolt-ons. Batteries included: a template engine, a tiny ORM, static
files, blueprints, security middleware, SEO, and a CLI — all pure stdlib.

    from larz import Larz
    import larz.money as money

    app = Larz(secret="...")
    money.enable(app)                 # payments on. MockProvider by default.

    @app.paid("$9/mo", trial_days=7)
    @app.get("/pro/report")
    def report(req):
        return "<h1>Pro report</h1>"

    app.run()

See examples/ for runnable demos (paid_app.py, saas_app.py — no API keys).
"""

from .core import Larz, Request, Response, Blueprint
from .templating import Template, Environment
from .models import Model, Field, connect
from . import money, seo, providers, security, templating, models

__version__ = "0.2.0"
__all__ = ["Larz", "Request", "Response", "Blueprint",
           "Template", "Environment", "Model", "Field", "connect",
           "money", "seo", "providers", "security", "templating", "models",
           "__version__"]
