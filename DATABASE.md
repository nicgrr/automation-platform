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
