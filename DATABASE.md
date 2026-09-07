# Database

SQLite (`automation.db`, ~25MB), accessed exclusively through SQLAlchemy models in
`automation_control/models.py`. No raw SQL migrations exist yet — tables are created
via `Base.metadata.create_all(engine)` at app startup, which only adds missing
*tables*, never alters existing ones. The four `scripts/migrate_add_*.py` files show
the existing convention for column additions: a small script that does the `ALTER
TABLE` by hand and is run once, manually.

This document supersedes `docs/database-schema.md`, which only covers the six
original Phase 1A tables and is now a small subset of the 22 tables that actually
exist. Left in place as history.

## Current schema (22 tables, audited 2026-09-07)

### Phase 1A control plane (mostly dormant)
| Table | Rows | Purpose |
|---|---:|---|
| `users` | — | Operator identities (auth is actually handled by `DASHBOARD_USERNAME`/`_PASSWORD_HASH` env vars, not this table) |
| `platform_config` | — | Non-secret key/value config |
| `schedules`, `job_runs` | — | Deterministic job scheduling + run history |
| `audit_events` | 2,564 | Redacted, correlation-ID-tagged activity log. Actively used by every module built tonight. |
| `tool_permissions`, `tool_executions` | — | Deny-by-default AI tool-call gating |
| `approvals` | 0 | Immutable human decisions for future write-capable actions |
| `ebay_listings` | 0 | Sandbox-only listing snapshots |
| `market_observations`, `price_recommendations` | 0 | An earlier, unused attempt at what `scan_ingest.pricing` now does properly |

### Bulk scan-ingest pipeline (live, primary business data)
| Table | Rows | Purpose |
|---|---:|---|
| `card_sets` | 174 | Cached set metadata (name, code, printed total, release date, logo/symbol images) |
| `catalog_cards` | 20,479 | **The reference catalogue** — every card in every cached set, with `phash`/`art_phash` for scan identification and `raw_prices` (raw pokemontcg.io snapshot) |
| `inventory_items` | 995 | **What's actually owned** — `(card_id, variant, condition)` unique, quantity-based, scan photo, session link |
| `card_prices` | 706 | Append-only price observations, source-tagged (`pokemontcg_tcgplayer_market`, `pokemonpricetracker_market`, …) |
| `foil_labels` | 169 | Human-confirmed foil/reverse-holo labels from tonight's `/foil-review`, feeding future threshold calibration |
| `scan_sessions` | 32 | One row per scanning session (set, variant, condition, totals) |

### Single-card capture pipeline (separate, partially built)
| Table | Rows | Purpose |
|---|---:|---|
| `captured_cards` | 579 | Free-text card fields (not linked to `catalog_cards`) + AI vision response + pricing/review status |
| `pending_listings` | 0 | Draft eBay listings, built but never used for a real listing |
| `ebay_credentials`, `oauth_states` | — | Sandbox OAuth token storage (encrypted) |
| `capture_batches` | — | Groups of captures from one photo session |

**Key finding:** `catalog_cards`/`inventory_items` and `captured_cards` model "a
card" independently and don't reference each other. This is the main seam Phase 1
needs to close.

## Modules 1–2 — Generic catalogue + inventory extension (done, 2026-09-07)

Implemented by `scripts/migrate_add_catalog_items.py`, run against production data:

```
catalog_items      20,479 rows -- one per existing catalog_cards row, item_type=TCG_CARD,
                   id reused verbatim from catalog_cards.id (so every existing FK into
                   catalog_cards.id keeps resolving unchanged)
tcg_cards          20,479 rows -- game='pokemon' detail for each catalog_items row
sealed_products    0 rows -- table exists, ready for Module 11 / One Piece sealed product work
collectible_products 0 rows -- table exists, ready for Sonny Angel/Smiski (Phase 6)
storage_locations  0 rows -- table exists, ready for Binder A / Bulk Box 1 / etc.
```

`inventory_items` gained six nullable columns (`catalog_item_id` — backfilled for
all 995 rows from the existing `card_id`; `storage_location_id`; `status`,
defaulted to `AVAILABLE` for all existing rows; `allocated_cost_basis`;
`grading_company`; `certification_number`). `card_id` and `catalog_cards` are
untouched and remain what `scan_ingest` reads/writes — `catalog_item_id` is
additive and not yet read by anything, so this migration changed no
currently-running behavior. Verified: `catalog_cards` and `inventory_items` row
counts identical before and after; full test suite green; both live services
restarted cleanly on the updated models.

Not yet done: nothing reads `catalog_item_id` yet (dashboard pages, scan_ingest's
commit path, `/inventory` still work entirely off `card_id`). That's the next
piece of Phase 1 — wiring the generic catalogue into the UI — not a schema change.

### Modules 3, 4, 6 — Purchasing, buying calculator (done, 2026-09-07)
```
buy_threshold_configs      id, label, green_max_pct, yellow_max_pct, is_default
                            -- seeded: "Default" 55%/70%, is_default=true
purchase_lots               id, source, seller, asking_price, target_buy_pct,
                            offered_price, purchase_price, status, notes
purchase_lot_items          purchase_lot_id FK, catalog_item_id FK, description,
                            market_value, quantity
potential_purchases         id, description, seller, source, url, asking_price,
                            market_value, target_price, max_price, confidence,
                            status, purchase_lot_id FK, notes
```
UI: `/buying-calculator` (stateless, computes buy %, break-even, ROI, margin,
green/yellow/red verdict), `/purchase-lots` (list + detail + line items),
`/potential-stock` (Kanban board by status). All three share
`buying.traffic_light()`, driven by `BuyThresholdConfig` rather than a
hardcoded triple.

### Module 5 — Pricing (consolidation, not new)
Extend `card_prices` into a game-agnostic `price_history` (rename via migration:
create new table, copy rows, drop old once `scan_ingest.pricing` is repointed) with
an explicit `currency` + `converted_aud` pair. Retire `market_observations` /
`price_recommendations`.

### Modules 7–10, 14–15 — Sales, channels, CRM, suppliers, goals, calendar (done, 2026-09-07)
```
marketplaces                 id, name -- seeded: Whatnot, eBay, Website, Facebook,
                             Instagram, Direct, Trade Show, In Person, Other
marketplace_fee_rules        marketplace_id FK, category, commission_pct,
                             processing_pct, fixed_fee, gst_treatment,
                             promotion_label, effective_from, effective_to
                             -- time-boxed rows, not a mutable fee number, so a
                             -- "0% commission weekend" is a row, not a deploy.
                             -- category-specific rule always beats a
                             -- category=None catch-all (commerce.active_fee_rule)
customers                    id, display_name, platform_handles (JSON), segment,
                             notes -- also created automatically from /sales
sales                        id, marketplace_id FK, customer_id FK, sold_at,
                             gross_amount, fees_amount, shipping_revenue,
                             shipping_cost, packaging_cost, tax_amount
sale_items                    sale_id FK, inventory_item_id FK, quantity,
                             unit_price, cost_basis
suppliers                     id, name, contact, website, account_status,
                             wholesale_discount_pct, notes
supplier_products             supplier_id FK, catalog_item_id FK, cost, min_order
goals                          id, label, kind (milestone | cumulative),
                             target_value, current_value, achieved_at
release_calendar               id, catalog_item_id FK, product_name, release_date,
                             supplier_deadline, wholesale_price, retail_price,
                             ordered_quantity
```
UI: `/marketplaces`, `/sales`, `/customers`, `/suppliers`, `/goals` (visual
progress bars), `/release-calendar`. `supplier_products` and `whatnot_*`
(Module 7's actual Whatnot show tracking, distinct from its fee rules which
are done) are not yet built -- next up.

### Module 11 — sealed case economics (done, 2026-09-07)
Uses the existing `sealed_products` table (added in the Module 1 migration,
previously empty). `/sealed-products` lists/creates them; `/sealed-products/
{id}/economics` computes all four scenarios from the spec side by side (sell
whole case, sell as boxes, sell as packs, open and sell singles) from
`units_per_display`/`displays_per_case` -- verified against a hand-calculated
example (12 boxes × 24 packs/box = 288 packs/case).

### Module 5 — price history (done, 2026-09-07)
No new tables -- `card_prices` already existed and had been writing real
data all session. `/prices/{catalog_item_id}` was the missing page to
actually see it: per variant, current price, 7-day average, 30-day average,
change %, high, low, last updated, source, plus a hand-drawn inline SVG
sparkline (no charting library). Linked from `/search` results.

### Module 12 — Sonny Angel / Smiski (done, 2026-09-07; ownership added 2026-09-07)
Uses `collectible_products` (added in the Module 1 migration, previously
empty). `/collectibles` lists/creates them (brand, series, character,
variant, secret flag, retail price).

Originally catalogue-only, same boundary as Module 11's sealed products,
because `inventory_items.card_id` was a NOT NULL foreign key into
`catalog_cards` specifically (a leftover from before the generic catalogue
existed) -- there was no way to record "I own 3 of this Sonny Angel" at all.
Closed by `scripts/migrate_relax_inventory_card_id.py` (below): the manual
"Add a collectible" form and the AI-capture confirm page (Module 19) both
now take a quantity and create a real `InventoryItem` (`card_id=None`,
`catalog_item_id` set) when it's nonzero. Not retroactive -- catalogue
entries added before this migration show 0 owned, not "unknown," since
there's no reliable source to backfill a real quantity from. Each add is
its own new row rather than incrementing an existing one for a repeat
purchase of the same figure -- a real future need, out of scope here.

### `inventory_items.card_id` made nullable (done, 2026-09-07)
`scripts/migrate_relax_inventory_card_id.py` -- SQLite can't `ALTER` a
NOT NULL column away, so this rebuilds the table: rename aside, drop its
named indexes (renaming a table doesn't rename them, and SQLite's own
DDL isn't reliably transactional through this driver, so a caught mid-way
failure attempts automatic recovery by renaming the old table back rather
than trusting a plain rollback), recreate from the current model, copy
every row across, verify the row count matches exactly, drop the old
table. Run against production with `scan-ingest.service` and
`scanbd.service` stopped (that pipeline writes to this table continuously)
after a manual backup
(`~/backups/automation-pre-inventory-card-id-relax-20260907-131503.db`).
Verified before/after: 995 rows, sum(quantity)=1372, unchanged;
`PRAGMA foreign_key_check` clean. Rehearsed twice against a throwaway copy
of the real database first -- the first rehearsal is what caught the
index-collision bug before it ever touched production.

### Module 7 (continued) — Whatnot show tracking (done, 2026-09-07)
```
whatnot_shows                id, title, show_date, viewer_count, follower_count
whatnot_show_items           whatnot_show_id FK, inventory_item_id FK,
                             description, starting_price, final_price,
                             outcome (pending|sold|unsold|giveaway), sale_id FK
```
`/whatnot` lists shows with live revenue/sell-through rollups; `/whatnot/{id}`
queues items and settles outcomes. Marking an item SOLD automatically
creates a real `Sale`/`SaleItem` using whichever `MarketplaceFeeRule` is
currently active for Whatnot -- so a show's numbers flow straight into
`/sales` and `/analytics` without re-entering anything. Verified: a $60 sale
against an 8%+3% rule correctly computes a $6.60 fee.

### Modules 13, 16 — analytics & the business dashboard (done, 2026-09-07)
No new tables -- `/analytics` computes everything from what already exists:
inventory market value (reuses `inventory_review`'s latest-price/AUD-conversion
logic), cost basis (honestly labelled partial -- `allocated_cost_basis` has no
historical backfill), cash tied up, today's/monthly sales and profit,
realized profit, potential stock value (open pipeline statuses only),
open offers, inventory ageing buckets with dead-stock flagging (90+ days,
still `AVAILABLE`), best sales channel, open goals. Verified against real
production data before deploy; 7 tests cover the ageing/dead-stock/
best-channel/open-pipeline-filter logic specifically since those are
the parts most likely to silently miscount.

### Module 19 — AI photo recognition for collectibles (done, 2026-09-07)
Implemented by `scripts/migrate_add_collectibles_capture.py`, run against
production data (0 rows, brand new table -- no backfill needed):
```
captured_collectibles   id, image_path, brand, series, character, variant,
                        is_secret, blind_box_series, ai_raw_response (JSON),
                        status (PENDING_REVIEW|REVIEWED|REJECTED, reused from
                        the existing CardCaptureStatus enum), captured_at,
                        reviewed_at, catalog_item_id FK (nullable)
```
Mirrors `capture.py`'s existing Pokémon-card pipeline exactly: `POST
/collectibles/capture` saves the photo, calls `extract_collectible_details`
(Claude vision, same `messages.parse`/Pydantic-`output_format` pattern as
`extract_card_details`), and creates one `PENDING_REVIEW` row per figure
detected -- or one blank row with the AI error/note recorded if extraction
fails or finds nothing, so a photo is never silently dropped. Nothing writes
to `catalog_items`/`collectible_products` until a human confirms it on
`GET/POST /collectibles/review/{id}`; rejecting just marks the row
`REJECTED` and creates nothing. 13 new tests (`tests/test_collectibles_capture.py`),
all mocking `automation_control.collectibles.extract_collectible_details` --
no real Anthropic API calls in the test suite. Full suite (593) green; manual
backup taken before migration
(`~/backups/automation-pre-collectibles-capture-20260907-115925.db`); live
service restarted and every new route smoke-tested (401, correctly
auth-gated); production DB confirmed still at 0 rows in the new table after
deploy (no test data leaked in this time).

## Migration strategy

1. **Never `DROP` or `RENAME` an existing table or column in the same change that
   adds the new schema.** Every step below is additive; a rollback is always "stop
   using the new columns," never "restore from backup," except where explicitly
   noted.
2. Take a fresh `sqlite3 automation.db ".backup '...'"` snapshot immediately before
   running any migration script (see `OPERATIONS.md`) — in addition to, not instead
   of, the nightly restic backup.
3. Each migration is a standalone script under `scripts/migrate_*.py`, matching the
   existing convention (`migrate_add_pricing_columns.py` etc.) — run once, by hand,
   logged in `CHANGELOG.md`.
4. Backfill new nullable FK columns (`inventory_items.catalog_item_id`, etc.) in the
   same migration script that adds them, so there's never a window where rows exist
   half-migrated.
5. Old columns (`card_id`, the standalone `market_observations` table, etc.) are
   removed only in a *later*, separate migration, after confirming (via `grep` across
   the codebase, not assumption) that nothing reads them anymore.
6. Every migration script supports `--dry-run`, matching the existing
   `correct_inventory_item.py` / `correct_inventory_variant.py` pattern already used
   tonight — print what would change, require a second explicit run to commit.
