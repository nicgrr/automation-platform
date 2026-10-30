# Handoff

Read this at the start of a session; update it before stopping. See the
handoff protocol in `AGENTS.md`.

## Current task — collectibles for Vinted (Codex, 2026-10-31)

Active agent: Codex. Branch `collectibles-vinted-preparation`, base `5785953`.
Operator: all figurines/collectibles are for Vinted ONLY, never publish them to
 eBay. Graded cards are separate and retain their existing workflow. Prepare
collectible identities/photos/descriptions and provisional AUD prices ending .99;
operator still confirms prices. Research Vinted browser options, do not publish.

In progress: audit owned stock + the two recent upload batches, add an enforced
collectible eBay publication/export exclusion, prepare an accessible Vinted
review/export area with price suggestions and unresolved identity flags. No
Vinted login, publication, or CAPTCHA automation in this batch. Preserve stock
quantities, confirmed prices and all photos; avoid duplicate inventory.

Existing dirty files at takeover (prior Codex session; preserved, not to be
committed as part of this task without explicit hunk isolation): api.py,
collectibles.py, ebay_photo_batch.py, ebay_publish.py, navigation_hubs.py,
photo_identification_review.py, untracked product_photo_review.py and its test.
They contain deployed mixed-product intake, photo display and graded safeguards.

Next: read publication/export and inventory paths; make minimal channel guard
and Vinted preparation changes; run focused tests; activate and verify the CRM
change within the user's ongoing authorization. No external listing writes.
Parallel agents are researching prices and Vinted rules read-only.
Validation: not run (planning checkpoint).

## Current task

Active agent: Claude Code (2026-10-30), branch
`lens-results-and-collectible-vocabulary`, base `22dd1a7`. Codex ran out of
credits mid-session; the operator asked Claude to finish Codex's work, then fix
CRM navigation (saves/uploads should return to the page the operator came
from).

## In progress

1. **DONE — took over Codex's uncommitted work** (operator requests from Codex
   session 2026-10-29 18:25–19:01 UTC; Codex reported each deployed live):
   representative card backs for front-only uploads
   (`representative_backs.py`), listing finish dropdown (`listing_finishes.py`,
   `ShopListing.finish`), and catalogue identification review for unmatched
   photos (`photo_identification_review.py`). 15 paths, untouched since 05:46
   local. Full suite on that tree: 2304 passed.
   - Problem found: `shop_listings.finish` was added to the live DB by an ad-hoc
     script (`scripts/migrations/migrate_listing_finish.py`), not Alembic. Live
     DB has the column but is still at revision `20261023_02`.
   - Committed Codex's work unchanged as `c91d924` (`Agent: Codex`); Alembic
     `20261030_01` replaces the ad-hoc script (`4592dfe`), tested on a fresh DB
     and a live snapshot.
   - Operator action after commit: run `.venv/bin/alembic upgrade head` (a
     no-op that records the revision) before the next CRM restart, or startup
     will refuse the revision mismatch.
2. **DONE — duplicate review (Codex, finished by Claude):** 13 Needs attention entries
   reviewed; report at
   `/home/nic/Documents/Codex/2026-10-30/Needs-attention-duplicate-review.md`.
   Operator answered that Son Gohan UGM5-002 DA is one card photographed twice,
   so one of its two live eBay listings is a duplicate. Operator approved
   (2026-10-30): end eBay item 267792969857 ($2.40, wrongly titled "C"; CRM
   listing `cf0041ed-…`, phantom inventory `8e6b8c1e-…` NORMAL qty 1) with
   `scripts/end_ebay_listing.py`, hide that CRM listing, and set the phantom
   inventory to 0 with an audit note. Keep the $49.95 DA listing
   (`54237f4a-…`, inventory `a965ae85-…`) untouched. Neither had sold.
   Status: DONE 2026-10-30. eBay 267792969857 ended (API success); CRM row
   retired via `scripts/retire_duplicate_ugm5_002_20261030.py` (rehearsed on a
   snapshot first; re-running refuses). The other 12 Needs attention entries
   need no action per Codex's report (8 already withdrawn, 3 live with
   unrecognised `imported` links — a UI classification bug, 1 withdrawn).
3. **CRM navigation fix (done, not yet deployed):** plan below in "Next steps".
   - Batch 1 DONE `ff5c543`: `navigation.redirect_back`, shared ui.py
     script (return params, trail, scroll, toast, Back link); card edits and
     photo audit converted; browser-checked; full suite green.
   - Batch 2 DONE (uploads): flow-origin cookie; card/collectible/accessory
     capture → queue end returns to the upload's starting page; personal
     collection upload uses redirect_back. Full suite 2331 passed.
   - Batch 3 DONE `3c84fd7` (listings): activate/hide/activate-all/edit and
     eBay cleanup dismiss use redirect_back; trail ignores message-only
     params. Browser-checked on the snapshot copy.
   - Batch 4 DONE (pricing): "Mark updated" returns to the list position.
     Full suite 2333 passed.
   - NOT YET LIVE: the running CRM still has the old code. To deploy:
     `.venv/bin/alembic upgrade head` (records 20261030_01; column already
     exists), then restart `ezbay.service`. Needs the operator's OK.
   - Not converted (deliberate forward flows or untouched areas): review
     save-and-next, capture→review, batch status pages, Listing Studio
     self-redirects, eBay export/publish steps, Whatnot, buying, sales,
     retailer goals, market watch. Convert with `redirect_back` if the
     operator reports them.
   - Not browser-tested: uploads (snapshot copy has no identification
     API key); covered by tests in tests/test_navigation.py. Throwaway test
   instance on 127.0.0.1:8091 (snapshot DB at `~/.cache/ezbay-navtest`, no API
   keys) is approved by the operator for browser checks.

## Done

- Recorded eBay orders for listings that have no SKU (14cf2ae)
- Documented the October checkpoint and the public storefront boundary (3e16063)
- Recorded the late-October live eBay operations run (401aa1c)
- Reviewed all 220 existing commits for agent attribution; documented all 41
  without a Claude trailer by theme, with absolute dates and commit references.
- Added shared instructions in `AGENTS.md` and its `CLAUDE.md` include alongside
  this handoff. No tests or services were run for this documentation task.
- Required saved plans before each small edit batch and progress/WIP commits
  afterward, with validation status, operator constraints and exact resume
  actions. Both agents read the same protocol through `AGENTS.md`.
- Added explicit agent trailers for new commits and recovery instructions for
  partially applied work. Existing uncertain history remains unattributed.
- Reviewed the documentation diff and commit scope; `git diff --check` passed.
  Only `AGENTS.md` and `HANDOFF.md` changed in this follow-up. `CLAUDE.md` and
  the Claude work-history subsection remain unchanged. No tests or services ran.

4. **Photo batch af62ce68 (in progress, 2026-10-30):** 26 cards, 6 done, 20
   held. Claude identified all 20 (see chat). Operator approved: (a) link
   photos + set finishes + Listing Studio drafts (no eBay publish); (b) add
   4 Chinese csv10C cards (Dudunsparce ex 179, Mamoswine ex 104, TR Crobat ex
   130, Psyduck 043) to catalogue + inventory; (c) eBay category mapping for
   pokemon_japan; (d) redesign the bulk photo review page. Trubbish, Deerling,
   Ferroseed, Houndour, Seaking are EXTRA copies (new inventory rows; leave
   the 2026-10-27 bulk-lot rows alone).
   - (c) DONE: pokemon_japan → eBay category 183454 added to production.
   - (d) DONE: one-at-a-time confirm flow (`photo_confirm.py`), committed;
     needs `ezbay.service` restart to go live.
   - (a)+(b) IN PROGRESS by the operator on their phone via that flow.
     Chinese Dudunsparce/Mamoswine/Crobat ex already had catalogue entries
     and active listings (set cn-csv10c); only Psyduck 043/222 is new.

5. **Shop ↔ eBay photo sync (2026-10-30):** eBay→shop DONE (`--refresh` on
   scripts/backfill_ebay_listing_photos.py: 265 read, 85 new, 0 missing).
   Image-hash audit of 254 linked live listings: 228 in sync, 20 with no CRM
   photo (shop uses eBay's), 6 where eBay has an older single photo:
   267796015344 Gardevoir, 267805604307 Yanma, 267796015386 Squirtle,
   267813837184 Loki, 267824797652 Dudunsparce, 267824797679 Shiinotic.
   Operator approved revising those six (photos + CRM titles) while KEEPING
   eBay prices: first set CRM price = eBay price for Gardevoir 2.40, Yanma
   6.23, Squirtle 3.39. DONE: all six revised and read back from eBay
   (prices unchanged). Loki needed a Trading API photos-only revise
   (Inventory API updated its inventory item but not the live listing);
   fixed revise_photos' missing XML declaration (`0c4f5dc`). Final audit:
   234/234 in sync, 20 live listings have only eBay photos (shop shows
   those), 0 out of sync. Nightly timer written (docs/systemd/
   ebay-photo-refresh.{service,timer}, ~03:40 Sydney) but NOT installed:
   the agent's permission rules block installing system services, so the
   operator installs it (commands in the commit message of that change).

6. **Duplicate Crobat ex (2026-10-30, operator-approved):** photo_confirm
   matched pair 6 to a duplicate local catalogue entry
   (local-e8908eacae92fc9fc8f2308c, number "130") instead of
   c1f26d7b… (130/222), so "Extra copy" created stock fbeb7db2… and listing
   aa7a892a… → eBay 267827676356 at $3.99, alongside the existing eBay
   267824797612 at $21.95 (listing c7d2c9fb…, stock 67fb27d2…). One physical
   card. Operator chose to END the $21.95 one and keep the $3.99 one.
   Plan: end 267824797612; withdraw c7d2c9fb, stock 67fb27d2 → 0 RETURNED;
   repoint aa7a892a/fbeb7db2/pair to c1f26d7b; delete the duplicate local
   catalogue entry; fix catalogue_guess to prefer the full printed number.
   DONE: eBay 267824797612 ended (cache shows Ended); CRM cleanup applied via
   scripts/retire_duplicate_crobat_20261030.py (kept listing on c1f26d7b with
   original stock 67fb27d2 qty 1; extra stock and duplicate entry deleted).
   photo_confirm fixed (full-number preference, unique stock handling).
   Needs `ezbay.service` restart for the confirm fixes. Note the kept
   listing is AUD 3.99 vs the old 21.95; operator's choice.

7. **Bundle singles (photo batch 7db66a0c, Claude Code session in the
   `remove-items-sale` worktree, 2026-10-29/30, operator-directed):** working
   directly in this checkout on the current branch (no branch switch: the live
   service reads this tree). Already DONE and audited (audit_events actions
   `ebay.listing.end_bundle_now_singles`, `bundle_cards.*`,
   `shop_listing.price_above_ebay_median`; DB backups
   `backups/automation-pre-*-20261029.db`): ended the 4 live bundle listings
   267785473765/67/68/71 (Trading API); added Celebi x2, Hisuian Zorua,
   Greninja ex as DRAFT listings with inventory (new catalogue item
   `tcgp-716205`); linked 10 existing + 10 new inventory rows to the live
   singles; set 13 CRM prices just above the eBay median (CRM only, NOT pushed
   to eBay; old prices in the session scratchpad and the audit events).
   Operator constraints: no eBay push until the operator reviews and presses
   Revise on eBay per listing; Heracross (listing q2 / inventory q1) and
   Tornadus (q5 / q4) stay unlinked until the operator gives quantities.
   - HAZARD found: the batch was retried 2026-10-29 20:58 local, which cleared
     the metadata stored on its pairs and re-proposed prices. Apply / Apply
     all on batch 7db66a0c would overwrite the new listing prices. Mitigation
     this session: set `proposed_price` to NULL on its READY pairs (old values
     kept in the audit event; a retry would re-propose them).
   - Pair 27 (Celebi) now has two catalogue matches (`neo4-106` original,
     `tcgp-716205` 30th Celebration Classic Collection): choose `tcgp-716205`.
     Pair 16 (Gothitelle) reports no match; its live listing uses `tcgp-647306`.
   - DONE (data): storage slots PK-01-001..013 (tray PK-01 is new, named like
     DB-01/OP-01) reserved for the 13 added inventory rows as UNCONFIRMED
     placements on batch 7db66a0c; `batch.storage_tray` set to PK-01. The
     operator confirms "stored" on the batch page, which copies the slot onto
     each inventory row. `proposed_price` cleared on 31 pairs. One audit event,
     `bundle_cards.protect_prices_reserve_storage`, holds the old values. Note:
     the script's dry-run committed (the audit helper commits the session), so
     the rerun with --apply stopped on its own "placement exists" check; the
     resulting state is exactly the intended one.
   - DONE (code, NOT deployed): read-only "Bundle singles" review page
     (`automation_control/bundle_singles.py`: list at `/bundle-singles` and a
     one-card view with Previous/Next at `/bundle-singles/{photo position}`),
     linked from the drawer's Review group (`ADMIN_NAV`, so the drawer is now
     13 links and `tests/test_nav.py` asserts 13), the top bar (hidden on
     phones) and the active-state prefixes in `ui.py`. Router registered in
     `api.py`. Checked read-only against the live DB: 32 cards, 13 reserved
     slots, 2 listings flagged "no inventory". Needs an `ezbay.service`
     restart (operator action); the restart also brings the earlier navigation
     fix (item 3) live, so run `.venv/bin/alembic upgrade head` first.
   - Validation: tests/test_bundle_singles.py (8) + test_nav.py +
     test_dashboard.py: 56 passed. Full suite (`pytest -q`, 2026-10-30, on
     the tree at `7095bad`): 2354 passed in 6m10s.
   - Open for the operator: confirm "stored" once the cards are in PK-01;
     Heracross/Tornadus quantities; Celebi pair 27 catalogue choice
     (`tcgp-716205`); Gothitelle pair 16 has no match; revise prices on eBay
     one listing at a time. The overnight eBay import now covers all 32 cards.

8. **Pick run and print-all labels (Claude Code session
   `ezbay-platform-review`, branch `feat/pick-run` in worktree
   `.claude/worktrees/pick-run`, 2026-10-29/30, operator-requested):**
   - DONE (code): `/ebay-sales/pick-run` lists every open eBay order's lines
     sorted by drawer then slot (`OP-01-009` → drawer OP-01, slot 009), then
     cards with no slot (Pokémon singles live in the bulk drawers), then
     sku-less `item:` lines, then bulk lots. Orders lettered A, B, … oldest
     first. "Print all labels" (`POST /ebay-sales/pick-run/print-labels`)
     prints each open order's label once, skipping any order with a
     `label.print_ebay` audit event, and stops with 502 on the first printer
     error. `print_ebay_label` now records that audit event. "Open orders" is
     one shared definition (`ebay_sales.open_orders`), also used for a new
     `/ebay-sales` nav badge (`NAV_COUNT_HREFS` in `ui.py`). No schema change.
   - Storage, per the operator: 2 rows of 4 three-drawer units; one One Piece
     (OP-01), one Dragon Ball (DB-01), the rest random bulk (Pokémon singles
     included), 3 free. 1,406 available rows have no slot.
   - Rebased onto `04f2342` cleanly. Validation: targeted (pick run, nav,
     navigation, bundle singles, eBay sales, dashboard, order import, labels)
     144 passed. Full suite in the worktree (2026-10-30): 2360 passed, 3
     failed — all in tests/test_representative_backs.py, because the worktree
     has no `scan_ingest_data/media/.../representative-backs/` images (gitignored
     data); the same 3 pass in the live checkout. Gotcha for worktree runs.
   - Deploy: fast-forward this checkout to `feat/pick-run`, tag, restart
     `ezbay.service`. The restart also brings items 3, 4(d), 6 and 7 live.
     Operator approved the pick run deploy; restart pending their OK on that
     wider scope.

7. **Vegito P-065 (2026-10-31):** duplicate audit of all 264 live listings
   found no other doubles; Vegito "Gold Stamped" eBay 267796015378 ($12.85,
   flatbed scan, no visible stamp) vs promo 267821547918 ($4.95). Operator:
   end 267796015378. DONE: ended on eBay; CRM listing 1800c342… withdrawn.
   Stock 2e2bd483… (tcgp-252816 Gold Stamped) retired to 0 RETURNED on
   2026-10-31 at the operator's request: its scan is the regular promo and
   they own no Gold Stamped copy. One Vegito remains (901d2419, eBay
   267821547918, $4.95).
   Open questions to operator: physical counts for Mega Heracross ex me2-4
   (eBay qty 2, stock 1) and Tornadus svp-210 (eBay qty 5, stock 4).

## Next steps

- CRM navigation fix. Problem: ~213 POST handlers redirect to fixed pages;
  card edits rebuild `back` from sort only (search, filters and scroll lost;
  callers outside `/inventory` are sent to `/inventory`); `ui.brand_header`
  hard-codes "← Dashboard". Operator uses the CRM as an iOS PWA with no browser
  back button. Reproduced on the test copy: card edit from
  `/inventory/cards?sort=name&q=pikachu` lands on `?sort=name` at the top;
  withdraw from `/shop-listings?status=active` lands on `/shop-listings` at the
  top. Design: `ui.py` submit listener adds hidden `_return_to` (current URL)
  to POST forms and remembers scroll; new `navigation.redirect_back()` accepts
  only same-origin relative paths; convert "stay where I was" handlers,
  keep forward flows (save-and-next, create→open, batch status, eBay
  confirm steps); "← Back" header link. Order: card edits + photo audit,
  uploads, listings, pricing. One commit per area.
- Operator to define the remaining lens-results and collectible-vocabulary
  goal; this documentation task does not establish further implementation work.

## Work history by agent

Each agent keeps its own section. Tie summaries to reliable authorship, explicit
`Agent:` or `Co-Authored-By` trailers, or directly recorded work in the current
session. `git log -i --grep='Co-Authored-By: Claude'` lists the commits behind
the Claude section. A shared human author identity alone is not attribution.

### Claude Code (2026-09-08 to 2026-10-29)

Covers the 80 most recent Claude commits. Older ones weren't reviewed because
the disk was full.

- **NL Vault storefront (Sep 8–15):** GSAP motion, split cards/collectibles
  hero, card-collage homepage, background built from the storefront's palette, cart
  "Continue shopping", Whatnot icon, editable shop listings. On Oct 29 it moved
  to public catalogue mode (`STOREFRONT_MODE=catalogue`), which links out to
  eBay/Vinted/Whatnot instead of running checkout.
- **Two-way storefront ↔ eBay stock sync (Sep 10, phases 1–5):** schema, eBay
  write adapter and widened OAuth scope, manual Publish-to-eBay, website sale →
  eBay auto-sync, eBay order polling reflected back onto the site.
- **Personal Collection lane (Sep 9):** collector-export import, card images,
  price column/sort, set-name alias fixes, per-card photo upload, real One
  Piece TCGplayer images and fixes for the wrong printing being picked.
- **Pricing (Sep 10 – Oct 29):** eBay pricing fixes (Nidoran gender symbols,
  parenthetical sub-variants), 2x market-signal cap on single cards, sealed
  product (ETB/booster box) market prices and Deal Check matching, nightly price
  backfill repaired, timer-driven eBay price runs, 473 stock copies made visible
  to pricing, pricing foundation with review-queue bulk save (Oct 6),
  collectible pricing, a price-safety gate and rarity families (Oct 29).
- **Photo recognition and audit (Sep 10–13):** inventory photo audit as a cached
  review page, "wrong card, re-file it", detection of footer-truncated cards,
  Google Lens result parsing with a field vocabulary, Sonny Angel figure naming
  (Flower Series, Cat Life Series), candidate pictures, stock-art fallback.
- **eBay connection and ops (Sep 13–15):** eBay Info row in exports, settings
  page, fix for a token refresh downgrading production, dead background jobs
  surfaced, Trading API listings view (sees relists), eBay Listings dashboard
  widget, 30-day sliding dashboard sessions, release calendar cleanup.
- **Multi-game scan ingest and inventory (Sep 21–22):** Dragon Ball, One
  Piece, Japanese Pokémon and Super Dragon Ball Heroes detection and pricing;
  Dragon Ball Super Divers catalogue; /inventory turned into a two-level tile
  drill-down by game; 303 invisible holdings fixed; collector-app CSV import
  removed; eBay export as one variation listing per set with a 13% fee markup
  for rare/Dragon Ball cards.
- **Labels (Oct 8):** landscape shipping labels on the HotLabel HL-M9.
- **October checkpoint (Oct 29):** committed about three weeks of live,
  previously uncommitted work. That includes the Listing Studio (eBay titles,
  descriptions and photo sets, approved revisions), front/back photo batches,
  bulk-order picking manifests, and the workflows wired into the CRM shell and
  dashboard. It also includes the dated one-off live eBay operation scripts and
  recording eBay orders for listings without a SKU. See the 2026-10-29
  `CHANGELOG.md` entry.

### Codex

No commit predating `7227ba4` can be reliably attributed to Codex. Reviewed all
220 commits across local refs before that handoff commit: every author and
committer is Nic, and no commit message, trailer or author/committer identity
mentions Codex. The 179 commits with a `Co-Authored-By: Claude` trailer are
excluded below.

These themes list all 41 remaining commits as **unattributed**, without claiming
them as Codex work. Dates are absolute commit author dates, spanning 2026-08-24
to 2026-10-03. `CHANGELOG.md` supplies context only; its 2026-09-24 entries were
recorded in the 2026-10-01 baseline commit (`6c776bd`).

- **Early capture and pricing (2026-08-24 to 2026-09-04):** currency and
  PokemonTCG adapters, card rotation/image processing and price review
  (`1c6c3a4`); later reverted the PriceCharting adapter (`a34f3e7`).
- **NL Vault presentation and discovery (2026-09-08 to 2026-09-09):** storefront
  redesigns, hero character strip added then removed, official Pokemon
  department logo, simpler wordmark, technical SEO and breadcrumbs, product
  image fixes, set filters, sorting and a two-column grid (`540f1c8`, `8eba234`,
  `ae70226`, `621befa`, `7a356ec`, `1c014bd`, `65f6f04`, `99c6121`).
- **Photo capture mechanics (2026-09-09 to 2026-10-02):** rembg background
  removal for collectible photos, then shared upload, background-batch and
  review-queue mechanics across card, collectible and accessory capture
  (`64aa4c0`, `6eec260`).
- **NL Vault administration and orders (2026-09-09 to 2026-10-02):** draft
  listing review and activation, storefront health monitoring and launch
  prerequisites, atomic stock reservations, guarded payment/cancellation/refund
  transitions and a transaction drill (`9413105`, `58e00ed`, `b335054`).
- **Platform baseline and release checks (2026-10-01 to 2026-10-02):** platform
  review and roadmap, expanded operational health, application/database revision
  reporting, Alembic baseline and startup schema enforcement, explicit pricing
  coverage, and service-user file/directory permission checks including new
  files (`6c776bd`, `8d38ce6`, `c5d5a61`, `7fdebb4`, `a938018`).
- **Canonical inventory and pricing identity (2026-10-01 to 2026-10-02):**
  explicit catalogue identity in prices and inventory, compatibility links and
  holding uniqueness, plus PokemonPriceTracker credit protection against
  non-Pokemon and zero-stock lookups (`f9918e2`, `635cd8a`, `1a8ad83`).
- **Recovery evidence (2026-10-02):** dashboard backup freshness and restore
  drill health, a scheduled restore drill, and a repeatable historical-release
  rollback rehearsal (`9e87f01`, `64320d1`).
- **Workflow consolidation and retirement (2026-10-02):** navigation aligned
  to daily workflows, one canonical sellable-draft queue, staged retirement of
  unused Goals and generic job/approval writes, and refreshed operator,
  architecture and recovery guides (`dcddb83`, `4e9764d`, `6d37b50`).
- **Market research and purchasing (2026-10-01 to 2026-10-02):** AU/JP
  collectible discovery, alert intake and Retailer Goals captured in the
  baseline; purchase candidates merged into Market Watch; retailer purchases
  linked to lots and inventory; supplier receiving with landed-cost allocation
  and duplicate/refund safeguards (`6c776bd`, `c51c0d2`, `888a658`, `541f7a5`).
- **Whatnot and direct sales (2026-10-02):** inventory-backed show queues,
  reservations and sale/unsold/giveaway outcomes, plus direct-sale stock
  consumption, customer matching, cost snapshots and guarded voids
  (`8ecd322`, `227496f`).
- **Direct eBay publishing (2026-10-02 to 2026-10-03):** production preflight
  and explicit confirmation, retry-safe offer reuse, duplicate-listing guards,
  new-SKU no-offer handling, placeholder-policy rejection and wider OAuth
  consent, independent order-import readiness, and the recorded live Alcremie
  canary validation (`f611f01`, `35e370b`, `7df4f7b`, `4158c90`, `1b0088b`,
  `0377e8b`).
- **eBay variation details and repository hygiene (2026-10-03):** fuller
  number/set-total, printing and rarity labels in variation exports, and
  ignored variation-revision backups (`f6e0b80`, `a5ddd6b`).

Subsequent verified Codex work:

- **Shared instructions and handoff (2026-10-29):** created the shared agent
  instructions and handoff, including the historical attribution audit
  (`7227ba4`, performed in this Codex session); saved the plan for checkpoints
  before edits (`6401344`, carrying `Agent: Codex`).

## Gotchas

- The NL Vault storefront is a bare uvicorn process, not a systemd unit;
  restart it by the PID on its port.
- A separate cleanup of the dashboard/nav UI was in progress in September;
  check with the operator before touching `ui.py` or the nav in `dashboard.py`.
- On 2026-10-29 the machine's disk was full ("No space left on device");
  shell commands and git writes failed until space was freed.
- The Claude photo-recognition/audit bullet is dated Sep 10–13, but its Cat
  Life Series work is commit `3b6d0fb`, dated 2026-09-15. The Claude section is
  preserved unchanged; that theme extends through 2026-09-15.
- Handoff checkpoints rely on both agents following `AGENTS.md`; no timer or
  hook is installed. A sudden usage cutoff may leave the current edit batch
  incomplete. Inspect the diff against the saved plan before resuming; Claude
  Code still needs to be started or resumed by the operator.

## Last updated

Claude Code, 2026-10-30 15:35 AEDT (pick run, item 8)

Last updated for current task: Codex, 2026-10-31.
