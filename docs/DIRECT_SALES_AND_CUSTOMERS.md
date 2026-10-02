# Direct sales and customers

Use **Sales ledger & direct sale** from the command palette, or **View all** on
the dashboard's Recent Sales card. Customer records are available beside it in
the command palette.

## Record a sale

1. Choose the channel (Direct, Facebook, In Person, Instagram, Trade Show or
   Other). Whatnot, eBay and NL Vault must use their own order workflows so a
   transaction cannot be entered twice.
2. Select the exact owned inventory holding and quantity. Stock allocated to an
   NL Vault/eBay listing is not offered and is rejected again when submitted.
3. Enter the actual sale price, fees, shipping, packaging and tax. Cost basis is
   copied from inventory; it is never guessed or re-entered.
4. Add the customer's name and/or email when known. EZBay reuses a normalized
   email first, then a case-insensitive name, and fills missing contact details.
5. Submit. Retrying the same browser submission is safe and cannot decrement
   stock or create revenue twice.

The sale appears in the shared sales ledger, dashboard and analytics. Customer
orders and lifetime value include completed sales only.

## Correct a mistaken direct sale

Use **Void** on the sale row. Only a completed direct sale can be voided there;
channel-managed records cannot be changed from the manual screen. A successful
void restores every linked inventory quantity exactly once and removes the sale
from revenue, profit and customer-value totals while retaining its audit history.
If the stock has since moved into another allocation state, resolve that movement
before voiding.

## Non-destructive rehearsal

Run:

```bash
.venv/bin/python -m scripts.direct_sale_drill
```

The drill uses a temporary database and makes no marketplace, payment or other
external calls. It verifies atomic stock decrement, customer linkage, cost
snapshot, duplicate-submit protection and one-time void restoration.
