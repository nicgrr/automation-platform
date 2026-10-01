# Retailer Goals

`/retailer-goals` is EzBay's retailer loyalty and planned-purchasing module.
Gameology Guild is the first program, but the schema is retailer-neutral.

## Architecture

- `Retailer` records trust status and whether a retailer may establish the
  Best Reputable AU comparison price.
- `RetailerProgram`, `RetailerMilestone`, `RetailerBudget` and
  `RetailerFundEntry` hold the loyalty target and its auditable fund ledger.
- `RetailerProduct` links queue entries to the existing `CatalogItem` /
  `SealedProduct` catalogue rather than creating a second product catalogue.
- `RetailerPriceSnapshot` records retailer, price, stock, URL, source and time.
  This is separate from `CardPrice` because the existing table has no retailer,
  trust, URL or stock fields.
- `RetailerPurchase` records eligible spend, the market-price snapshot and the
  Guild premium. If inventory is requested it links to the existing
  `InventoryItem` row and updates its quantity and per-unit cost basis.

All money is `Decimal`/`NUMERIC`, rounded to cents. Shipping is recorded but is
excluded from eligible spend and fund spending by default.

## Weekly fund

The next due contribution date is stored on the budget. Opening the tracker or
recording a purchase catches up each due week exactly once using a unique ledger
key. This avoids a new background worker and also survives server downtime.
Emergency pause prevents contributions; resuming starts from the next selected
weekday rather than adding the paused weeks retroactively.

## Trust and recommendations

Only a retailer that is both `trusted` and `best_price_eligible` can establish
Best Reputable AU. Unverified and blocked prices remain visible in history but
do not affect premium calculations. The recommendation score uses fund
affordability, premium band, stock, status, priority, monthly/per-product limits
and the queue item's maximum acceptable price. It makes no return forecast and
does not place orders.

## Automation boundary

The product, snapshot and notification-preference tables are ready for future
price/restock/preorder monitors. No monitor is enabled yet, and there is no code
that places an order or initiates a payment.
