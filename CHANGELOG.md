# Changelog

Entries from the point this file was created (2026-09-07) onward. Earlier history
lives in `git log`.

## 2026-09-07 — Module 21: eBay market search, in place of Facebook Marketplace scraping

Asked for "a tool to scan the net or even Facebook Marketplace for deals or
collections." Flagged this before building: Facebook has no public API for
this, scraping Marketplace violates its Terms of Service, and doing it for
real would mean this app holding the operator's personal Facebook login
session -- a real account-ban risk with no safe mitigation, categorically
different from "build it more carefully." Agreed alternative: extend the
eBay integration (already partially built tonight, read-only) into a real
search page instead.

- `GET /market-search`: search a term, see what eBay has listed (via the
  existing `EbaySandboxReadAdapter.search_market` / Browse API, unchanged
  from the read-only, no-mutation-methods adapter built earlier), shown
  next to this app's own last-known price for any matching card in
  `card_prices` -- so "is this actually a deal" has a second number to
  compare against. Runs on an application access token
  (`EbayOAuthClient.application_token()`), not the operator's own eBay
  login, since public listing search doesn't need seller authorization the
  way creating a listing would.
- Honest about environment: while `ebay_env` stays `"sandbox"` (its
  default, and the only mode `start-control-plane.sh` will actually start
  in), results are eBay's fake test catalogue, not real listings -- the
  page says so plainly rather than presenting sandbox data as real deals.
  Meaningful real-world results are gated behind the same deliberate
  production-credentials decision as everything else eBay-related tonight.
- Moved `configured()`/`ebay_services()` from `api.py` into `ebay_oauth.py`
  (a pure relocation, no behavior change) so this new page can import them
  without a circular import back into `api.py`.
- 6 new tests (login gate, blank query, unconfigured message, a real
  search with results, the catalogue cross-reference, failure handling).
  Caught a real bug before it shipped: `normalize_market()`'s price field
  is a string straight from eBay's JSON, and the results-table formatting
  assumed it was already numeric -- would have crashed on the first real
  search with a result. Full suite (653) green; `ezbay.service` restarted;
  both routes smoke-tested (401, correctly gated).

## 2026-09-07 — AI photo recognition wired into buying, purchase lots, potential stock, Whatnot

Asked for "input something in there (AI recognition of items) and able to
select and confirm the items and see market value" across the buying
calculator, potential stock, and purchase lots, plus connecting Whatnot to
the same flow.

- New `automation_control/quick_price.py`: `POST /quick-price/identify`
  takes one photo, reuses the existing `extract_card_details()` (same AI
  vision call the card-capture pipeline already uses), matches each
  detected card against the reference catalogue (`CatalogCard.number` +
  fuzzy name, narrowed by set name when read), and returns each match's
  latest AUD market price from `card_prices`. Deliberately stateless and
  read-only -- no `CapturedCard` row, nothing written to inventory, the
  photo lives in a temp dir for the duration of the request only. Scoped
  to TCG cards for now; collectibles have no live market-price feed yet
  to look up against.
- New shared widget in `ui.py` (`quick_price_widget()` /
  `QUICK_PRICE_STYLE` / `QUICK_PRICE_SCRIPT`, present on every page like
  the search autocomplete): upload a photo, get back a list of identified
  cards with their market value, click one to fill the page's own
  form fields -- the click is the "select and confirm" step; the page's
  own submit button still has to be pressed for anything to actually
  save. One generic script wired via `data-name-field`/`data-price-field`
  attributes instead of four bespoke ones.
- Wired into `/buying-calculator` (fills `market_price`), `/purchase-lots/{id}`'s
  add-item form (fills `description`+`market_value`), `/potential-stock`'s
  add-to-watchlist form (same), and `/whatnot/{id}`'s add-to-queue form
  (fills `description`+`starting_price`).
- 13 new tests (8 for the identify endpoint itself -- matched/unmatched/
  multi-card/unreadable-field-fallback/failure-handling, 5 confirming the
  widget is wired to the right field names on each page); full suite (647)
  green; `ezbay.service` restarted; all four pages plus the new endpoint
  smoke-tested (401, correctly gated).

## 2026-09-07 — Unified the whole app onto one light theme, fixed a live mobile bug

Only `/dashboard` had the polished light theme (charts, topbar, autocomplete);
every other page still had the earlier dark "trading desk" look. Asked to make
the whole app look good and consistent, chose (with the user) to unify
everything onto the dashboard's light design system rather than polish the
dark theme in place.

- **Root cause found and fixed**: a screenshot from a phone showed the
  dashboard's topbar search box rendered as a sliver a few pixels wide.
  `.topsearch{flex:1}` had no `min-width:0`, and the nav links had no mobile
  breakpoint at all -- on a narrow viewport, flex items don't shrink below
  their own content size by default, so the nav links and brand claimed their
  full width first and squeezed the only flexible item (the search box) down
  to nothing. Fixed at the source: the shared topbar now sets `min-width: 0`
  on the search box and hides the nav links below 680px, so the search box
  gets the space instead.
- `ui.py`'s shared `STYLE` is now a light palette (the dashboard's own
  tokens: `#f4f5f8` background, white panels, `#0891b2`/`#7c3aed`
  cyan-to-violet accent) instead of the dark navy/neon-cyan one. Every
  component that referenced these as CSS custom properties (`.panel`,
  `.stat-card`, `.pill`, tables, forms, buttons) re-themed automatically;
  the neon glow effects (button/card box-shadows, focus rings tuned for a
  dark background) were replaced with the calmer flat-shadow look the
  dashboard already used.
- Added `.pill.warn` (amber) -- `buying.py`'s traffic-light verdict has
  returned `"warn"` for a YELLOW result since Phase 2, but `ui.py` only ever
  styled `.ok`/`.bad`/`.neutral`, so a YELLOW verdict silently rendered with
  no color at all. Caught by a new regression test, not by inspection.
- **Consolidated the topbar**: it was duplicated almost verbatim inside
  `dashboard.py`'s bespoke shell and nowhere else -- exactly the kind of
  duplication that let the mobile bug above go unnoticed on every other page
  (which had no topbar at all until now). It's now one shared component in
  `ui.py` (`_topbar_html`/`TOPBAR_STYLE`), present on every page via `page()`,
  with a `show_nav=False` escape hatch for the login page.
  `dashboard.py` was refactored to call the shared `page()` shell instead of
  hand-rolling its own `<!doctype html>`, keeping only its widget-grid
  markup and chart styling as page-specific.
- `/search`'s own dedicated search box (with the same fixed id as the
  topbar's) is gone -- it would have been a duplicate-id bug the moment the
  topbar became universal. The persistent topbar box now doubles as the
  page's input, pre-filled with the current query (`page(..., search_value=...)`).
  `AUTOCOMPLETE_SCRIPT` moved from `search.py` into `ui.py` since every page
  needs it now, not just `/search` and `/dashboard`.
- `brand_header()` dropped the large gradient "EzBay" wordmark (redundant
  now that the topbar always shows the brand) in favor of a plain page-title
  `<h1>`, matching the dashboard's own heading style.
- Every page-specific `_STYLE` block's hardcoded `#0a0f1c` (recessed
  input/detail background) became `var(--surface-sunken)`; hardcoded
  semantic-color literals (`#f87171`, `rgba(52,211,153,...)`, etc.) became
  their matching `var(--danger)`/`var(--success)` tokens, so a future
  palette change doesn't require re-auditing every module again.
- 8 new tests (`tests/test_ui.py`) plus fixes to `test_pwa.py` (merged two
  theme-specific PWA tests into one quote-agnostic check, since every page
  now shares one shell). Full suite (635) green; `ezbay.service` restarted;
  smoke-tested `/login` (200) and a dozen other routes (401, correctly
  gated, no crashes); production DB confirmed untouched (995 rows).

## 2026-09-07 — eBay production-mode plumbing (deliberately not enabled)

Asked to build a "real eBay listing flow." Investigation found the existing
code has three independent, deliberate guardrails against ever doing that
today: `config.py` type-locked `ebay_env` to `Literal["sandbox"]`,
`ebay_oauth.py` actively rejects any credential that looks like a real
production client ID/RuName, and `scripts/start-control-plane.sh` refuses
to even start the service unless `EBAY_ENV=sandbox` exactly.
`listing_pipeline/publish.py` -- the only place that would ever write a
real listing -- is an intentional, unimplemented placeholder. Flagged this
to the user rather than building around it; agreed scope: add the
production-mode *plumbing* only, leave every guardrail in place, no live
listing capability today.

- `ebay_env` now accepts `"production"` (still defaults to `"sandbox"`).
- `ebay_oauth.py`: added `AUTH_URL_PRODUCTION`/`TOKEN_URL_PRODUCTION` and
  `valid_production_client_id`/`valid_production_runame` (the mirror image
  of the sandbox validators -- reject a *sandbox*-looking credential instead
  of a production one). `authorization_url()`, `EbayOAuthClient`, and
  `store_user_tokens()` all take an `environment` parameter now (default
  `"sandbox"`, so every existing call site keeps working unchanged).
- `adapters/ebay.py`: `EbaySandboxReadAdapter` takes an `environment`
  keyword arg selecting `api.ebay.com` vs `api.sandbox.ebay.com` -- still
  read-only in both, no mutation methods added.
- `api.py`: `configured()`/`ebay_services()` and all four `/api/ebay/*`
  routes now pick validators/endpoints/response labels off
  `settings.ebay_env` instead of hardcoding "sandbox".
- **Deliberately untouched**: `start-control-plane.sh`'s
  `EBAY_ENV must be sandbox` check, `EbaySandboxReadAdapter`'s read-only
  contract, and `listing_pipeline/publish.py`'s unimplemented status. Going
  live still needs all three crossed on purpose, plus real production
  credentials from the eBay developer portal that only the user can supply.
- 10 new tests (production URL selection, cross-environment credential
  rejection in both directions, `configured()` behavior per environment);
  full suite (628) green; `ezbay.service` restarted -- still starts in
  sandbox mode exactly as before, confirmed via the startup script's own
  guard still being in the code path.

## 2026-09-07 — Module 20: generic import/export

- `/export`: CSV downloads for inventory, sales, sale line items, customers,
  and suppliers -- every export is a plain read-only query, nothing here
  writes to the database. Linked from the dashboard's Business quick links.
- `scripts/import_suppliers_csv.py`: bulk-import suppliers from a CSV,
  same dry-run/idempotent-upsert convention as the existing
  `import_tcg_catalog_csv.py`, keyed on exact (case-insensitive) name since
  suppliers have no other natural key. An invalid `account_status` value is
  skipped with a warning rather than aborting the whole file.
- Import stays a CLI script rather than a web upload form, matching the
  existing convention (`import_tcg_catalog_csv.py`) -- bulk data entry from
  a spreadsheet is an occasional back-office task, not something done from
  a phone. Export is the web-facing half since that's naturally a
  dashboard action.
- 15 new tests (6 for the export routes, 5 for the import script, 4 dashboard
  link/list checks updated); full suite (619) green; `ezbay.service`
  restarted and all five export routes plus `/export` itself smoke-tested
  (401, correctly gated).

## 2026-09-07 — Bulk photo capture for collectibles

- `/collectibles/capture/bulk`: select multiple photos at once, mirroring
  the card pipeline's bulk capture exactly (`capture.py`'s `_process_photo`
  / `_run_capture_batch` pattern) -- photos save synchronously (fast, disk
  I/O only), then a background task extracts each one so the browser isn't
  held open for the AI calls. Reuses the existing generic `capture_batches`
  table (already shared infrastructure, no schema change) and
  `/collectibles/capture/bulk/status/{id}` for progress.
- Refactored the single-photo route's extraction+row-creation logic into
  `_process_collectible_photo`, now shared by both the single and bulk
  paths, same as cards' `_process_photo`.
- 6 new tests (bulk creates one row per photo, one photo's AI failure
  doesn't abort the rest, status page states, auth/503 gating); full suite
  (608) green; `ezbay.service` restarted and both new routes smoke-tested
  (401, correctly gated); production DB confirmed clean
  (`captured_collectibles` still 0 rows).

## 2026-09-07 — Supplier products UI (closes a known gap from Phase 2)

- `supplier_products` has existed since the Phase 2 migration with no UI at
  all. Added `GET /suppliers/{id}` (a real detail page -- contact info,
  website, discount, minimum order, and a table of what they sell) and
  `POST /suppliers/{id}/products` to add one. Supplier names on `/suppliers`
  now link to their detail page. No schema change.
- 6 new tests; full suite (602) green; `ezbay.service` restarted and both
  new routes smoke-tested (401, correctly gated); production DB confirmed
  clean (`suppliers`/`supplier_products` still 0 rows).

## 2026-09-07 — Collectible ownership tracking (closes the Module 12 known limitation)

- `scripts/migrate_relax_inventory_card_id.py`: `inventory_items.card_id` is
  now nullable. Run for real against production with `scan-ingest.service`
  and `scanbd.service` stopped (confirmed inactive first), after a manual
  backup (`~/backups/automation-pre-inventory-card-id-relax-20260907-131503.db`).
  Rehearsed twice against a throwaway copy of the live database before
  touching it for real -- the first rehearsal caught a real bug (renaming a
  table doesn't rename its own named indexes in SQLite, so the rebuild's
  fresh `CREATE INDEX` calls collided with the old ones) that would
  otherwise have hit production. Verified 995 rows / sum(quantity)=1372
  identical before and after, `PRAGMA foreign_key_check` clean; both
  services restarted clean afterwards.
- `/collectibles`'s manual "Add a collectible" form and the Module 19
  AI-capture review/confirm page both now take a quantity; a nonzero one
  creates a real `InventoryItem` (`card_id=None`, `catalog_item_id` set) --
  Sonny Angel/Smiski/blind boxes can finally be tracked as "I own N of
  this," not just catalogued. The collectibles list now shows an Owned
  column. Not retroactive: catalogue entries added before today show 0
  owned rather than a guessed number.
- 5 new tests (2 for the migration script itself using a hand-built legacy
  schema, 3 for the ownership-creation behavior); full suite (598) green;
  `ezbay.service` restarted and `/collectibles` smoke-tested (401, correctly
  gated).

## 2026-09-07 — Module 19: AI photo recognition for collectibles

- `POST /collectibles/capture`: photograph a Sonny Angel/Smiski/blind-box
  figure, get an AI-suggested brand/series/character/variant back via a new
  `extract_collectible_details()` (Claude vision, mirrors the existing
  Pokémon-card `extract_card_details()` exactly -- same model, same
  `messages.parse`/Pydantic-`output_format` pattern). Lands in a new
  `captured_collectibles` staging table with `status=PENDING_REVIEW`; a
  failed or empty extraction still creates one blank row (with the error/note
  recorded) rather than silently dropping the photo, matching the card
  pipeline's own rule.
- `GET /collectibles/review` (queue) and `GET/POST /collectibles/review/{id}`
  (confirm/reject): nothing reaches the catalogue until a human confirms it
  here -- confirming creates a real `CatalogItem` + `CollectibleProduct`;
  rejecting just marks the row `REJECTED`. This is the one route in the whole
  module that writes to the catalogue, and it never runs unattended.
- Real cost implication, called out before starting per the user's explicit
  go-ahead: each capture is one Claude Opus vision call, same as the existing
  card-capture flow.
- `scripts/migrate_add_collectibles_capture.py` (new table only, no backfill)
  run against production after a manual backup
  (`~/backups/automation-pre-collectibles-capture-20260907-115925.db`);
  verified 0 rows before and after. 13 new tests, all mocking
  `extract_collectible_details` -- no real API calls in the suite. Full suite
  (593) green; live service restarted; every new route smoke-tested (401,
  correctly auth-gated, no crashes); production DB confirmed clean.

## 2026-09-07 — Module 18: installable to the home screen (PWA basics)

- Generated a brand-matching icon (cyan-to-violet gradient, "Ez" mark,
  same colors used throughout the dark theme) at 192px/512px/180px via
  Pillow, and a `static/manifest.json`.
- Mounted `/static` (unauthenticated by design -- no inventory or business
  data lives there, just install-to-home-screen assets) and added
  `apple-mobile-web-app-*`/`apple-touch-icon`/`theme-color`/manifest tags
  to both `ui.py`'s shared dark-theme `page()` and the dashboard's own
  light-theme shell.
- **Deliberately no service worker / offline caching**: this tool shows
  live inventory and pricing, and a caching layer risks showing stale
  numbers on exactly the data a phone check exists to get right. iOS
  (the only device this has actually been used from all session) needs
  no service worker for "Add to Home Screen" -- it reads the
  `apple-mobile-web-app-*` tags directly; the manifest covers
  Android/desktop installability the same way.
- 5 new tests (manifest validity, icons served as PNG, no-login-required,
  both page shells include the tags); full suite (580) green; live
  service restarted, manifest/icons/dashboard all confirmed responding.

## 2026-09-07 — Search autocomplete

- `GET /search/suggest`: a small JSON endpoint (max 8 results, 2-character
  minimum) reusing the same catalogue query as the full `/search` page.
- Wired a vanilla-JS (no framework) debounced autocomplete dropdown onto
  both the dedicated `/search` page and the dashboard's top-bar search --
  arrow-key navigation, Enter to jump to a result's price-history page,
  Escape/click-outside to close. Results are rendered via `textContent`,
  never `innerHTML` with interpolated data, regardless of where a card
  name originated (import or otherwise).
- Refactored the full-page search query into a shared `_matching_items`
  helper so the suggest endpoint and the results page can't drift apart.
- 5 new tests for the suggest endpoint, 2 more confirming both pages
  actually include the autocomplete wiring; full suite (575) green; live
  service restarted, all three routes confirmed responding (401, correctly
  gated).

## 2026-09-07 — Dashboard redesign: light theme, real charts

- Rebuilt `/dashboard` from scratch per an explicit visual reference (a
  Salesforce executive dashboard) after feedback that the plain stat-grid
  version looked "unorganised and messy": organized widget cards, a top
  nav with search, and real hand-drawn inline SVG charts (donut, gauge,
  horizontal bar) instead of plain numbers -- no charting library, matching
  this project's existing plain-string-HTML convention. New `charts.py`
  module holds the reusable chart-drawing functions (7 tests).
- **Deliberately scoped to this one page**: every other page in the app
  keeps its existing dark "trading desk" theme; only the dashboard's
  palette changed, by explicit user choice, not a global theme switch.
  The new `dashboard.py` module has its own self-contained HTML shell
  rather than reusing `ui.py`'s shared dark `page()`.
- Preserved every navigation link the old dashboard had (card-capture
  pipeline, every business module page) in a "quick links" section below
  the new widget grid, verified by a test that checks all of them render.
- No schema changes. `_review_queue_size` and the `/dashboard` route moved
  out of `api.py` into the new `dashboard.py`; several now-unused imports
  cleaned up from `api.py` in the process.
- 6 new tests for the dashboard itself; full suite (569) green; live
  service restarted and both `/dashboard` (401, correctly gated) and
  `/login` (200) confirmed responding.

## 2026-09-07 — Module 5: price history page

- `/prices/{catalog_item_id}`: the pricing engine (`card_prices`,
  `PriceSource`) has been writing real data all session with nowhere to
  view it. Shows current/7-day-avg/30-day-avg/change/high/low/source per
  variant, plus a hand-drawn inline SVG sparkline. Linked from `/search`.
  No new tables. 4 new tests; full suite (556) green; live service
  restarted.

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
