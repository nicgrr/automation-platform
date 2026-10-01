# EZBay platform review — 1 October 2026

## Owner decisions

Status: **complete — all 19 review decisions confirmed by the owner.**

Decisions recorded during the review:

1. **eBay publishing — transition approach.** Keep bulk CSV as the dependable
   fallback and large-batch/recovery method while direct eBay publishing is validated
   end to end. After direct publishing has proven reliable, make it the default.
2. **NL Vault — retain and launch properly.** Keep NL Vault and develop it into an
   active public sales channel. Do not call it production-ready until a controlled
   test has verified checkout, payment webhook handling, stock reduction, fulfilment,
   cancellation/refund, completed legal details and service monitoring.
3. **Single-card capture — consolidate.** Retain phone and bulk-photo capture as
   stock-intake methods, but make approved captures resolve into the canonical
   catalogue and inventory. Create sale listings through `shop_listings`; retire the
   unused `pending_listings` workflow after verifying that nothing external depends
   on it.
4. **Original control plane — retire unused parts.** Preserve the active audit-event
   system. Hide the unused jobs, schedules, approvals, tool permissions, tool
   executions, users, market-observation and price-recommendation surfaces, then
   remove their code and tables through a backed-up migration after confirming there
   are no external callers.
5. **Manual CRM records — retain selectively.** Keep customers and manual sales as
   the future unified record for direct, social, event and cash transactions. Keep
   suppliers for purchasing and cost history. Hide these modules until they are
   integrated into real workflows. Remove the unused generic goals module because
   more specific operational and retailer-goal features supersede it.
6. **Purchasing — consolidate around real workflows.** Retain the buying calculator
   and purchase lots. Merge Potential Stock into Market Watch as a saved-candidate
   status workflow instead of maintaining two places for possible purchases. Keep
   purchase lots hidden until supplier, inventory and cost-allocation integration is
   complete.
7. **Whatnot — planned within six months.** Retain the Whatnot module but keep it out
   of the everyday workflow until it is needed. Before the first show, integrate show
   items and outcomes with canonical inventory, sales and customer records, and run a
   rehearsal workflow.
8. **Retailer Goals — retain under Market Tools.** Keep the Gameology/retailer-goal
   functionality, but make it a subordinate Market Tools feature instead of a
   primary navigation section. Later, completed retailer purchases should create or
   update purchase-lot and inventory records automatically.
9. **Catalogue identity — complete the generic migration.** Make
   `catalog_item_id` the sole canonical identity across all readers and writers.
   Retain legacy `card_id` and `catalog_cards` temporarily for compatibility and
   reference data; remove the legacy link only after complete migration, data
   verification and a fresh backup.
10. **Operational alerts — dashboard only.** Do not send Discord or email alerts.
    Make failures prominent and persistent on the dashboard, retain status/history,
    and clearly show when a condition has recovered.
11. **Database migrations — adopt Alembic.** Baseline the current production schema
    in place without rebuilding it. Record and order every future schema migration,
    expose the current database revision, and report application/database revision
    mismatches clearly instead of relying on `create_all()` or untracked manual
    migration state.
12. **Documentation — establish one current source of truth.** Create current
    operator, architecture/data-flow, and security/backup guides. Move superseded
    Phase 1 material under `docs/history/` with clear historical labels; preserve
    useful incident context while removing conflicting active instructions.
13. **Releases — deploy known revisions.** Inventory and separate source, generated
    files, backups and temporary artifacts without discarding existing work. Review
    and commit the current application, tag known-good releases, display the deployed
    Git and database revisions, back up before releases, gate deployments on tests and
    migration checks, and document rollback.
14. **Database engine — retain SQLite for now.** Improve versioning, backup, restore
    testing and monitoring without moving databases. Reconsider PostgreSQL when
    concurrent writers, measured locking/reporting limits, multiple operators or
    meaningful public-store order volume justify the additional operational cost.
15. **Navigation — five primary workflows.** Organise everyday use around Add Stock,
    Review, Inventory, Sell and Research. Keep Settings, exports, health and other
    administration accessible but visually secondary.
16. **Specialist tools — retain but nest.** Keep Personal Collection, accessories,
    collectibles and sealed-product economics within Inventory. Keep Deal Check and
    Release Calendar within Research. Do not give each specialist tool equal
    top-level navigation prominence.
17. **Missing prices — represent uncertainty honestly.** Show “No supported price
    source” for unsupported products and never substitute another language or
    printing automatically. Permit manual prices only with source notes and a review
    date. Continue queueing stale supported prices, but exclude explicitly unsupported
    products from unhealthy stale-price counts.
18. **Feature retirement — use a 60-day staged process.** Hide and deprecate an
    unused feature first, observe and verify callers for 60 days, export any data and
    take a fresh backup, remove code in one release, and remove schema only in a later
    release so rollback remains possible.
19. **Automation authority — automate preparation, not irreversible judgment.**
    Automatically identify and price items, prepare drafts, refresh market data,
    import orders, reconcile records, reserve/decrement sold stock, run health checks
    and surface exceptions. Require explicit owner confirmation before publishing a
    new listing, materially changing a live price, purchasing, refunding or cancelling
    a paid order, deleting inventory, or running bulk/destructive corrections.

## Executive assessment

EZBay is operational and its core inventory-to-sale workflows are in considerably
better condition than its historical documentation suggests. The production API,
scanner and scanner-button service are running; the SQLite database passes both
`PRAGMA integrity_check` and `PRAGMA foreign_key_check`; inventory/listing
reconciliation is clean; all known review queues are empty; and the full automated
test suite is green at the time of this review.

The platform is not yet "boring to operate," however. The most important risk is
release control: the live service runs directly from a worktree containing roughly
10,000 changed lines in 83 tracked files and 82 untracked paths. The database and
jobs can be healthy while the exact deployed application remains hard to reproduce,
compare or roll back. The second issue is conceptual overlap: EZBay contains an old
AI/control-plane foundation, a legacy single-card capture pipeline, the live bulk
scan pipeline, two eBay listing paths and a newer storefront. Several of those are
valid stages, but several are dormant or partially superseded and currently make the
system look larger than the business workflow really is.

Recommended target: retain five visible workflows — **Add stock, Review, Inventory,
Sell, Research** — backed by one catalogue identity, one inventory identity, one
price history and explicit channel adapters. Archive dormant control-plane features
and hide unused business modules until there is real data or an active operational
need.

## Evidence snapshot

- `ezbay.service`, `scan-ingest.service` and `scanbd.service`: active.
- Database: SQLite/WAL, integrity `ok`, zero foreign-key violations.
- Live data: 64,911 catalogue items; 2,139 inventory holdings; 97,130 price
  observations; 2,127 storefront listings; 183 mirrored eBay listings; 89 scan
  sessions; 5,638 audit events.
- Reconciliation audit: clean.
- Review queues: clean.
- Background jobs covered by the current health module: all on schedule.
- Pricing gap: seven available holdings have no price observation in the last 30
  days (including holdings with no price at all).
- Business-key duplicate checks: zero duplicate inventory keys, zero duplicate eBay
  SKUs, and zero duplicate active shop listings for the same inventory item.
- Repeated single-card captures are common (66 repeated name/set/number groups), but
  these are capture events/copies and are not automatically data duplicates. The
  catalogue also repeats names across sets and products by design.

## What each pipeline does

| Pipeline | Purpose and path | Current state | Keep? | Outstanding work |
|---|---|---|---|---|
| Bulk flatbed scan | Scanner button → `scanbd` → watch folder → detect/crop → OCR/pHash/vision identification → review if uncertain → inventory commit | Primary intake path; active and automated; multi-game catalogue support | Yes, core | Include scanner, image audit and all related timers in health reporting; keep auto-detection unpinned; continue improving catalogues for sources with partial coverage |
| Manual scan review | `/review` corrects held crops; `/foil-review` classifies variants; `/inventory-audit` catches photo/card mismatches | Queues currently clear; strong safety boundary because uncertain items are held | Yes, core | Make the review centre the only normal entrance; keep individual queues as drill-down pages |
| Single-card photo capture | `/cards/capture` or bulk photo upload → AI identification → card review → price review → draft listing | Built and tested, but its `captured_cards` identity is separate from the main catalogue and only 16 captures have been promoted; `pending_listings` is empty | Keep intake, consolidate downstream | Promotion must always resolve into `catalog_items`/`inventory_items`; retire the separate pending-listing path if shop/eBay export is the chosen selling workflow |
| Collectible/accessory photo capture | Photograph or bulk upload → AI/reverse-image/link identification → review → generic catalogue/inventory | Newer generic equivalent of card capture; small but real data set | Yes | Standardise shared capture/review code so cards, collectibles and accessories do not each maintain near-identical batch, image and decision plumbing |
| Catalogue and inventory | Legacy `catalog_cards` plus generic `catalog_items`/subtypes; `inventory_items` represents owned stock | Generic links are complete for all inventory; 16 non-legacy holdings correctly have no `card_id` | Yes, core | Treat `catalog_item_id` as canonical everywhere; preserve `card_id` only as a compatibility field until all readers are migrated, then remove the dual identity deliberately |
| Pricing | Append-only `card_prices`; live/provider imports; manual review; eBay market fallback | Large history and good coverage; seven stale/missing holdings | Yes, core | Clear the seven-item gap; rename/generalise `CardPrice.card_id`, which is actually a generic catalogue-item ID; ensure every price source exposes currency, timestamp and provenance consistently |
| NL Vault storefront | Inventory → `shop_listings` → public shop/cart/checkout → shop orders → fulfilment | 2,127 listings; no completed shop orders yet; separate storefront app | Yes, if storefront launch is intended | Replace legal placeholders and complete an end-to-end test transaction before calling it production-ready; add storefront/service/payment-webhook health checks |
| eBay bulk export | Inventory/shop selection → CSV batch → upload to eBay → import results → listing cache/linkage | Actively used; 15 export batches and 4,881 export items; duplicate guards and reconciliation exist | Yes, core eBay path | Document this as the canonical listing path; keep photo, SKU and sold-stock reconciliation gates mandatory |
| Direct eBay publish | A shop listing can be published through a newer Trading API path | Present and tested, but overlaps bulk export; historical `listing_pipeline/publish.py` remains an intentionally unimplemented Sell API placeholder | Choose one default | Decide whether direct publish or CSV is primary. Keep the other as an explicit fallback, not an equal top-level workflow. Delete/archive the unwired placeholder once the decision is made |
| eBay sales return loop | Periodic order import → pack assistant → complete/discard → sold inventory reconciliation | Active automation; seven imported orders | Yes, core | Add one dashboard metric for import freshness and unmatched orders; retain idempotency/reconciliation tests |
| Market research/watch | Live search, photo search, saved watch terms, AU/JP scans and alert imports | Active but lightly used (two watch terms, 436 listings/matches) | Yes, secondary | Rename “Sonny Angel watch” to generic “Market watch”; show provider freshness and failed-alert delivery count |
| Retailer goals | Gameology loyalty milestone, fund ledger, price snapshots and planned purchases | Seeded and functional; three products, no recorded purchases/prices | Optional | Keep behind Market tools until used. It is not part of core CRM operation and should not compete in navigation with stock/sales work |
| Purchasing/CRM/Whatnot | Purchase lots, potential stock, suppliers, customers, sales, goals and Whatnot shows | Schemas and screens exist, but their main tables contain zero rows | Dormant | Hide from normal navigation and stop expanding until a real workflow uses them. Archive entirely if still unused after an agreed review date |
| Original AI control plane | Jobs, schedules, permissions, approvals, users, tool executions and old recommendation tables | Almost entirely zero-row and not how authentication, scheduling or pricing currently work | No operational value today | Preserve audit events, but archive/remove the unused UI/API/schema surface after confirming no external caller depends on it |

## Duplication and overlap

### Real architectural duplication

1. **Two card identities.** `captured_cards` stores free-text identity while the
   live inventory uses `catalog_items`/`tcg_cards` (and retains `catalog_cards` as a
   legacy compatibility layer). This is the biggest data-model seam.
2. **Two intake implementations.** Flatbed scanning and single-photo capture need
   different image handling, but their result should converge immediately on one
   catalogue and inventory service.
3. **Three listing concepts.** `pending_listings`, `shop_listings` and eBay export
   items represent overlapping draft/listable state. `pending_listings` is unused;
   `shop_listings` plus channel-specific export/link records is the cleaner model.
4. **Two eBay publication methods.** CSV bulk export and direct Trading API publish
   both exist, while an older Sell API publisher remains an unwired placeholder.
5. **Historical price/recommendation tables.** `market_observations` and
   `price_recommendations` are empty and superseded by `card_prices` plus current
   pricing services.
6. **Repeated capture modules.** Card, collectible and accessory capture/review
   modules repeat upload, batch-status, image-serving and approval mechanics.
7. **Documentation lineages.** README/security/Phase 1 documents still describe a
   sandbox-only read-only control plane, while the live platform now has production
   eBay, storefront checkout and write-capable routes. This is dangerous duplication
   of truth, not merely stale prose.

### Things that look duplicated but are valid

- Multiple captured rows for the same card can be separate physical copies.
- Catalogue products with the same normalised name can belong to different sets,
  games or product types.
- `catalog_cards` and `tcg_cards` temporarily coexist to preserve identifiers during
  migration; this is acceptable only while `catalog_item_id` is explicitly canonical.
- Market-watch listings and eBay listing mirrors are different: one discovers buy
  opportunities, the other mirrors the seller account.

## Reliability and maintainability findings

### Strong points

- Database integrity, foreign keys and the checked business-key duplicate rules are
  clean.
- SQLite is in WAL mode and remains proportionate to the current single-operator,
  low-concurrency workload.
- Reconciliation, price import, listing refresh, order import, market watch and scan
  ingest have scheduled services, and their current runs are healthy.
- Review queues fail safely: uncertain identifications wait for a human rather than
  silently entering stock.
- The navigation has already been condensed to workflow hubs, substantially reducing
  visible complexity.
- There is broad automated coverage and a large regression suite.

### Risks and gaps

1. **No clean release boundary (critical).** Production executes a heavily modified
   working tree. Make a reviewed checkpoint, tag it, and deploy only committed tagged
   revisions. Record the deployed revision in `/health` or Settings.
2. **Health check is incomplete (high).** It monitors seven jobs but omits
   `inventory-audit`, `scanbd`, `scanbd-restart`, backup timers, storefront health,
   `foil-refresh` and `sheet-check`. In addition, an `unknown` job state does not set
   `needs_attention`, so “all running on schedule” can be printed when systemd state
   could not be read.
3. **Manual migrations (high).** Startup `create_all()` cannot alter existing tables;
   many one-shot migration scripts and a zero `PRAGMA user_version` mean there is no
   authoritative database revision. Adopt Alembic or, minimally, a migrations ledger
   table with ordered, idempotent revisions and startup compatibility checks.
4. **Stale operational docs (high).** Sandbox/read-only claims conflict with current
   production behavior. Rewrite README/architecture/security/operations around the
   actual system and move Phase 1 documents under an explicit historical folder.
5. **Storefront launch gaps (high if public).** Legal pages contain business-name,
   ABN and contact placeholders. There are no real shop orders yet, so payment,
   webhook, reservation, cancellation and fulfilment need a controlled end-to-end
   rehearsal.
6. **Large modules (medium).** `review_queue.py` and several listing/inventory modules
   combine rendering, database operations, external calls and decision logic. Split
   only along services already visible in tests; avoid a wholesale rewrite.
7. **Observability remains local (medium).** Log files and dashboard status are useful,
   but a failure can still depend on someone opening the dashboard. Add one concise
   notification path for overdue/failed jobs and reconciliation findings.
8. **Source limitations (accepted).** Japanese Pokémon has no live price source and
   SDBH catalogue coverage depends on a retailer snapshot. These should remain visible
   data-quality states, not be “fixed” with guessed mappings or English prices.

## Necessary versus unnecessary

### Necessary now

- Catalogue, inventory and price history.
- Bulk scan intake and human review queues.
- Inventory/photo reconciliation.
- One primary eBay listing path and the eBay order return loop.
- Storefront only if it is actively being launched and supported.
- Authentication, audit logging, backups, health reporting and data export.
- Market search/watch where it informs real purchases.

### Keep, but subordinate

- Personal collection, collectibles/accessories, retailer goals, sealed economics,
  release calendar and deal checking.
- Direct eBay publishing as either the primary method or a clearly labelled fallback.

### Archive or hide unless a real use appears

- Empty AI tool-permission/job/approval framework (retain audit logging).
- Empty `market_observations` and `price_recommendations` tables/code.
- Unused `pending_listings` pipeline if `shop_listings` is canonical.
- Empty suppliers, manual CRM sales/customers, purchase lots, potential stock, goals
  and Whatnot operations.
- Historical architecture documents presented as if they describe the live system.

Do not immediately drop tables. First remove them from navigation and new development,
export any rows, verify no route/job uses them, then remove code and schema in a tagged,
reversible migration.

## Recommended cleanup sequence

### Phase 0 — establish a safe baseline (before feature work)

1. Review and checkpoint the current worktree; separate generated/backups from source;
   tag the exact revision now running.
2. Capture a fresh SQLite backup and test a restore into a temporary database.
3. Add the deployed revision and database revision to Settings/health output.
4. Fix the seven stale/missing prices or mark them explicitly “no supported source.”

### Phase 1 — make failure visible

1. Expand health checks to every service/timer and treat `unknown` as attention-worthy.
2. Add storefront HTTP and payment-webhook configuration checks.
3. Send one notification on new job/reconciliation failure; deduplicate repeated alerts.
4. Add a daily backup-success age check and a periodic restore drill.

### Phase 2 — choose canonical concepts

1. Canonical item: `catalog_items.id`.
2. Canonical owned stock: `inventory_items.id`.
3. Canonical price record: generic catalogue item + variant + source + currency + time.
4. Canonical sellable draft: `shop_listings`; channel records should reference it.
5. Choose direct publish or CSV as the default eBay path and label the other fallback.

### Phase 3 — remove noise safely

1. Hide dormant modules and measure whether they are requested for 30–60 days.
2. Move Phase 1/control-plane docs to `docs/history/` and publish one current operator
   guide and one current architecture diagram.
3. Remove the unwired publisher and superseded tables only through a versioned,
   backed-up migration.
4. Extract shared capture and review services without changing route behavior.

### Phase 4 — prove the business loops

Run and document one controlled example of each loop:

1. scan → review → inventory → price;
2. inventory → eBay listing → imported order → packed → stock decremented;
3. inventory → storefront listing → checkout/webhook → fulfilment/refund;
4. backup → restore → application starts and reconciliation stays clean.

## Definition of “clean and does not break”

EZBay is at the target state when a new operator can use five workflow entrances,
every owned item has one canonical identity, every listing points back to inventory,
all automation is idempotent and visibly monitored, the deployed revision is known,
the database revision is known, a restore has been rehearsed, and unused experimental
modules no longer appear in normal operation.
