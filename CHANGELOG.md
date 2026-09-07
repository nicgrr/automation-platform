# Changelog

Entries from the point this file was created (2026-09-07) onward. Earlier history
lives in `git log`.

## 2026-09-07 — Module 12 (Sonny Angel/Smiski) + Module 7 (Whatnot shows)

- `/collectibles`: catalogue for Sonny Angel/Smiski/blind boxes, using
  `collectible_products` (previously empty). Inventory *ownership* tracking
  is a deliberate known limitation -- see DATABASE.md for why
  (`inventory_items.card_id` is NOT NULL against `catalog_cards`
  specifically; relaxing it needs a reviewed migration with scan-ingest
  stopped, not a workaround here).
- `/whatnot`: real show tracking (`whatnot_shows`, `whatnot_show_items`) --
  queue items, settle outcomes. Marking an item SOLD automatically creates a
  Sale/SaleItem using the active Whatnot MarketplaceFeeRule, so a show's
  numbers flow straight into `/sales` and `/analytics`. Verified: $60 sale
  at 8%+3% correctly computes a $6.60 fee.
- 7 new tests (careful this time to always override `get_session` with an
  isolated fixture, per the previous incident); full suite (552) green;
  live service restarted; production DB confirmed clean.

## 2026-09-07 — Module 11 (sealed case economics) + Modules 13/16 (analytics)

- `/sealed-products`: list/create sealed products (booster boxes, cases,
  displays), using the `sealed_products` table added in the Module 1
  migration (previously empty). `/sealed-products/{id}/economics` compares
  all four spec scenarios (whole case / boxes / packs / open-and-sell-singles)
  side by side -- verified against a hand-calculated example.
- `/analytics`: the actual business command centre (Modules 13 & 16). No new
  tables -- computes inventory market value, cost basis (honestly flagged
  partial), cash tied up, today's/monthly sales & profit, realized profit,
  potential stock value, open offers, inventory ageing with dead-stock
  flagging, best sales channel, and open goals, all from data that already
  exists. 7 tests specifically target the ageing/dead-stock/best-channel/
  pipeline-filter logic.
- **Caught and fixed a real incident during this work**: manual smoke-testing
  via `TestClient` without overriding `get_session` wrote test data (two fake
  purchase lots, a fake potential purchase, a fake sealed product) directly
  into the *production* database, discovered when `/analytics` showed a
  $450 "potential stock value" that shouldn't have existed. Cleaned up
  immediately (verified via direct row counts before and after); all
  committed test suites already used an isolated `tmp_path` SQLite fixture
  correctly -- only the ad-hoc manual verification scripts were at fault.
  Lesson: every manual TestClient check from here on overrides `get_session`
  with an isolated database, no exceptions.
- Full suite (545) green; live service restarted; production DB confirmed
  clean of test artifacts after the fix.

## 2026-09-07 — Phase 2: purchasing, sales, CRM, suppliers, goals, calendar

- Added 13 new tables (all additive, no backfill needed -- brand new concepts):
  `buy_threshold_configs`, `purchase_lots`, `purchase_lot_items`,
  `potential_purchases`, `marketplaces`, `marketplace_fee_rules`, `customers`,
  `sales`, `sale_items`, `suppliers`, `supplier_products`, `goals`,
  `release_calendar`. Seeded a default 55%/70% buy-threshold config and 9
  standard marketplaces.
- Built and deployed real pages for every one of them: `/buying-calculator`
  (Module 6 -- verified against the spec's own worked example: market $100,
  seller $60, expected sale $90 → 60% buy, $30 gross profit), `/purchase-lots`
  (Module 3, with the green/yellow/red verdict), `/potential-stock` (Module 4,
  Kanban board), `/marketplaces` (Module 7, time-boxed fee rules),
  `/sales` (Module 8), `/customers` (Module 9, auto-created from sales),
  `/suppliers` (Module 10), `/goals` (Module 14, visual progress),
  `/release-calendar` (Module 15).
- Caught and fixed a real bug via the test suite: comparing a
  `DateTime(timezone=True)` column read back from SQLite (naive) against an
  aware `datetime.now(UTC)` in Python raised `TypeError`. Fixed by pushing the
  comparison into the SQL `WHERE` clause instead, matching how the rest of the
  codebase already avoids this.
- 20 new tests across 3 new test files; full suite (534) green; live service
  restarted and every new route smoke-tested (401s, correctly auth-gated, no
  crashes).
- Not yet built: Module 11 (sealed case economics), Module 7's actual Whatnot
  show tracking (fee rules exist; show-specific inventory does not yet),
  `supplier_products` has no UI yet.

## 2026-09-07 — Phase 1 (continued): global search, One Piece via CSV import

- Added `scripts/import_tcg_catalog_csv.py` -- a generic, game-agnostic CSV
  importer for `catalog_items`/`tcg_cards`, since there's no verified API for
  One Piece the way pokemontcg.io serves Pokémon (same "manual import until a
  real API exists" philosophy the pricing engine already uses). Deterministic
  ids from (game, set, number) mean re-importing a corrected CSV updates
  existing rows instead of duplicating. Seeded 2 real One Piece cards (OP17
  Kaido, Monkey D. Luffy) as a working example.
- Added `/search` (Module 17): searches `catalog_items` across every game in
  one place and cross-references ownership via `inventory_items.
  catalog_item_id` -- the first real feature built on the new generic
  catalogue rather than the Pokémon-specific tables. Verified against live
  data (searching "Ethan" correctly shows "own 3" for Ethan's Pichu, matching
  tonight's earlier commit). Added a Catalogue panel + search link to the
  dashboard. 6 new tests; full suite (520) green; live service restarted clean.

## 2026-09-07 — Phase 1 (started): generic catalogue schema

- Added `CatalogItem`, `TcgCard`, `SealedProduct`, `CollectibleProduct`,
  `StorageLocation` models and an `InventoryStatus` enum.
- Extended `InventoryItem` with `catalog_item_id`, `storage_location_id`,
  `status`, `allocated_cost_basis`, `grading_company`, `certification_number` —
  all nullable/defaulted, all backfilled.
- Ran `scripts/migrate_add_catalog_items.py` against production data: backfilled
  20,479 `catalog_items`/`tcg_cards` rows from `catalog_cards`, linked all 995
  `inventory_items` rows to their `catalog_item_id`. Fixed a bug caught during
  the first run (the script never imported `automation_control.models`, so
  `Base.metadata.create_all` silently created nothing) before it touched
  anything the second time. Verified row counts unchanged, full test suite green,
  both live services restart clean. See `DATABASE.md` for the detail.
- Manual backup taken immediately before the migration:
  `~/backups/automation-pre-catalog-items-migration-20260907-085156.db`.

## 2026-09-07 — Phase 0: collectibles platform audit

- Audited the host: confirmed this repo (not a separate app) is "the existing
  Pokémon inventory application," running as bare systemd (not Docker) against
  SQLite (not Postgres); catalogued the 22 existing tables across its two
  previously-separate Pokémon pipelines (bulk scan-ingest, single-card capture);
  confirmed the pre-existing restic-to-OneDrive backup already covers
  `automation.db` correctly and is running successfully.
- Took a manual pre-project backup snapshot
  (`~/backups/automation-pre-collectibles-platform-20260907-081207.db`) in
  addition to the automatic nightly one.
- Added `ARCHITECTURE.md`, `DATABASE.md`, `OPERATIONS.md`, this file — Phase 0
  deliverables. `docs/architecture.md` and `docs/database-schema.md` are left in
  place as historical Phase 1A record.
- Decision: evolve this app in place for the collectibles platform rather than
  building a parallel Docker/Postgres system — see `ARCHITECTURE.md` for the full
  reasoning.
- No schema changes yet. Phase 1 (unified catalogue, One Piece support, inventory
  extension) is proposed in `DATABASE.md`, pending review before execution.

### Also this session, ahead of the platform work
- `feat(foil)`: human-in-the-loop foil/reverse-holo review at `/foil-review`,
  backed by a new `foil_labels` table, plus several scan-recovery scripts
  (gapless-sheet recovery, held-sheet checks, variant correction) written while
  clearing a night's backlog of stuck scans.
- `fix(pricing)`: pokemonpricetracker.com calls are now paced and circuit-broken
  at the shared `_get()` call site, after live per-scan pricing (not the backfill
  job) fired 50+ requests in under 5 minutes and got the API key blocked three
  times.
- `inventory-audit.timer` moved from a single midnight run to hourly, 22:00–14:00.
