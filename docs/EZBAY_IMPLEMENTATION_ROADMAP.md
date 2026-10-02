# EZBay cleanup implementation roadmap

This is the delivery plan for the 19 owner decisions recorded in
`EZBAY_PLATFORM_REVIEW_2026-10-01.md`. Work is deliberately sequenced so that
reliability and rollback improve before business workflows or schemas are removed.

## Delivery rules

- Preserve current data and behavior until a replacement path is tested.
- Back up before every schema or destructive change.
- Add regression tests with each behavior change.
- Do not remove a feature's schema in the same release that removes its code.
- External publishing, purchasing, refunds, cancellations, inventory deletion and
  bulk corrections continue to require explicit owner confirmation.
- The dashboard is the sole operational-alert destination.

## Phase 0 — reproducible baseline

- [x] Complete platform review and owner decision register.
- [x] Run the full regression suite (1,889 passing on 2026-10-01).
- [x] Separate source from generated data, backups and investigation artifacts.
- [x] Review and checkpoint the current worktree without losing existing work.
- [x] Display deployed Git revision and database revision in Settings (the database
  correctly reports "not baselined" until the Alembic baseline is applied).
- [x] Document and test the release rollback procedure; the non-destructive drill
  boots a historical tagged application against its matching pre-release snapshot.
- [x] Add a service-account import check so owner-only file permissions cannot pass
  tests and then prevent the deployed web service from starting.

## Phase 1 — truthful health and recovery

- [x] Treat unreadable/unknown job state as attention-worthy.
- [x] Add inventory-audit, scanner-button, scanner-recovery and backup jobs to health.
- [x] Add storefront HTTP/configuration health once its service is deployed.
- [x] Make job problems persistent and prominent on the dashboard until recovered.
- [x] Add backup age and last successful restore-drill status, with a monthly
  non-destructive drill and dashboard/CRM alerts when evidence becomes stale.
- [x] Perform and document a restore into a temporary database (integrity and
  foreign keys clean, migration to head succeeded, restored app health HTTP 200).
- [x] Classify the seven stale/missing-price holdings as supported-stale or
  explicitly unsupported (four supported-stale; three manual-only with reasons).

## Phase 2 — versioned data model

- [x] Introduce Alembic and baseline the existing production schema in place
  (`20261001_01`; blank-database build and production drift check both clean).
- [x] Fail safely and visibly on application/database revision mismatch.
- [x] Make `catalog_item_id` canonical in every active inventory reader and
  writer; retain the synchronised nullable `card_id` compatibility link during
  the observation window.
- [x] Generalise the historical `CardPrice.card_id` name to catalogue-item identity
  (`catalog_item_id` is canonical in application code; the physical column and a
  compatibility synonym remain for the observation window).
- [x] Route approved card/photo captures into canonical catalogue and inventory;
  unsafe or ambiguous captures remain visible without changing stock.
- [ ] Preserve legacy fields until verification is complete; remove them only in a
  later backed-up release.

## Phase 3 — workflow consolidation

- [x] Organise primary navigation as Add Stock, Review, Inventory, Sell and Research.
- [x] Nest specialist tools beneath Inventory and Research.
- [x] Move Retailer Goals beneath Research/Market Tools.
- [x] Merge Potential Stock into Market Watch candidate statuses; the empty legacy
  table and detail routes remain during the observation window, but accept no new rows.
- [x] Make `shop_listings` the canonical sellable draft and retire new writes to
  the empty `pending_listings` queue; preserve its schema and legacy detail route
  during the observation window.
- [ ] Validate direct eBay publishing end to end; retain CSV as fallback and for
  large/recovery batches.
  - [x] Validate the complete Inventory API request contract locally, add a
    read-only production preflight, explicit live confirmation, required item
    specifics/condition descriptor/duration and crash-safe offer resumption.
  - [ ] Publish one owner-approved production canary and verify it in the live
    eBay mirror before declaring direct publishing fully validated.
- [x] Extract shared capture/review mechanics for cards, collectibles and accessories
  without changing their external routes; upload persistence, background-batch
  accounting/status and pending-queue traversal now share one implementation,
  while extraction fields and catalogue-creation rules remain type-specific.

## Phase 4 — channel readiness

- [ ] Complete NL Vault legal/business details.
- [x] Exercise checkout, signed webhook, atomic reservation, stock decrement,
  fulfilment, cancellation and refund in a controlled temporary-database
  transaction; the repeatable drill makes no Stripe/eBay calls or real charges.
- [x] Add storefront health to the dashboard before public launch.
- [x] Prepare Whatnot inventory, show, sale and customer integration and rehearse
  the complete workflow in a temporary database before first use; real inventory
  cannot be queued while allocated to NL Vault/eBay, and exact Seller Hub fees
  take precedence over dated estimates.
- [x] Integrate retailer purchases with purchase lots and canonical inventory in
  one transaction, allocate shipping into landed stock cost, group matching
  retailer order numbers, guard duplicate submissions and unsafe refunds, and
  rehearse purchase/refund/repurchase against a temporary database.
- [ ] Complete CRM integration before exposing every former empty screen:
  inventory-backed direct sales and customer identity are complete and rehearsed;
  supplier purchasing and receipt into inventory remains.

## Phase 5 — staged retirement and documentation

- [ ] Hide and deprecate the unused control-plane surfaces while retaining audit
  events.
- [ ] Remove the generic goals module.
- [ ] Begin the agreed 60-day observation period for deprecated features.
- [ ] Replace active documentation with current operator, architecture/data-flow and
  security/backup guides.
- [ ] Move superseded Phase 1 material to `docs/history/` with historical labels.
- [ ] After the observation window: export, back up and remove unused code; remove
  its schema only in a later release.

## Acceptance journeys

The cleanup is complete only after all four journeys are documented and repeatable:

1. scan/photo → review → canonical catalogue → inventory → trustworthy price;
2. inventory → direct eBay publish → imported order → packed → stock reconciled;
3. inventory → NL Vault → checkout/webhook → fulfilment or refund;
4. backup → temporary restore → migration verification → application health and
   reconciliation clean.
