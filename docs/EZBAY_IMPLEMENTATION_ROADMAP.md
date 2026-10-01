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
- [ ] Document and test the release rollback procedure.
- [x] Add a service-account import check so owner-only file permissions cannot pass
  tests and then prevent the deployed web service from starting.

## Phase 1 — truthful health and recovery

- [x] Treat unreadable/unknown job state as attention-worthy.
- [x] Add inventory-audit, scanner-button, scanner-recovery and backup jobs to health.
- [x] Add storefront HTTP/configuration health once its service is deployed.
- [x] Make job problems persistent and prominent on the dashboard until recovered.
- [ ] Add backup age and last successful restore-drill status.
- [x] Perform and document a restore into a temporary database (integrity and
  foreign keys clean, migration to head succeeded, restored app health HTTP 200).
- [x] Classify the seven stale/missing-price holdings as supported-stale or
  explicitly unsupported (four supported-stale; three manual-only with reasons).

## Phase 2 — versioned data model

- [x] Introduce Alembic and baseline the existing production schema in place
  (`20261001_01`; blank-database build and production drift check both clean).
- [x] Fail safely and visibly on application/database revision mismatch.
- [ ] Make `catalog_item_id` canonical in every inventory reader and writer.
- [ ] Generalise the historical `CardPrice.card_id` name to catalogue-item identity.
- [ ] Route approved card/photo captures into canonical catalogue and inventory.
- [ ] Preserve legacy fields until verification is complete; remove them only in a
  later backed-up release.

## Phase 3 — workflow consolidation

- [ ] Organise primary navigation as Add Stock, Review, Inventory, Sell and Research.
- [ ] Nest specialist tools beneath Inventory and Research.
- [ ] Move Retailer Goals beneath Research/Market Tools.
- [ ] Merge Potential Stock into Market Watch candidate statuses.
- [ ] Make `shop_listings` the canonical sellable draft and retire
  `pending_listings` after dependency checks.
- [ ] Validate direct eBay publishing end to end; retain CSV as fallback and for
  large/recovery batches.
- [ ] Extract shared capture/review mechanics for cards, collectibles and accessories
  without changing their external routes.

## Phase 4 — channel readiness

- [ ] Complete NL Vault legal/business details.
- [ ] Exercise checkout, webhook, reservation, stock decrement, fulfilment,
  cancellation and refund in a controlled transaction.
- [x] Add storefront health to the dashboard before public launch.
- [ ] Prepare Whatnot inventory, show, sale and customer integration and rehearse a
  show workflow before first use.
- [ ] Integrate retailer purchases with purchase lots and inventory.
- [ ] Integrate customer, direct-sale and supplier records before exposing their
  currently empty screens.

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
