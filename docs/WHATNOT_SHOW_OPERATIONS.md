# Whatnot show operations

## Before the show

1. Open **Sell → Whatnot** and create a draft with the same category used on
   Whatnot. Category matters when EZBay estimates fees.
2. Add owned inventory from the eligible-stock selector. EZBay immediately
   reserves one unit. Stock already allocated to NL Vault or a live/in-flight
   eBay offer is deliberately excluded; withdraw it from that channel first.
3. Confirm every queued item, starting price and physical location, then press
   **Start show**. An empty show cannot start.

## During and after the show

- **Sold:** enter the final item price and buyer handle. Prefer the exact fee
  from Whatnot Seller Hub. Add the Whatnot order ID when available; duplicate
  IDs are rejected. Buyer-paid shipping and tax are only used when estimating
  processing fees, because Whatnot calculates processing on the whole buyer
  payment. The resulting sale and customer appear automatically in Sales and
  Customers, and the reserved inventory becomes sold.
- **Unsold:** the reserved unit returns to available inventory exactly once.
- **Giveaway:** the unit leaves inventory as a giveaway and no revenue sale is
  created.
- **Complete show:** available only after every queued item has an outcome.
  Record peak viewers and the post-show follower count if known.
- **Cancel:** all still-pending reservations are marked unsold and restored;
  already settled sales and giveaways are never reversed.

## Fee safety

The exact Seller Hub fee is the accounting source of truth. If it is left blank,
EZBay requires an active dated fee rule and labels the result as an estimate. It
never records an invented zero fee. Australian Whatnot commission is tiered and
GST treatment depends on the owner's registration, so update rules when Seller
Hub changes the account's locked 28-day rate.

## Non-destructive rehearsal

Run:

```bash
.venv/bin/python -m scripts.whatnot_show_drill
```

The drill uses only a temporary database. It rehearses reservation, show start,
sale/fee/customer handoff, unsold restoration, giveaway depletion, completion
and cancellation without contacting Whatnot, eBay or any other external service.
