# Retailer purchase operations

## Record a purchase

1. Open **Research → Market Tools → Retailer Goals**.
2. Choose **Mark purchased** on the exact product.
3. Confirm unit price, quantity, date and eligible loyalty spend. Enter shipping
   once for the order line and add the retailer order number when available.
4. Submit once. A browser retry is safe: the same form submission cannot create
   a second fund entry, purchase lot or inventory receipt.

EZBay records the loyalty purchase and fund entry, creates or updates a purchase
lot, adds a canonical purchase-lot line, and receives the sealed quantity into
inventory as one transaction. Purchases from the same retailer with the same
order number share a lot. Shipping is excluded from loyalty progress but included
in the inventory's landed per-unit cost.

The purchase history links to the resulting lot. Automatically created retailer
lots are read-only there; use Retailer Goals so the fund, loyalty progress, lot
and inventory cannot be edited out of sync.

## Refund or remove a purchase

Use **Refund / remove** in Retailer Goals. EZBay refuses the reversal when the
linked holding has an active shop/eBay allocation, is reserved or sold, or no
longer contains the full purchased quantity. Withdraw the listing or resolve the
stock movement first; do not force the inventory balance.

When the stock is fully available, the action creates the refund fund entry,
removes the received quantity, reverses eligible spend and changes the linked lot
to refunded after its last active line is refunded. A zero-quantity holding is
retained as returned history and is reused if the same product is purchased again.

## Non-destructive rehearsal

Run:

```bash
.venv/bin/python -m scripts.retailer_purchase_drill
```

The drill uses a temporary database and makes no retailer, payment, eBay or other
external calls. It verifies the lot and inventory links, landed cost, fund ledger,
allocation guard, refund reversal and repurchase reuse.
