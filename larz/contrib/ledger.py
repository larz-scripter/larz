"""
larz.contrib.ledger — double-entry books, powered by larzledger.

Money apps move money; a real one keeps books. With the `money` extra, this adds
``app.ledger`` (a double-entry Ledger that must always balance) plus convenience
helpers to record the two most common events — a sale and a refund — as proper
balanced postings.

    from larz.contrib import ledger
    ledger.enable(app)                       # app.ledger, app.record_sale, ...

    app.record_sale("Pro plan", 99.00)       # Dr Cash / Cr Revenue
    app.ledger.trial_balance()               # always balances
"""
from . import require


def _lib():
    require("larzledger", "money")
    import larzledger
    return larzledger


def new_ledger(currency="$", cash="Cash", revenue="Revenue",
               refunds="Refunds", fees="Payment fees"):
    """Create a Ledger pre-seeded with the accounts a SaaS/marketplace needs."""
    larzledger = _lib()
    lg = larzledger.Ledger(currency=currency)
    lg.account(cash, "asset")
    lg.account(revenue, "income")
    lg.account(refunds, "income")
    lg.account(fees, "expense")
    lg._larz_accounts = dict(cash=cash, revenue=revenue,
                             refunds=refunds, fees=fees)
    return lg


def enable(app, currency="$"):
    """Attach ``app.ledger`` and record_sale / record_refund / record_fee
    helpers that post standard balanced double entries."""
    lg = new_ledger(currency=currency)
    acc = lg._larz_accounts
    app.ledger = lg

    def record_sale(description, amount, date=None):
        # Debit Cash (asset up), Credit Revenue (income up)
        return lg.post(description, {acc["cash"]: amount},
                       {acc["revenue"]: amount}, date=date)

    def record_refund(description, amount, date=None):
        # Debit Refunds (income down), Credit Cash (asset down)
        return lg.post(description, {acc["refunds"]: amount},
                       {acc["cash"]: amount}, date=date)

    def record_fee(description, amount, date=None):
        # Debit Payment fees (expense up), Credit Cash (asset down)
        return lg.post(description, {acc["fees"]: amount},
                       {acc["cash"]: amount}, date=date)

    app.record_sale = record_sale
    app.record_refund = record_refund
    app.record_fee = record_fee
    return lg
