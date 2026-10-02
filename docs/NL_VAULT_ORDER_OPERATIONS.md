# NL Vault order operations

## Order lifecycle

1. Checkout creates a pending order and atomically reserves its stock before the
   buyer leaves for Stripe. The available storefront and linked inventory counts
   fall immediately.
2. A signed Stripe webhook (or verified success-page reconciliation) changes the
   order from **Pending payment** to **Paid**. Payment does not subtract stock a
   second time. The confirmed quantity is then queued for eBay synchronisation;
   a temporary reservation never ends an eBay offer by itself.
3. The Shop orders screen advances the order through **Paid → Packed → Shipped**.
   A tracking number is required to mark it shipped.
4. Stripe session expiry or operator cancellation restores the reservation once.
   For a Stripe-backed order, EZBay verifies and expires the open Stripe Checkout
   Session before making the stock saleable again.
5. Refunds are completed in Stripe or the bank first, then recorded in EZBay.
   Tick **Returned stock is back in hand** only after physically receiving and
   checking the goods; otherwise the refund changes order status without adding
   stock.

## Safety rules

- Never use **Cancel & restore stock** for a payment received outside EZBay. Use
  **Record manual payment** after ticking **Funds received**, then continue
  fulfilment or record a refund.
- Do not tick the refund restock box for lost shipments, goodwill refunds, or an
  item the buyer still has.
- Repeated payment webhooks, expiry webhooks, cancellation clicks and restoration
  attempts are guarded so they cannot subtract or add the same stock twice.
- A completed Stripe Checkout Session blocks cancellation as unpaid. Reconcile
  the payment, then use the refund workflow if necessary.

## Non-destructive rehearsal

Run:

```bash
.venv/bin/python -m scripts.storefront_transaction_drill
```

The drill uses a temporary database, locally signed fake Stripe events and
disabled eBay synchronisation. It checks reservation, payment, packing, shipping,
cancellation restoration and refund restocking without touching production or
making an external request.
