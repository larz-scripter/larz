# Wiring Larz to the estate's GemVault rail

`larz.providers.GemVaultProvider` is verified against the estate's live GemVault
`mkt.py`. This is how an estate app (EarnifyHub shop, a LarzOS Pro tier, the AI
Gateway's metered billing) dogfoods Larz for real card + crypto payments.

```python
from larz import Larz
import larz.money as money
from larz.providers import GemVaultProvider

gv = GemVaultProvider(
    app="eh_shop",                       # your GemVault merchant/app id (must be
                                         # in GemVault's DODO_ALLOWED_APPS for card)
    api_base="https://gemvault.larzpay.com",   # GEMVAULT_API_URL
    token="<server-to-server token>",    # same token the PHP callers use
    secret="<EH_SHOP_GV_SECRET>",        # webhook HMAC secret for this app
)

app = Larz(secret="...")
money.enable(app, provider=gv, base_url="https://yourapp.com",
             db="/var/data/yourapp_money.db")

@app.paid("$19")
@app.get("/pro/toolkit")
def toolkit(req):
    return render_toolkit(req)
```

## How it maps to GemVault

| Larz | GemVault |
|---|---|
| `create_checkout(subject, sku, cents, …)` | `POST {api}/api/mkt/dodo/checkout` `{app, uid, amount, return_url}` → `{checkout_url}` |
| `parse_webhook(req)` | verifies `X-GV-Signature = HMAC-SHA256(raw_body, secret)`, reads `uid` + `usd_amount` |

GemVault only round-trips `uid`, so Larz packs `subject|sku` into it and unpacks
on the webhook — that's what lets the framework grant the right entitlement.

## Point GemVault's webhook at Larz

Configure this app's GemVault webhook URL to:

    https://yourapp.com/larz/webhook/gemvault

Larz's built-in `/larz/webhook/<provider>` route verifies the signature, grants
the entitlement (or credits a credit-pack purchase), and records the payment —
no webhook code to write.

## Notes / caveats

* **Card vs crypto:** the `/api/mkt/dodo/checkout` path is the productized card
  flow and requires the app to be allowlisted (`DODO_ALLOWED_APPS`). Crypto/wallet
  apps (cryptolarz, larzpay) are intentionally *not* allowlisted for card.
* **Subject identity:** by default `subject` is the anonymous session id. For a
  logged-in app set `req.session["user"] = <your uid>` after login so entitlements
  bind to the real account, not the browser session.
* **Idempotency:** GemVault may re-deliver a webhook; `record_payment` uses the
  provider payment id as the primary key, so re-delivery is a harmless upsert.
* **DB location:** point `money.enable(db=...)` at durable storage, not `/tmp`.
