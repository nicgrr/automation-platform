# EZBay operator guide

This is the current map of the platform. Start at **Dashboard** for work waiting,
service health, recovery evidence and recent sales. Use the five workflow hubs in
the drawer; the command palette is the quickest route to occasional tools.

## Daily workflow

1. **Add stock** — scan bulk cards or capture card, collectible and accessory
   photos. A capture is not stock until its review is approved.
2. **Review** — resolve identity, foil, price and listing queues. Ambiguous items
   remain blocked and visible; do not force them into inventory.
3. **Inventory** — search the canonical owned-stock record, correct location or
   condition, and investigate stale supported prices. `manual_only` items are not
   automation failures.
4. **Sell** — choose exactly one channel workflow. Never record the same sale in
   the direct-sales screen as well as eBay, Whatnot or NL Vault.
5. **Research** — use Market Watch for purchase candidates and the buying
   calculator for margin decisions. Potential Stock is a compatibility view, not
   a second queue.

The dashboard is the only operational alert inbox. A red or amber item should be
opened and resolved there; Discord and email are not incident channels.

## Stock intake

- Bulk Pokémon scans flow through Scan Ingest, card review and foil review before
  committing to the canonical catalogue and inventory.
- Individual photo capture for cards, collectibles and accessories has its own
  recognition fields but shares upload, batch and review mechanics.
- Retailer purchases must be recorded in **Retailer Goals**. They create a linked
  purchase lot and landed-cost inventory together; use that same screen for safe
  refunds.
- Supplier stock must start from a catalogue-linked supplier product, then a
  purchase lot. Completing/receiving the lot allocates the actual purchase price
  across its lines and creates inventory once.

Detailed procedures: [scan ingest](scan-ingest.md),
[retailer purchases](RETAILER_PURCHASE_OPERATIONS.md), and
[supplier purchases](SUPPLIER_PURCHASE_OPERATIONS.md).

## Selling channels

- **eBay direct** is the normal individual-listing route. Run the production
  preflight and explicitly confirm a live publish. Reusing a SKU resumes the
  existing offer instead of creating another one. Use the CSV bulk file for
  large or recovery batches. This path was live-validated with the approved
  Alcremie VMAX canary on 3 October 2026; continue to review every listing before
  confirming publication.
- **eBay sales** imports orders, assists packing and reconciles stock. Do not
  create a second direct-sale record.
- **NL Vault** reserves stock at checkout and guards payment/cancellation races.
  It is private until Stripe and legal/business launch requirements are complete.
- **Whatnot** reserves inventory when it is queued into a show. Finish each item
  as sold, unsold or giveaway, then complete the show.
- **Direct/social/in-person** sales use the Sales ledger. Select owned inventory;
  cost is copied from stock. Void an error there to restore stock exactly once.

Detailed procedures: [eBay](EBAY_OPERATIONS.md),
[NL Vault](NL_VAULT_ORDER_OPERATIONS.md),
[Whatnot](WHATNOT_SHOW_OPERATIONS.md), and
[direct sales/customers](DIRECT_SALES_AND_CUSTOMERS.md).

## What is deliberately retired

- Generic Goals is removed. **Retailer Goals** remains and is the real purchasing
  workflow.
- Generic `/approvals` and `/jobs` writes return `410 Gone`; rejected attempts are
  audited. Workflow-specific confirmations and systemd timers remain active.
- The old Potential Stock and pending-listing schemas remain only for the
  observation window and accept no new duplicate records.

See [the deprecation register](DEPRECATION_OBSERVATION_2026-10-02.md) before
removing any compatibility table or field.

## Health and recovery

The Dashboard and on-demand CRM health report cover application/storefront
health, all scheduled jobs, backup freshness and restore-drill evidence. For
commands, release gates and incident recovery, use
[Security and recovery](SECURITY_AND_RECOVERY.md). Never treat a successful HTTP
health response as proof that NL Vault is ready for public launch; its launch gate
is separate and intentionally visible.
