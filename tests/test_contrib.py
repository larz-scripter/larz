"""Tests for larz.contrib adapters (the framework x stack integration).

The Larz Stack libraries live as sibling repos on this box; we add them to
sys.path so the adapters actually execute. Each test skips gracefully if its
backing library isn't importable, so the suite stays green on a bare install.
"""
import os
import sys
import unittest

# make the sibling stack repos importable
_HOME = "/home/elevenace"
for _p in ("larzpdf", "larzledger", "larztotp", "larzqr", "larzagent", "larzmoney"):
    _d = os.path.join(_HOME, _p)
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)

from larz import Larz, contrib
from larz.contrib import pdf, ledger, twofa_qr, agents


def _have(mod):
    return contrib.available(mod)


class TestRequire(unittest.TestCase):
    def test_available_false_for_missing(self):
        self.assertFalse(contrib.available("larz_nope_xyz"))

    def test_require_raises_with_hint(self):
        try:
            contrib.require("larz_nope_xyz", "money")
            self.fail("expected ImportError")
        except ImportError as e:
            self.assertIn("pip install larz[money]", str(e))

    def test_core_stays_zero_dep(self):
        # importing larz.contrib must never force a stack library
        self.assertTrue(hasattr(contrib, "require"))


@unittest.skipUnless(_have("larzpdf"), "larzpdf not installed")
class TestPdf(unittest.TestCase):
    def test_build_invoice_bytes(self):
        data = pdf.build_invoice("INV-1",
            items=[("Pro plan", 1, "99.00"), ("Setup", 2, "10.00")],
            seller={"name": "Acme"}, buyer={"name": "a@b.com"}, tax=0.10)
        self.assertIsInstance(data, (bytes, bytearray))
        self.assertTrue(data[:5] == b"%PDF-")

    def test_enable_invoice_response(self):
        app = Larz(secret="t")
        pdf.enable(app)
        resp = app.invoice("INV-2", items=[("Item", 1, "5.00")],
                           download="inv.pdf")
        self.assertEqual(resp.headers["Content-Type"], "application/pdf")
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertTrue(bytes(resp.body)[:5] == b"%PDF-")

    def test_receipt(self):
        app = Larz(secret="t")
        pdf.enable(app)
        resp = app.receipt("R-1", items=[("Paid item", 1, "20.00")])
        self.assertEqual(resp.headers["Content-Type"], "application/pdf")

    def test_dict_items(self):
        data = pdf.build_invoice("INV-3",
            items=[{"description": "X", "qty": 3, "price": "4.00"}])
        self.assertTrue(data[:5] == b"%PDF-")


@unittest.skipUnless(_have("larzledger"), "larzledger not installed")
class TestLedger(unittest.TestCase):
    def test_record_sale_balances(self):
        app = Larz(secret="t")
        lg = ledger.enable(app)
        app.record_sale("Pro plan", "99.00")
        self.assertTrue(lg.is_balanced())
        self.assertEqual(str(lg.balance("Cash")), "99.00")
        self.assertEqual(str(lg.balance("Revenue")), "99.00")

    def test_refund_and_fee(self):
        app = Larz(secret="t")
        lg = ledger.enable(app)
        app.record_sale("Sale", "100.00")
        app.record_refund("Oops", "30.00")
        app.record_fee("Processor", "3.00")
        self.assertTrue(lg.is_balanced())
        # cash: +100 -30 -3 = 67
        self.assertEqual(str(lg.balance("Cash")), "67.00")

    def test_trial_balance_present(self):
        app = Larz(secret="t")
        lg = ledger.enable(app)
        app.record_sale("S", "10.00")
        rows = lg.trial_balance()
        self.assertTrue(any(r[0] == "Cash" for r in rows))


@unittest.skipUnless(_have("larztotp") and _have("larzqr"),
                     "larztotp/larzqr not installed")
class TestTwofaQr(unittest.TestCase):
    def test_enroll_returns_secret_and_svg(self):
        app = Larz(secret="t")
        twofa_qr.enable(app, issuer="MyApp")
        secret, svg = app.twofa_enroll("alice@example.com")
        self.assertTrue(secret)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_verify_current_code(self):
        import larztotp
        app = Larz(secret="t")
        twofa_qr.enable(app, issuer="MyApp")
        secret, _ = app.twofa_enroll("bob@example.com")
        code = larztotp.TOTP(secret).now()
        self.assertTrue(app.twofa_verify(secret, code))
        self.assertFalse(app.twofa_verify(secret, "000000"))

    def test_rerender_with_existing_secret(self):
        app = Larz(secret="t")
        twofa_qr.enable(app)
        s1, _ = app.twofa_enroll("c@x.com")
        s2, svg = app.twofa_enroll("c@x.com", secret=s1)
        self.assertEqual(s1, s2)
        self.assertIn("<svg", svg)


@unittest.skipUnless(_have("larzagent"), "larzagent not installed")
class TestAgents(unittest.TestCase):
    def test_enable_attaches(self):
        app = Larz(secret="t")
        agents.enable(app, model="claude-sonnet-5", api_key="x")
        self.assertTrue(callable(app.agent))
        self.assertTrue(callable(app.ask))

    def test_agent_builds(self):
        import larzagent
        app = Larz(secret="t")
        agents.enable(app, model="claude-sonnet-5", api_key="x")
        a = app.agent(system="be brief")
        self.assertIsInstance(a, larzagent.Agent)
        self.assertTrue(hasattr(a, "run"))


if __name__ == "__main__":
    unittest.main()
