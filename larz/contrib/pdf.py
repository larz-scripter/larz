"""
larz.contrib.pdf — PDF invoices and receipts, powered by larzpdf.

A money framework should be able to hand a customer a real document. With the
`money` extra installed, this adds ``app.invoice(...)`` and ``app.receipt(...)``
that return a downloadable PDF Response with exact, correctly-totalled amounts.

    from larz.contrib import pdf
    pdf.enable(app)

    @app.get("/invoice/<n>")
    def inv(req, n):
        return app.invoice(n,
            items=[("Pro plan (annual)", 1, "99.00"), ("Setup", 1, "20.00")],
            seller={"name": "Acme"}, buyer={"name": req.user.email},
            tax=0.08, download="invoice-%s.pdf" % n)
"""
from . import require
from ..core import Response


def _lib():
    require("larzpdf", "money")
    import larzpdf
    return larzpdf


def _norm_items(items):
    for it in items:
        if isinstance(it, dict):
            yield it.get("description", ""), it.get("qty", 1), it.get("price", 0)
        else:
            desc = it[0]
            qty = it[1] if len(it) > 1 else 1
            price = it[2] if len(it) > 2 else 0
            yield desc, qty, price


def build_invoice(number, items, seller=None, buyer=None, currency="$",
                  tax=None, tax_label="Tax", notes=None, date=None,
                  receipt=False, paid=True):
    """Return PDF *bytes* for an invoice (or receipt). ``items`` is a list of
    ``(description, qty, price)`` tuples or ``{"description","qty","price"}``
    dicts; larzpdf totals them exactly."""
    larzpdf = _lib()
    common = dict(number=number, date=date, seller=seller or {},
                  buyer=buyer or {}, currency=currency, notes=notes)
    doc = (larzpdf.Receipt(paid=paid, **common) if receipt
           else larzpdf.Invoice(**common))
    for desc, qty, price in _norm_items(items):
        doc.item(desc, qty, price)
    if tax is not None:
        doc.tax(tax, tax_label)
    return doc.render()


def build_receipt(number, items, **kw):
    """Return PDF bytes for a paid receipt."""
    kw["receipt"] = True
    return build_invoice(number, items, **kw)


def _response(pdf_bytes, download=None):
    headers = {}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="%s"' % download
    return Response(pdf_bytes, headers=headers, content_type="application/pdf")


def enable(app):
    """Attach ``app.invoice(...)`` / ``app.receipt(...)`` (return a PDF Response)
    and ``app.build_invoice(...)`` (returns raw bytes)."""
    def invoice(number, items, download=None, **kw):
        return _response(build_invoice(number, items, **kw), download)

    def receipt(number, items, download=None, **kw):
        return _response(build_receipt(number, items, **kw), download)

    app.invoice = invoice
    app.receipt = receipt
    app.build_invoice = build_invoice
    return app
