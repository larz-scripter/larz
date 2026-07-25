"""
Larz — the money-native web framework.

A small, from-scratch, dependency-free Python web framework where payments,
paywalls, and usage-metering are first-class primitives instead of bolt-ons.

    from larz import Larz
    import larz.money as money

    app = Larz(secret="...")
    money.enable(app)                 # payments on. MockProvider by default.

    @app.paid("$9/mo")
    @app.get("/pro/report")
    def report(req):
        return "<h1>Pro report</h1>"

    app.run()

See examples/paid_app.py for a full, runnable demo (no API keys required).
"""

from .core import Larz, Request, Response
from . import money, seo

__version__ = "0.1.0"
__all__ = ["Larz", "Request", "Response", "money", "seo", "__version__"]
