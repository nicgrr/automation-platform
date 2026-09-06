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

## Proposed additions (Phase 1+, not yet created)

Additive only — no existing table is dropped or renamed. New tables first; columns
added to existing tables are nullable/backfilled so nothing currently running
breaks mid-migration.

### Module 1 — Generic catalogue
```
catalog_items            id, item_type (tcg_card | sealed_product | collectible |
                          graded_card | accessory | other), name, image_url,
                          created_at
tcg_cards                catalog_item_id FK, game (pokemon | one_piece | ...),
                          set_id, set_code, number, character, rarity, variant,
                          parallel, language, release_date, artist
                          -- catalog_cards becomes a `game='pokemon'` row here;
                          -- existing catalog_cards.id is reused as the FK target
                          -- during migration, not regenerated
sealed_products           catalog_item_id FK, brand, game, set_id, product_type,
                          units_per_display, displays_per_case, release_date, rrp
collectible_products      catalog_item_id FK, brand, series, character, variant,
                          is_secret, blind_box_series, retail_price
```

### Module 2 — Inventory (extends, doesn't replace)
```
ALTER inventory_items ADD catalog_item_id (nullable FK, backfilled from card_id)
ALTER inventory_items ADD storage_location_id, status, purchase_lot_item_id,
                          allocated_cost_basis, grading_company, certification_number
storage_locations         id, name, kind
```
`card_id` stays for backward compatibility until every reader (dashboard pages,
scan_ingest commit path) is switched to `catalog_item_id`; then it's dropped in a
later, separate migration once nothing references it.

### Modules 3–4 — Purchasing
```
purchase_lots              id, source, seller, asking_price, target_buy_pct,
                            offered_price, status, purchased_at, notes
purchase_lot_items         purchase_lot_id FK, catalog_item_id FK, market_value_snapshot,
                            allocated_cost
potential_purchases        id, description, seller, source, url, asking_price,
                            market_value, target_price, max_price, status,
                            confidence, discovered_at, notes
buy_threshold_config        id, label, green_max_pct, yellow_max_pct
                            -- configurable, not hardcoded, per the explicit ask
```

### Module 5 — Pricing (consolidation, not new)
Extend `card_prices` into a game-agnostic `price_history` (rename via migration:
create new table, copy rows, drop old once `scan_ingest.pricing` is repointed) with
an explicit `currency` + `converted_aud` pair. Retire `market_observations` /
`price_recommendations`.

### Modules 6–9 — Sales, channels, CRM
```
marketplaces                id, name (whatnot | ebay | website | facebook | ...)
marketplace_fee_rules       marketplace_id FK, commission_pct, processing_pct,
                            fixed_fee, gst_treatment, category, effective_from,
                            effective_to, promotion_label
                            -- time-boxed rows, not a single mutable fee number,
                            -- so a "0% commission weekend" is a row, not a
                            -- deploy
customers                   id, display_name, platform_handles (JSON), segment,
                            notes
sales                        id, marketplace_id FK, customer_id FK, sold_at,
                            gross_amount, fees_amount, shipping_revenue,
                            shipping_cost, packaging_cost, tax_amount
sale_items                   sale_id FK, inventory_item_id FK, quantity,
                            unit_price, cost_basis
```

### Modules 7, 10–15 — Whatnot, suppliers, goals, calendar
```
whatnot_shows                id, title, show_date, viewer_count, follower_count
whatnot_show_items            whatnot_show_id FK, inventory_item_id FK,
                             starting_price, final_price, outcome (sold | unsold |
                             giveaway)
suppliers                     id, name, contact, website, account_status,
                             wholesale_discount_pct, notes
supplier_products             supplier_id FK, catalog_item_id FK, cost, min_order
goals                          id, label, target_value, current_value, kind
release_calendar               id, catalog_item_id FK, announcement_date,
                             preorder_date, supplier_deadline, release_date
```

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
