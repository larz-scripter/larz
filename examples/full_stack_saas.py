"""Full-stack money-native SaaS: the Larz framework + the Larz Stack.

    pip install larz[full]
    python full_stack_saas.py

The framework core is zero-dependency; the stack lights up through extras and
larz.contrib. This one file wires a Pro subscription, a double-entry revenue
ledger, PDF invoices, and 2FA enrollment QR codes — each backed by a stack
library, none of it hand-rolled.
"""
from larz import Larz
import larz.money as money
from larz.contrib import pdf, ledger, twofa_qr

app = Larz(secret="change-me")
money.enable(app)                       # payments (keyless MockProvider in dev)
ledger.enable(app)                      # app.ledger + record_sale/refund/fee   (larzledger)
pdf.enable(app)                         # app.invoice / app.receipt             (larzpdf)
twofa_qr.enable(app, issuer="AcmeSaaS") # app.twofa_enroll / app.twofa_verify   (larztotp + larzqr)


@app.get("/")
def home(req):
    return ("<h1>AcmeSaaS</h1>"
            "<a href='/pro'>Go Pro</a> · <a href='/invoice'>Invoice (PDF)</a> · "
            "<a href='/2fa'>Enable 2FA</a> · <a href='/revenue'>Books</a>")


@app.paid("$9/mo", trial_days=7)        # framework paywall — checkout is automatic
@app.get("/pro")
def pro(req):
    app.record_sale("Pro subscription", "9.00")   # books stay balanced
    return "Welcome to Pro! Revenue booked to the ledger."


@app.get("/invoice")
def invoice(req):
    return app.invoice("INV-1001",
        items=[("Pro plan (annual)", 1, "99.00"), ("Onboarding", 1, "49.00")],
        seller={"name": "AcmeSaaS", "email": "billing@acme.test"},
        buyer={"name": "customer@example.com"},
        tax=0.08, download="acme-invoice-1001.pdf")   # a real PDF download


@app.get("/2fa")
def twofa(req):
    secret, svg = app.twofa_enroll("customer@example.com")
    return "<h1>Scan to enable 2FA</h1>" + svg + "<p><code>%s</code></p>" % secret


@app.get("/revenue")
def revenue(req):
    rows = app.ledger.trial_balance()
    out = ["<h1>Books</h1><table><tr><th>Account</th><th>Debit</th><th>Credit</th></tr>"]
    for name, typ, dr, cr in rows:
        out.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (name, dr, cr))
    out.append("</table><p>Balanced: <b>%s</b></p>" % app.ledger.is_balanced())
    return "".join(out)


if __name__ == "__main__":
    app.run()
