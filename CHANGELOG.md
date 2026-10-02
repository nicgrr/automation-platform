# Changelog

Entries from the point this file was created (2026-09-07) onward. Earlier history
lives in `git log`.

## 2026-10-02 — Recovery evidence on the dashboard

The dashboard and on-demand CRM health report now verify that the latest
encrypted-backup success is recent and that a real restore drill has passed in
the last 45 days. A repeatable, non-destructive restore drill copies a snapshot
to temporary storage, verifies integrity and foreign keys, migrates it to the
current schema, preserves key row counts and boots the application health
endpoint. A tracked monthly systemd timer keeps that evidence current.

Release rollback is now documented and executable as a non-destructive drill. It
exports a historical release tag into temporary storage, pairs it with the named
pre-release database snapshot, and exercises the historical application's real
schema gate and health endpoint without changing the live checkout or database.

The primary drawer now follows the five daily workflows: Add stock, Review,
Inventory, Sell and Research. Specialist destinations remain in their workflow
hubs and command palette; Retailer Goals now lives inside Market Tools instead of
competing with Research as a top-level destination. The approved photo-capture
handoff was also production-audited: all promoted rows retain valid canonical
catalogue and inventory links, while ambiguous captures stay visible and blocked.

Potential Stock is now one workflow inside Market Watch rather than a second,
empty manual-entry board. Any discovered listing can be tracked through Watching,
Contact Seller, Offer, Accepted, Purchased or closed statuses, with target/maximum
prices and notes retained across market refreshes. Dashboard and analytics consume
these canonical candidates. The empty legacy table remains for the observation
window but its form no longer creates duplicate records.

`shop_listings` is now the single sellable-draft queue used by the dashboard,
eBay hub, storefront administration, bulk export and direct eBay publishing.
The historical `pending_listings` table had zero production rows; its build/list
endpoints now route to canonical drafts, while legacy detail handling and schema
remain available during the observation window.

Direct eBay publishing now has a read-only per-listing production preflight and
an explicit live-publication confirmation page. Its Inventory API payload now
includes the required item specifics, ungraded-card condition descriptor,
Content-Language header and GTC duration. Retries discover and reuse an offer by
SKU, including reconciling a publish that succeeded just before a process crash,
so retrying cannot casually create a second offer. CSV remains the labelled bulk
and recovery path; no production canary was published automatically. The old,
unwired `listing_pipeline/publish.py` placeholder was removed so there is only
one real direct-publish implementation.

Card, collectible and accessory photo capture now share the common mechanics
for saving uploads, settling background batches, rendering batch status and
advancing pending-review queues. Their existing URLs, recognition fields and
catalogue/inventory decisions remain independent; this removes duplicated
plumbing without pretending the three stock types have identical business rules.

NL Vault checkout now reserves listing, physical-inventory and bulk-lot stock
atomically before redirecting to Stripe. Payment, expiry and cancellation race
through guarded one-time transitions, so webhook retries cannot double-subtract,
restore twice or cancel a payment that already won. The operator can confirm a
manual payment, safely expire and cancel an open Stripe session, or record a
completed refund with optional restocking only after the returned goods are back
in hand. A temporary-database transaction drill covers checkout, signed webhook,
packing, shipping, cancellation and refund without external calls or real money.

Whatnot shows now have a draft/live/completed/cancelled lifecycle backed by real
owned inventory rather than free text. Queueing reserves stock and rejects items
still allocated to NL Vault/eBay; sold, unsold and giveaway outcomes respectively
create a deduplicated Sale/customer handoff, restore stock once, or deplete it as
a giveaway. Exact Seller Hub fees are preferred and a missing rule can no longer
silently produce zero fees. A temporary-database rehearsal covers the full show
without contacting Whatnot or another external service.

Retailer purchases now create linked purchase-lot lines and canonical sealed
inventory automatically in the same transaction. Shipping is allocated into
landed inventory cost, matching order numbers group into one lot, duplicate form
submissions do not double-charge the fund or duplicate stock, and refunds are
blocked while units are allocated or no longer fully available. Safe refunds
reverse the ledger, inventory and linked-lot state together. A temporary-database
drill covers purchase, channel-allocation blocking, refund and repurchase without
external calls.

Direct/social/in-person sales now select canonical owned inventory instead of
accepting disconnected free text and a hand-entered cost basis. Recording a sale
atomically reduces stock, snapshots cost, reuses a customer by normalized email
or name, and rejects channel-allocated stock and duplicate form submissions. A
guarded void restores stock exactly once and voided sales no longer inflate
dashboard, analytics or customer lifetime value. Sales and customer records are
now available through the command palette, with a temporary-database rehearsal
covering the full flow without external calls.

Suppliers and purchase lots now form a real stock-intake workflow rather than
standalone reference forms. Supplier products must link to canonical catalogue
items; supplier lots snapshot quoted costs, allocate the exact final purchase
price proportionally across their lines, and receive inventory once with both
line-total and per-unit cost evidence. Retailer-generated lots remain protected
from manual edits. Suppliers and Purchase Lots are now available under Market
Tools after a no-external-call transaction rehearsal passed.

Phase 5 retirement has begun without deleting compatibility data. The unused
generic Goals routes and analytics panel are removed while their empty table is
retained. Generic job and approval writes now return `410 Gone` and record the
rejected attempt in the audit log instead of creating disconnected records. The
production baseline, earliest 60-day review date and mandatory export/backup/
migration checks are recorded in the deprecation register.

Current operator, architecture/data-flow and security/recovery guides now replace
the Phase 1 control-plane documents as active instructions. Superseded Phase 1
files live under `docs/history/` with explicit historical labels. The acceptance
journeys for stock intake, eBay, NL Vault and verified recovery are documented
against the consolidated platform.

The PokemonPriceTracker backfill now proves a held card is actually a Pokémon
printing before spending a search credit. `catalog_cards` also contains scanned
Dragon Ball references, so the older table-based assumption was consuming the
daily allowance on guaranteed misses. Zero-quantity stock is excluded and cards
with no price from any source are prioritized ahead of already-covered backlog.

Photo-capture promotion now reuses an existing draft for the same catalogue item
and printing instead of creating a second sellable listing. Direct eBay preflight
also blocks any listing whose inventory is already linked to another active or
draft listing. The sole production duplicate found while preparing the canary was
an obsolete September Alcremie draft; it was withdrawn through the audited
workflow after a clean online backup, leaving the newer canonical listing with its
actual card photo and approved price.

A real read-only production canary check exposed that eBay represents "no offer
exists for this SKU" as HTTP 404 with Inventory API error `25713`, rather than the
documented-looking empty offer list used by the mocks. The write adapter now maps
only that exact response to an empty result so new SKUs proceed to `createOffer`;
all other 404 responses still fail closed.

The first confirmed canary attempt stopped before offer creation because all four
configured Seller Hub prerequisites were the explicit
`unused-order-polling-only` placeholder. Write readiness now rejects placeholder
values instead of checking only that they are non-empty. The OAuth consent flow
also requests eBay's `sell.account` scope so, after the owner reconnects, existing
business policies can be read and verified; refreshes preserve an older token's
original scopes until that reconnection occurs. The staged inventory SKU remains
private with zero offers and the failed local link is resumable.

## 2026-10-01 — Platform review, truthful health and schema baseline

Completed the head-to-toe platform review and recorded all 19 owner decisions in
`docs/EZBAY_PLATFORM_REVIEW_2026-10-01.md`, with the sequenced delivery plan in
`docs/EZBAY_IMPLEMENTATION_ROADMAP.md`. Expanded dashboard health from seven to
thirteen operational dependencies: unreadable state is now attention-worthy, and
scanner-button, scanner-recovery, inventory-photo-audit, encrypted-backup,
backup-check and backup-maintenance jobs are covered.

Settings now shows the exact application Git revision (including a dirty-working-
tree warning) and database revision. Adopted Alembic, generated and blank-database
tested a complete static schema baseline, reconciled historical SQLite type/index
representations, made a fresh integrity-checked backup, and stamped production at
`20261001_01`. `alembic check` reports no schema drift and no business table or row
was changed by the stamp.

The first restart caught owner-only (`0600`) permissions on newly tracked source:
the test user could import it, but the `ezbay` service user could not. Normalised
tracked files to their Git modes and added an explicit service-account import check
to the release procedure; the API then restarted and passed its health probe.

Follow-up hardening added virtual-environment and tracked-directory permission
checks and made the market watch runner import-safe under the service account.
Alembic revision `20261001_02`
records whether a catalogue item has automated price coverage: three manually
catalogued printings are explicitly `manual_only`, while four genuinely stale
supported prices remain visible as work. Application startup now refuses an
unversioned or mismatched database instead of trying to repair it with
`create_all()`, and Settings reports the expected-versus-current revision.
A non-destructive restore drill then proved the pre-migration backup could be
upgraded to the current head with all core counts preserved, clean integrity and
foreign keys, and a healthy application startup.

NL Vault now has a tracked, restartable systemd service, a database-backed health
endpoint and dashboard monitoring of both the process and HTTP response. Its public
launch gate separately names every missing Stripe and legal prerequisite; legal
pages read confirmed facts from configuration, retain obvious placeholders while
incomplete, and remain `noindex` until the owner explicitly marks the reviewed text
approved. Running the private shop can no longer be mistaken for launch readiness.

Pricing code now uses `CardPrice.catalog_item_id`, matching the generic catalogue
foreign key that the field has represented for Pokémon, One Piece, Dragon Ball,
sealed stock and collectibles. The physical `card_id` column and an ORM synonym
remain during the compatibility window, avoiding a risky 97,000-row table rebuild
while removing the misleading name from active readers and writers.

Inventory now has the same canonical identity end to end. All active readers
and writers use `InventoryItem.catalog_item_id`; the database requires it and
enforces one holding per catalogue-item/variant/condition. The older nullable
`card_id` link is retained only for CatalogCard scan compatibility, must agree
when present, and legacy card-only ORM writes are promoted automatically. The
migration preserved all 2,140 holdings and 3,663 units in a production-copy
drill with clean integrity and foreign keys.

## 2026-09-24 — Generic collectible discovery and alert inbox

Expanded the Sonny Angel market page into a generic AU/JP collectible watch
workflow. Users can add, pause and scan watch terms; eBay continues to use its
exact item-location filter, while optional Brave public-web discovery remains
disabled until both an API key and licensed result-storage permission are
configured. Added deduplicated `.eml`/IMAP intake for Gumtree, Buyee,
ZenMarket, FROM JAPAN, Depop and Facebook alerts, plus a PWA share target and
manual listing intake that never stores marketplace credentials or fetches the
shared page. A twice-daily systemd timer refreshes watches without sending
messages or making purchases. The additive migration preserved all market
history and backfilled the existing Sonny Angel listings into the seeded
watch.

## 2026-09-24 — Australia/Japan market scope and marketplace intake

Restricted eBay market search and the Sonny Angel market watch to sellers in
Australia or Japan using eBay Browse API's official `itemLocationCountry`
filter. The watch page now filters by seller country, excludes older rows from
other locations, shows the configured scope, and can launch a fresh AU/JP eBay
scan. Added validated manual link intake for Facebook Marketplace and Depop
finds, including AUD/JPY landed-cost conversion. This deliberately does not
scrape either service or store a personal login: Meta has no public Marketplace
search API, and Depop's API is partner-only rather than a public market-search
feed.

## 2026-09-24 — Condensed workflow navigation

Replaced the 39-link drawer with eleven workflow entrances across Home,
Stock, Selling, Buying & research, and System. Added native mobile-friendly
hubs for Scan & add, Review centre, eBay, NL Vault and Market tools. The
Review centre carries one combined queue badge; every specialist route keeps
its original URL and remains directly searchable through Ctrl-K. Settings now
links to CRM data exports, and the dashboard quicklinks use the same condensed
map as the drawer.

## 2026-09-24 — Retailer Goals / Gameology Guild tracker

Added a native `/retailer-goals` module for accumulating a weekly buying
fund while measuring eligible Gameology Guild spend and the actual premium
paid over trusted Australian competitors. It includes configurable $1k/$5k
milestones and premium bands, a sealed-product Buy Queue, fund affordability,
completion projections, retailer trust controls, price history, an objective
recommendation panel, purchase/refund history, inventory creation through the
existing `InventoryItem` model, notification preferences for future monitors,
and a compact dashboard widget. No purchasing or payment automation exists.

The additive `migrate_add_retailer_goals` migration created ten generic
retailer/program tables and seeded Gameology plus three requested products
with blank prices and product URLs. A SQLite-consistent pre-migration backup
is at `backups/automation-pre-retailer-goals-20260924.db`; integrity and
foreign-key checks passed. Full suite: 1,592 tests green.

## 2026-09-15 — A release calendar with something on it, and an obsolescence sweep

**The release calendar is no longer a flat add-only table.** It shipped
with no way to fix a typo or mark something released short of the
database, and 0 rows to look at. Now: `/release-calendar/{id}/edit` and
`/delete` (same validation-and-404 pattern as the shop-listing editor),
and every entry gets a status pill computed from today's date against its
release date and supplier deadline -- *order deadline soon* (≤7 days,
red inside 2), *deadline passed*, *releasing soon* (≤14 days), *upcoming*,
*released* -- with rows sorted urgent-first instead of insertion order, so
the thing that needs a PO this week is what you see first, not buried
under everything else. Seeded with the two confirmed upcoming English
Pokemon TCG releases (cross-checked across two independent sources):
30th Celebration ETB (2026-09-16) and Mega Evolution: Delta Reign ETB
(2026-11-06, order by 2026-10-24) -- `scripts/seed_release_calendar.py`,
idempotent, safe to re-run.

- 18 new tests (7 for edit/delete/status/sort, 3 for the seed script, plus
  the pill-styling gap the first pass of this missed -- `.listing-actions`
  was only ever defined in `shop_listings_admin.py`'s own stylesheet, not
  this file's). Full suite (1439) green.

**Obsolescence sweep**, per request -- found and cleared:

- `automation_control/docker_broker/` -- dead directory, source already
  gone in an earlier uncommitted diff, nothing left but a stale
  `__pycache__` and zero references anywhere in the app. Removed; app
  still imports clean.
- Root-level eBay File Exchange CSVs and ad-hoc reports -- all
  regenerable from the scripts that write them, none of it source.
  Gitignored rather than deleted, so nothing on disk is lost.
- Two scan-queue sheets that had been stuck for a week, each a scan of a
  stray knife/letter-opener rather than cards (confirmed by looking at
  the actual images, not just trusting the ingest heuristic that flagged
  them as held). Moved to `scan_ingest_data/discarded/`, out of the
  processing queue.

Flagged for a decision rather than acted on: `backups/` has grown to
1.1GB of mostly full-DB snapshots from this week's corrections; three
scan sheets remain genuinely held (one recoverable 3x3 Prismatic
Evolutions grid, two single-card scans of unclear intent).

## 2026-09-15 — Edit a listing, and three "eBay ..." labels that read as one

**A shop listing can be edited.** Fixing Kappa's stale price meant hand-
editing the database, because nothing in the app could re-touch a
listing's title or price after it was created. `/shop-listings/{id}/edit`
-- title, price, quantity, condition -- with real validation (empty
title, non-numeric or non-positive price, negative quantity all rejected
with a clear message, not a 500) and every change recorded with its
before/after values, the same as the correction made by hand. Not gated
on status: a withdrawn or sold-out listing's data can be just as wrong as
an active one's, so "Edit" is on every row regardless.

**Navigation**, on inspection, split into two different things:

- *The storefront's* links and its nav-drawer toggle -- checked both by
  walking every page and testing every href against the real route table,
  same method as the original admin-app audit. Clean: nothing broken.
- *The admin drawer* had a real, self-inflicted problem: three items
  added today for three different eBay features all read as
  "eBay ... something" sitting next to each other --
  "eBay listing drafts", "eBay listings", "eBay export" -- with no way to
  tell from the label alone which shows what's actually live, which is
  an unpublished draft, and which is the bulk-upload file builder.
  Renamed to name the function: "eBay drafts (unpublished)", "eBay live
  listings", "eBay bulk upload file" -- and the on-page headers/titles
  now match, so what you land on says the same thing the drawer promised.

- 27 new tests for the edit form. Full suite (1437) green.

## 2026-09-15 — NL Vault gets a background

The page canvas was flat white -- `body{background:var(--paper)}` -- behind
an otherwise "playful, colourful collectibles-store look" (the file's own
words). Asked for something with the energy of a dense anime-poster
collage: bold saturated colour, layered composition, diagonal motion.

Not literal character art -- reproducing other artists' copyrighted
characters on a storefront that sells product isn't something to build,
collage reference or not. What actually carries across is the *energy*:
built from the four accent hues already used everywhere on this page
(the same brass/teal/blue/pink as the buttons, tags and ribbon badge, not
a fifth palette invented for this one thing) as large soft colour blobs
placed asymmetrically across the viewport, plus a fine dot-grid texture
for the pop-art/print quality a poster collage has up close. Pure CSS,
zero new assets -- the file's own rule ("no build step") holds.

Every real surface -- the ink header, white product cards, white panels
-- stays fully opaque, so the background can be this bold without ever
touching legibility: it only shows in the margins and the gaps between
cards. `body::before`, `position:fixed`, so the blobs hold their place
as the page scrolls past a long product grid rather than tiling
awkwardly downward.

The hero band (the wordmark, "Fresh in the vault") gets a bolder version
of the same system rather than any new foreground shapes -- a prior full
card showcase in that exact spot was pulled for reading as clutter (see
the file's own history), so the fix here is depth and colour behind the
existing calm layout, not more content competing with the wordmark for
attention.

- All 82 existing storefront tests pass unchanged -- pure background CSS,
  no HTML content touched. Full suite (1426) green.

**Not done here:** the live storefront process (`storefront.app:app` on
:8090) has no systemd unit and isn't running under tmux/screen -- a
detached background process from four days ago. Restarting it to pick up
this change means a few seconds of downtime for real shoppers, which
needs your say-so rather than mine.

## 2026-09-15 — Working through the "ready to build" checklist

Five items, one already done, one genuinely new bug found along the way.

**Photo audit fix button** -- already built. `/inventory-audit/refile`
existed, fully wired, with its own merge-or-create logic and a button on
the page -- someone (uncommitted work in the tree) had already closed this
gap since the original audit. Correcting the checklist rather than
duplicating it. What was still missing: the form made you retype the set
code and number off the photo by hand, even though "looks like" is
already that answer sitting in the flagged row. Pre-filled now, still
editable, still requires a real click to confirm.

**One-action eBay export** -- `/ebay-export`. Chains
`export_ebay_bulk_listing.py` and `convert_to_ebay_template.py` (both
given the standard `main(argv)` signature so they can be called in-process
rather than shelled out to) and hands back the finished File Exchange CSV
as a download. Verified end to end against the real account, which caught
two real bugs: the route itself forgot to pass the letter/parcel shipping
policy names through (would have silently fallen back to placeholders
eBay rejects), and separately, uncommitted work-in-progress in
`convert_to_ebay_template.py` (real variation-listing support, verified
against a live competitor listing) crashed on a `C:Rarity` field this
account's actual downloaded template doesn't have a column for -- fixed
by dropping any field a row emits that the template doesn't support,
rather than failing the whole export over one optional item specific.

**A timer for the eBay listings cache** -- `ebay-listings-refresh.timer`,
daily 03:31. The refresh logic was extracted out of the "Refresh from
eBay" button into `refresh_from_ebay()`, shared by the route and the new
`scripts/refresh_ebay_listings.py`, so the cache can't drift depending on
which one last ran it. Added to `job_health.py` alongside the other four
jobs. Running it live surfaced real news: **17 active listings now**, up
from 2 -- the eBay upload from a few days ago evidently went through.

**Personal Collection's page weight** -- 810 forms (2 per row x 405 rows)
cut to 407: every "List for sale" button now references one shared form
via HTML5's `form=`/`formaction=` instead of carrying its own `<form>`,
which is valid where a per-file-input upload form genuinely can't be
consolidated the same way. DOM element count roughly halves; raw page
weight barely moves, since the fix targets element count, not bytes --
reporting that plainly rather than as a KB win it isn't.

**Dashboard performance** -- diagnosed, not yet fixed. Profiled the route
directly rather than guessing: `_latest_prices` + `_latest_ebay_prices`
account for ~73% of render time, because both pull every historical
`card_prices` row for every held card into Python and pick the latest by
hand, rather than a SQL-side "latest per group" query -- a reasonable
call when the comment above them was written, now costing 25,000+ row
loads against a table that has grown to 41,868 rows and keeps growing via
the pricing timers built this week. Shared by 6 modules
(quick_price/analytics/deal_check/dashboard/inventory_review/market_search),
so a rewrite is correctness-sensitive and deserves its own dedicated,
tested pass rather than a rushed inline change alongside everything else
today.

- 15 new tests across the five items. Full suite (1426) green.

## 2026-09-15 — Cat Life Series

14 figures added -- 9 base coat patterns plus two separate rarity tiers,
Lucky (3) and Secret (2) -- cross-checked between the brand's own release
post and Toysez before landing, same as every prior addition.

The names in first search results ("Black Cat", "Silver Tabby") looked
like generic pattern labels rather than the brand's real names; fetching
both sources directly confirmed the figures really are just named after
their coat ("Calico", "Tuxedo", "Black") -- the fish, chicken leg, and
milk bottle seen in Nic's own photo of the set are per-figure props, not
separate names.

The master list's raw export shortens the series to **"Cat Life"**; every
retailer and the brand's own product page call it **"Cat Life Series"** --
the same Sun-Kissed-Style-vs-Summer-Vacation shape as before, aliased the
same way.

Lucky and Secret are two named rarity tiers on eBay's own product page,
collapsed to one `is_secret` flag here (this vocabulary has no
in-between), with the tier kept in `secret_label` for anyone reading the
CSV by hand.

- 3 new tests. Full suite (1412) green.

## 2026-09-15 — eBay Listings on the dashboard, and a sweep for the same bug elsewhere

The dashboard gets an **eBay Listings** widget, reading the same local
cache /settings and /ebay-listings already read -- no live eBay call on
every dashboard load. Shows the cached active count and when it was last
checked; **never a silent 0** when nothing has been checked yet, which is
the exact failure that prompted /ebay-listings to exist in the first
place (the Sell API this app is built around reported 0 while two real
listings were live). Also only ever counts rows the Trading API path
itself wrote, same as /settings and /ebay-listings, tested directly.

Asked to check other pages for wrong information, so before treating that
as done, here's what was actually checked rather than assumed clean:

- **Hardcoded environment strings.** Grepped for "Sandbox" outside its
  legitimate uses and found one real bug: `adapters/ebay.py`'s error
  messages said "eBay Sandbox API request failed" / "sandbox access
  token is not configured" **unconditionally**, regardless of which
  environment actually failed -- the same class of bug
  `adapters/ebay_trading.py` was built to avoid from the start. A
  production failure read as a sandbox problem. Fixed to name the real
  environment; verified both directions with a test.
- **Pluralisation.** Every `{'s' if ... != 1 else ''}` branch in the
  codebase (6 of them) checked by hand -- all correct.
- **Subject-verb agreement**, the bug class from two commits ago ("1
  background job need attention", "1 held card have no eBay signal").
  Swept every page-facing f-string with a count next to a bare verb;
  found nothing further.
- **The "empty cache vs never checked" bug** -- the shape of the eBay
  gap itself. Checked every other cache-backed page (Lens results, foil
  review, photo audit): all already distinguish "ran and found nothing"
  from "hasn't run" correctly, most by returning nothing at all rather
  than a false zero.

Not claimed: a line-by-line proofread of every page's copy. This was a
grep-driven sweep for specific, previously-confirmed bug shapes, not an
exhaustive review -- flagged here plainly rather than overclaimed.

- 4 new tests (2 dashboard widget, 2 environment-aware error messages).
  Full suite (1409) green.

## 2026-09-15 — A page that can actually see a relisted item

Nic relisted two items through Seller Hub's Relist button and asked
whether this app could see them. It couldn't, and now it can: `/ebay-listings`,
built around `adapters/ebay_trading.py`'s GetMyeBaySelling wrapper --
the Trading API call Seller Hub itself is built on.

The prior route (`/api/ebay/listings`, used by nothing in the UI) calls
the Sell Inventory API, which only ever sees listings created *through*
the Inventory API. A listing made any other way -- by hand, Relist, bulk
File Exchange -- is permanently invisible to it, confirmed directly on
this account: Sell API reported 0 while GetMyeBaySelling found 2, real,
priced, live listings.

Trading API predates OAuth and is XML-over-HTTP, but takes the same
Sell-scope access token as a bridge header (`X-EBAY-API-IAF-TOKEN`) --
nothing new to configure. And its failures don't show up as an HTTP
status: it returns 200 on both success and failure, with the real result
in `<Ack>`, confirmed live by deliberately sending an invalid site id.

- `ActiveList` is omitted from the response entirely, not returned empty,
  when there's nothing in it -- checked `Ack` before treating an absent
  section as "nothing to see" rather than "eBay declined to answer".
- `SoldList`/`UnsoldList` are explicitly excluded from the request. Left
  to defaults, GetMyeBaySelling handed back real buyer order details (a
  live probe surfaced a genuine buyer email/address/postcode) that this
  call has no reason to fetch or store.
- Writes into the existing `EbayListing` table -- documented as a
  wholesale mirror of "whatever eBay reports" -- tagged
  `raw_snapshot.source = "trading_api"` so a future Sell API sync of the
  same item id can't silently blend with this one.
- Cached, not live per page load: a human clicking Refresh is currently
  the only thing that calls eBay here, and the page says how stale that
  is rather than a fresh number.
- `/settings`' eBay section links to it, showing "not checked yet" as
  WARN rather than a false "0" -- 0 is a specific claim about eBay this
  app cannot make without visiting the page at least once, which is
  exactly the mistake the old Sell-API-only view was making silently.

- 21 new tests: pure XML parsing against real response shapes, pagination,
  the Ack=Failure/HTTP-200 case, and route-level tests confirming a Sell
  API row and a Trading API row in the same table never mix. Full suite
  (1405) green.

## 2026-09-15 — Long-lived, sliding sessions

Sessions had a hard 8-hour ceiling with no renewal, so a full workday of
steady use ended in a mid-task logout with no way back short of typing the
password again. This app is Tailscale-only and single-user -- the real
perimeter is the tailnet, not this cookie -- so a long session costs
nothing a public-facing login couldn't accept.

Sliding rather than a longer fixed expiry: a session's clock resets on any
use past 24 hours old, up to a 30-day ceiling from the last time it was
used at all. Staying logged in now costs nothing more than opening the app
at least once a month; a flat 30-day expiry from login would still force a
re-login on exactly the daily use this exists to stop interrupting.

First version of the fix did nothing, silently. `require_dashboard_user`
tried to set the renewed cookie on FastAPI's dependency-injected
`Response` -- which only reaches the browser when the route *doesn't*
return its own Response object. Every route here does
(`return HTMLResponse(...)`, `return RedirectResponse(...)`), so the
cookie was set on an object nothing ever sent. Caught by actually checking
for a Set-Cookie header on a live request rather than trusting the code
read correctly.

Fixed with middleware: the dependency marks a session for renewal in
`request.state`, and `renew_session_middleware` writes the cookie onto
whatever Response the route actually returned, regardless of which
subclass. Verified against both HTMLResponse and RedirectResponse routes,
since that distinction is exactly what broke the first attempt.

- No dedicated auth test file existed before this -- every other test
  overrode `require_dashboard_user` rather than exercising it, so even the
  original 8-hour cutoff had never actually been run by the suite.
- 14 new tests, including the renewed cookie itself verifying for a fresh
  window rather than just being present. Full suite (1384) green.

## 2026-09-15 — Confirming there are no eBay listings, and fixing the route that couldn't check

Nic asked where to see current eBay listings. `/api/ebay/listings` calls
the real eBay API with the connected credential, so it should have been
the answer -- instead it 400'd every time, in any environment: it called
`GET /sell/inventory/v1/offer` with no `sku`, and eBay's Inventory API has
no "list every offer" endpoint. `getOffers` is per-SKU; a missing `sku` is
read as an empty one and rejected (errorId 25707, "invalid value for a
SKU"). Nothing in the UI calls this route yet, which is the only reason it
had never been noticed.

Fixed by walking the SKUs the inventory-items page actually returned and
fetching each one's offers individually. Confirmed live end to end against
eBay production with the account's own credential once the fix was in:
**0 listings** -- the honest answer to the original question, now backed
by eBay's own API rather than an empty local table.

- 2 new tests against a fake client shaped like eBay's real responses,
  including the 400-with-no-sku case the bug produced. Full suite (1370)
  green.

## 2026-09-14 — A settings page, so "is eBay hooked up?" has an answer

`/settings`, in the Today group of the nav. Everything on it was already
knowable and scattered: the eBay connection lived only inside a 503 you
had to trigger, the API keys only in `.env.local`, the background jobs
only on the dashboard.

Four sections: **eBay** (environment, connection, token expiry,
marketplace, and whether the listing prerequisites are met), **Integrations**,
**Background jobs**, and **Storage**.

**No credential is ever rendered** -- not even masked. A truncated key
still leaks its length and prefix, and nothing this page answers needs the
value, so each one reports only "configured" or "not configured" plus what
stops working without it ("reverse image search is unavailable"). A test
plants a known secret in five settings and asserts neither it nor its
first or last eight characters appear anywhere in the HTML.

The eBay section calls out the environment mismatch specifically, because
that failure is otherwise silent: a sandbox token against production comes
back as an ordinary auth error, so nothing tells you *which* eBay you are
attached to. "Connected" and "able to publish" are also separated -- a
missing returns policy names itself rather than hiding inside a generic
not-ready state.

Deliberately read-only apart from the Connect button. Values come from
`.env.local`, which systemd's timers read too, so anything edited in the
browser would disagree with what the nightly jobs use.

- 8 new tests. Full suite (1368) green.

## 2026-09-14 — Why there are no eBay stats to show

Asked for eBay figures on the dashboard, and there are none to put there.
Every eBay table is empty: `ebay_listings` 0, `ebay_listing_links` 0,
`ebay_imported_orders` 0, `sales` 0, `shop_orders` 0. The 997
`shop_listings` rows are the NL Vault storefront, not eBay. Nothing has
ever been published to eBay, so there are no orders, so there are no
sales.

Underneath that, a bug that would have stopped it working even once the
listings existed. `valid_user_access_token` fetched `EbayCredential` id=1
with **no environment check**, and the refresh path called
`store_user_tokens` without passing one -- so it fell back to the
`"sandbox"` default and silently rewrote the stored environment on every
refresh. A production connection downgraded itself to sandbox and then
handed a sandbox user token to the production API, which fails as a
generic auth error. That reads as "my credentials broke" rather than "I am
connected to the wrong eBay", and it is the state this box was in:
`EBAY_ENV=production` with a stored `sandbox` credential.

- The refresh now carries the record's own environment through.
- A mismatch is refused by name -- "connected to sandbox but configured
  for production, reconnect at /auth/ebay/start" -- rather than failing at
  the API.
- "Not connected" names the environment it wanted instead of always
  saying Sandbox.
- 3 new tests. Full suite (1360) green.

The dashboard work is not blocked on code. It is blocked on there being a
single eBay listing, which needs reconnecting in production and either a
successful File Exchange upload or the publish path being run.

## 2026-09-14 — Making a dead job visible from the dashboard

The price backfill died on 8 September and nobody noticed for six days.
The traceback was written to `scan_ingest_data/logs/` **once a day, every
day**. Nothing read it. Every figure on the dashboard kept rendering a
value the whole time -- just a staler one each morning.

`job_health.py` reads the state of the four background jobs and the
dashboard shows it above the widget grid. Healthy collapses to one line;
only trouble gets room.

Two states, and only one of them is "failed":

- **failed** -- it ran and exited non-zero.
- **overdue** -- the timer's next firing is in the past, or there is no
  next firing at all. A timer that has been stopped, masked or never
  enabled looks perfect on every other measure, because nothing failed;
  nothing happened. That is the shape of outage most worth catching and
  the one a plain "did it error?" check misses entirely.

`Result` alone is not enough, which this box proved on the spot:
`systemctl reset-failed` had cleared it to "success" while
`ExecMainStatus` still held the 1 from the previous day's failure. Reading
Result would have put a green tick over it. Both are checked, and the
detail line deliberately omits Result when the exit code disagrees --
"exited 1 (success)" reads as a bug in the panel rather than a broken job.

Each entry says what the failure costs in the reader's terms, not the
unit's: "held cards stop getting a market value", not "oneshot exited 1".

- Degrades to "unknown" rather than raising if systemd is absent, the unit
  is renamed, or the query hangs. A status panel must never be the thing
  that takes the page down.
- Costs the dashboard ~130ms (1,445 -> 1,578) for eight `systemctl show`
  calls. The dashboard was already the slowest page in the app; batching
  the queries is the obvious fix if it starts to matter.
- 18 new tests, every one a case the real outage hit or would have slipped
  past. Full suite (1357) green.

## 2026-09-13 — 473 copies of stock that nothing could see

Running the new eBay timer to prove it worked gave the wrong answer: 1,530
cards priced, and the count of held cards with no eBay signal stayed at
exactly 163. Ordering never-priced first should have made that impossible.

They were not being skipped. They were invisible. `InventoryItem` carries
both `card_id` (older, a CatalogCard) and `catalog_item_id` (the CatalogItem
everything else keys on), and **185 rows had only the old one**. Both
`import_ebay_prices` and `list_for_sale` filter on
`catalog_item_id IS NOT NULL`, so those rows -- **473 copies** -- could
never be priced and could never be listed. No number of runs would have
reached them.

`scripts/backfill_inventory_catalog_item_id.py` fills the newer column in.
Safe because the two are identical wherever both are set: zero rows
disagree and all 185 orphans resolve to a real CatalogItem. The script
re-checks both at run time and refuses rather than guessing if either stops
holding.

Result, after backfilling and re-pricing:

- held cards with no eBay signal: **163 -> 0** (100% of 1,343)
- `ebay_active_low` rows: 4,447 -> 6,198
- listable inventory rows: 1,784

Worth naming the shape of this. The audit started from "no button to check
prices"; the button was the smallest part. Under it sat a timer that had
been dead six days, a `--limit` that re-priced the same arbitrary slice
forever, and 473 copies of stock that two separate subsystems could not
see. None of it was visible from the page that prompted the question.

- 1 new test, on the invisibility itself. Full suite (1316) green.

## 2026-09-13 — eBay prices get a timer, and `--limit` starts meaning something

`ebay-price-import.timer` now runs daily at 03:17 (+ up to 5 min jitter),
early enough that the prices every listing decision is built from are fresh
*before* the day's listing work. Units vendored into `docs/systemd/`
alongside the wrapper script, so what's installed on the box is in the repo.

Writing it exposed a worse bug than the missing timer. `_targets` applied
`.limit()` with **no ordering at all**, so a limited run re-priced whatever
the database happened to return first -- every time. With 1,343 held cards
and a default limit of 200, the same arbitrary 200 were refreshed forever
and the remaining 1,143 were never reached. That also made yesterday's new
"Refresh eBay prices" button a 200-card no-op.

Targets are now ordered never-priced first, then stalest first, so any
limit sweeps the whole set instead of starving most of it. The timer still
passes `--limit 2000` to cover everything in one pass with headroom.

Also worth correcting: the claim that the failing pokemonpricetracker unit
"logged no stdout" was wrong. It logs to
`scan_ingest_data/logs/pokemonpricetracker-backfill.log`, and that file had
recorded the same TypeError **six times, once per day**. The failure was
never invisible -- nothing was reading it. A failed-timer indicator in the
UI would have caught this on day one and is not built.

- 2 new tests, on the ordering and on a limit taking the stalest rather
  than an arbitrary slice. Full suite (1315) green.

## 2026-09-13 — eBay prices get a button too, and they are the ones that matter

`import_ebay_prices` had no timer and no trigger of any kind. The last run
was 11 September, by hand. That is the more expensive of the two gaps:
`list_for_sale` builds every listing price from `ebay_active_low` as its
conservative figure, so a stale eBay signal moves real money in a way a
missing pokemonpricetracker row does not.

163 of 1,343 held cards have no eBay signal at all. A new **eBay market
signal** panel says so and runs the import.

The two sources have opposite shapes, so they get separate panels rather
than one "refresh prices" button:

- pokemonpricetracker is 100 credits/day at 3 a search -- about 25 cards,
  so its panel reports a backlog it chips away at.
- eBay Browse is ~5,000 calls/day at one per card, so the whole held set
  fits in a single run. This is the one that genuinely can be
  "everything".

Confirmed working against production on three cards before wiring it up,
and it prices collectibles as well as cards -- Sonny Angel Polar Bear came
back at a $25.32 low against a $36.05 median.

- Still no timer for it. The button is currently the only thing that runs
  it; a daily unit alongside pokemonpricetracker-backfill.timer is the
  obvious next step and needs a root change.
- 3 new tests. Full suite (1313) green.

## 2026-09-13 — The nightly price backfill had been dead for six days

The audit's headline complaint was "no button to check prices for
everything". The button turned out to be the smaller half of it: there is
already a systemd timer that prices held inventory daily, and it had
failed at noon every day since **8 September**.

    TypeError: '<' not supported between instances of 'str' and 'NoneType'

`backfill_pokemonpricetracker_prices` selected every inventory row's
card_id, and collectibles carry `card_id = NULL` -- they are filed by
catalog_item_id. A None key reached `sorted()` and killed the run. 8
September is the day the first collectibles were committed. Six days of
failure, invisible because the unit logs no stdout and nothing surfaces a
failed timer.

Filtered at the query. Today's run recovered on demand: 25 cards searched,
23 priced, 3 with no upstream match.

Also on the price page, an **Inventory prices** panel with the backlog
(1,193 held card/variant pairs) and a *Price the next 25* button. It runs
the same script the timer runs rather than a second implementation -- a
job that silently died for six days is not an argument for having two of
them -- and it records an audit event on crash. Deliberately not
"re-price everything": the free tier is 100 credits a day at 3 a search,
so everything is about 48 days of runs. The panel says how far through you
are instead of implying one click finishes it.

Smaller, from the same audit:

- **Shop listings** shows a count on every filter tab. The page opens on
  Drafts by design -- it exists because nothing else showed a DRAFT row --
  but with one draft and 995 active listings it greeted you with
  "1 listing(s)". Counts fix that without moving the review step.
- **Adding a collectible by hand** defaults to quantity 1, matching the
  review form. At 0 no InventoryItem is created and `list_for_sale` only
  sees quantity > 0, so the old default produced a catalogue entry that
  could never be listed. Nothing needed rescuing -- the 22 items in that
  state turned out to be seeded blind-box rows, which are products to
  price, not figures owned.
- **Kappa** is no longer flagged as a secret.
- **Pink Robby / Flower Robby** gain "... Robby Angel" aliases, the form
  the brand's own Summer Vacation release uses.
- 7 new tests. Full suite (1310) green.

## 2026-09-13 — A retry and a decision are different jobs

Twelve cards had sat unpriced since 15 August. Ten of them were behind
nothing worse than `pokemontcg.io returned 500` -- an upstream outage that
afternoon -- and the page gave no way to tell those apart from the two that
genuinely cannot be matched. One list, one button, same red pill on every
row, so the whole thing read as work nobody wanted to start.

"Not yet priced" is now two panels:

- **Ready to price** -- never checked, or failed on something a retry can
  fix. The button reads "Retry 10 lookups" and names the count.
- **Needs your decision** -- an ambiguous match (two candidates at different
  prices) or no match after every search variant. The error shows here,
  where it is the thing you act on, rather than on every row.

A price run now skips the blocked ones: re-asking about an ambiguous card
returns the same ambiguity and spends a rate-limited call doing it. "Try
these anyway" is the escape hatch, because the classifier reads error
strings and a misread card would otherwise never be retried by anything
again. An unrecognised error counts as retryable -- guessing "transient"
costs one API call, guessing "permanent" strands a card in a list nobody
revisits, which is the failure being fixed.

- Fixed a test-isolation bug found on the way: the price-check run-lock is
  module state, and a test leaving a run "in progress" made every later
  test's price check a silent no-op. Reset around each test now.
- 9 new tests, including the real error strings off all twelve cards.
  Full suite (1305) green.

## 2026-09-13 — Stock art on audit-flagged photos, journalled

113 inventory items now show pokemontcg.io reference art instead of their
own photo, via `scripts/photo_audit_use_stock.py`. Own photos across the
catalogue go 1,273 -> 1,160.

The audit flags 135. Two exclusions bring that to 113, and both matter:
the 20 items already dismissed in `/inventory-audit` (a dismissal is a
human saying "I looked, this is fine", and re-clearing it throws that
verdict away), and 2 whose closest match *is* the card they're filed under
-- a badly-hashing photo of the right card, not a mismatch.

Nothing is deleted. Every cleared path goes to a journal under `backups/`
first -- before the rows change, since a journal lost to a crash is a swap
nobody can undo -- and `--revert` restores from it. All 113 photo files are
still on disk. Revert skips any item whose photo was re-added since, which
would otherwise silently undo a real fix.

**This hides a symptom rather than fixing one.** A flagged item is either a
good card whose photo hashes badly, or a genuinely misfiled card. In the
second case stock art makes the listing *more* wrong: the buyer sees a
confident render of a card that may not be in the envelope, at that card's
price -- the exact failure audit_inventory_photos.py exists to catch, and
why it is read-only by design. It also clears the photo from the review
page, which is the evidence for deciding what the card really is.

Of the 113, 47 sit at own_distance under 20 against a threshold of 12 --
close enough to the line that the audit's own docstring puts false
positives there (foils, crops catching the neighbour). Those are the ones
most likely worth reverting and reviewing by hand.

- 7 new tests, on what the swap excludes and what revert refuses to
  clobber. Full suite (1286) green.

## 2026-09-13 — The eBay upload that failed all 1,014 rows

An upload came back "1,014 failed, 0 completed" -- every row, none
completed, which is the signature of a bad *file* rather than bad
listings. Two structural differences from eBay's own template, both in
`convert_to_ebay_template.py`:

- **The Info row was never written.** `read_ebay_header` reads the
  template's row 1 (`Info,Version=1.0.0,Template=...`) purely to find row
  2, then discards it. File Exchange uses that row to identify which
  template and version a file was built from. The script's own docstring
  documents the row; the writer just never emitted it.
- **No BOM.** eBay's template carries one, the output didn't, and the data
  is not ASCII ("Pokemon" with an accent, throughout). The Action column
  declares `CC=UTF-8` while the file gave the parser nothing to confirm it
  with.

Regenerated: 1,014 listings byte-identical to the failed upload, now with
the Info row and a BOM ahead of them.

- A new test drives the script end to end and asserts both, with its fake
  template built from the converter's own output columns so a new field
  can't leave it asserting against a stale header.
- Full suite (1272) green.

Unconfirmed: eBay's own per-row error report ("Download results") hasn't
been read yet, so this is two defects certainly fixed, not two defects
proven to be the whole cause.

## 2026-09-12 — Candidates you can look at, not just read

The suggestion panel listed names. "Milk Biscuit 4, Milk 3" is not a
question two words can settle -- it needed a picture, which is exactly the
pair that had already been called wrong once.

Each candidate now shows a photo, taken from the highest-ranked Lens match
that named it. Lens returns a thumbnail per match and they were already
cached, so this costs no extra fetching and no new runs: the pictures were
sitting in files the page was reading anyway. Laid out as cards beside the
capture photo, so confirming is a glance rather than a name lookup.

- Every candidate across all five pending captures resolved to a picture.
  A match without a thumbnail falls back to a placeholder rather than
  dropping the candidate.
- Clicking still only fills the form. Nothing about this writes.
- Review page with 6 candidates and 30 results comes to 223 KB.
- 2 new tests. Full suite (1271) green.

## 2026-09-12 — Flower Series, and the weak capture answers itself

Nic confirmed capture fd0da650 is a Flower Series figure, so its line-up
went in: 12 regular and 3 secrets. Two fetched sources agreed on the
twelve; an earlier search summary had claimed a Dandelion that neither
page lists, which is why nothing lands here without a second source.

The payoff is that no new Lens run was needed. Re-reading the **same
cached matches** for that capture, the suggestion goes from a weak
"Flower Series 5" to **Lily Bell, 8** -- the evidence was always in the
titles, and the vocabulary was the only thing missing.

- Two Flower Series figures share a name with a limited series of their
  own: Cactus, and Cherry Blossom. The cross-series guard already handles
  it -- "Cactus Series 2020" resolves to the series, "Flower Series Cactus"
  to the figure.
- Retailers split on "Cherry Blossom" vs "Cherry Blossoms", and matching
  is exact on word boundaries, so the plural is aliased.
- Three earlier tests asserted that a Sonny Angel title could only ever
  resolve to a series. That was a limitation, not a rule, and it no longer
  holds -- updated rather than worked around.
- 6 new tests. Full suite (1269) green.

## 2026-09-12 — Sonny Angel figures, and a guard for partial figure lists

Summer Vacation's line-up went in from a retailer page, cross-checked
against a second source and Sonny Angel's own release post first: 8 types,
6 regular and 2 secret (Unicorn, Flamingo Robby Angel). The sheet calls
the 2026 release "SUMMER VACATION" and everyone selling it says
"Sun-Kissed Style", which is how the catalogue acquired
`Summer Vacation Series - Sun-Kissed Style` -- aliased.

Adding figures for one series of many exposed a real hazard in the
character-beats-series rule. Sonny Angel figure names are generic and
recur across series, so "Animal Series 4 Rabbit" matched the single Rabbit
we happen to know and reported **Summer Vacation** -- a confidently wrong
series, on a field that becomes an eBay query.

A character now only wins when the title doesn't name a *different*
series. Reporting just the series a title actually named beats reporting a
figure from one it didn't. Pop Mart is unaffected: its figure sets are
complete per series, so the character and the series always agree.

- A figure filed under a series the master list doesn't have now fails at
  load rather than matching and reporting an unpickable series.
- 6 new tests. Full suite (1266) green.

## 2026-09-12 — The vocabulary goes multi-brand

Pop Mart arrived, and with it the problem that makes brand scoping
mandatory rather than tidy: **Sonny Angel's Snack Series has a Mustard,
and so does Pop Mart's Twinkle Twinkle Create Your Taste.** Matching a
Lens title on that name without knowing the brand is a coin toss between
two companies' figures, and the loser gets priced against the wrong one.

`sonny_angel_series.py` is now `vocabulary.py`, keyed by brand. The two
brands need different shapes, so the loaders are separate and only the
output is common:

- **Sonny Angel** stays series-level — nobody sells one random Animal
  Series 4 figure — and still parses the raw spreadsheet export verbatim,
  so refreshing it is re-exporting the sheet.
- **Pop Mart** is figure-level, because a set page names all twelve and
  marks its chases. Twinkle Twinkle ships with 9 regular figures and its
  three secrets (Lemon, Milk, Chili Sauce) flagged.

`match()` and `suggest()` take a brand, and the review page passes the one
on the capture row. Its datalists are scoped the same way, so a Pop Mart
capture is never offered Sonny Angel's series.

- A character beats the series it belongs to, regardless of name length.
  Naming the figure tells you its series as well, so it's strictly the
  better answer — matching "Twinkle Twinkle Chili Sauce" to the set rather
  than the figure threw away the half that mattered, including its secret
  flag.
- A matched chase figure now ticks the Secret box, and a matched character
  fills the Character field, alongside the series that was already there.
  Still click-to-fill in the browser: the Lens route writes nothing to the
  capture, and a test holds that line.
- Aliases are brand-scoped too, so Sonny Angel's `Fast Food` can't resolve
  for Pop Mart.
- CRYBABY followed, once two live captures came back with no consensus
  for exactly one reason: Lens had named them correctly both times and the
  app had no vocabulary to read it with. Crying Again (12 + 3 secrets) and
  Crying For Love (12 + 2) are in, read off Pop Mart's own product pages.
  CRYBABY turns out to be the character line, not the series -- the series
  sits under it -- so listings that drop the prefix are aliased.
- A variant beats the figure it varies: "The Robber Red Ver." is a secret
  and "The Robber" is not, and they price accordingly.
- 17 new tests. Full suite (1260) green.

Not done, and worth knowing: `scripts/import_sonny_angel_series.py` still
carries its own hardcoded list of 11 series, which the vocabulary now
contradicts (it has 65, and the seed's two hipper names are wrong). Left
alone deliberately — pointing it at the vocabulary changes what it seeds,
which needs the per-series secret data first.

## 2026-09-12 — Google Lens was working; the app threw its results away

Reverse image search on a collectible capture reported "Google Lens found
nothing usable for this photo" on every run. The runs were all succeeding:
one real Sonny Angel search had matched the figure correctly and returned
56 results, top of list a Flower Series listing naming the exact figure.

The actor writes **one dataset item per image searched**, not one per
match, with the matches nested under the key named by that item's own
`searchType`:

    {"searchType": "visual-match",
     "visual-match": {"inputUrl": "base64:0", "results": [{...}, ...]}}

Both call sites read a dataset item as if it were a match, so every
`title`/`link`/`thumbnail` came back `None`, the link-less row was filtered
out, and the page reported nothing found. Flattening now happens once in
`reverse_image_search.py`, which both callers share.

- Every existing test mocked `search_image` with a flat
  `[{"title": ..., "link": ...}]` — a shape the actor never returns. The
  mock encoded the wrong assumption, so the suite stayed green while the
  feature was dead. New tests assert against a dataset item copied
  verbatim from a real run.
- `source` and `price` were being dropped on the floor; the review row now
  shows them, because "ebay.com · $24.00" is most of what picking the
  right match depends on.
- Results are capped at 30. Thumbnails arrive as inline base64 of 4–15 KB
  each, so 56 matches was ~500 KB of page and cache per capture, and past
  the first couple of dozen they're reposts and lookalikes.
- Only `http(s)` links survive. These titles and links are scraped from
  third-party pages and go straight into an `href`.

## 2026-09-12 — The series field gets a vocabulary

`series` was free text, and the catalogue had drifted accordingly: 16 of
47 Sonny Angel rows named a series that doesn't exist. `Animal Series`
seven times over, with four numbered ones to choose from and no way back
to which. `Animals Series 2`. `Hippers Animal Series`, for a line actually
called `Animal Hippers`. The eBay pricer builds its query from this field,
so each of those was quietly pricing a figure against the wrong
comparables.

Nic's master list now ships as `automation_control/data/` — parsed, not
retyped, so refreshing it is re-exporting the sheet. 65 series against the
11 the seed script knew: 10 regular (it was missing Snack Series), 46
limited (it had none), 6 hippers, 3 gifts.

- The review form offers them as a `<datalist>`, never a `<select>` —
  Sonny Angel ships several series a year, so one the sheet hasn't caught
  up with has to stay typeable.
- A series also isn't only sold under its sheet name. The catalogue's
  `Fast Food Series` rows turned out to be Snack Series figures (confirmed
  by Nic, not inferred), and that's the name sellers use — so matching
  canonical names alone missed every title for the Hamburger secret, the
  most valuable figure in the catalogue. Aliases resolve to the canonical
  name, and never become the stored one.
- Lens matches are read for a series and ranked by how many agree, with
  the tally shown. On the real Flower Series capture that's 6 against 1,
  1, 1. Consensus is the signal; one listing naming a series is a
  coincidence, and the page says which it's looking at.
- Clicking a suggestion fills the form field in the browser. It stays a
  suggestion — the Lens route still writes nothing to the capture row, and
  a test holds that line.
- `match_series` refuses to guess. A title saying only "Animal Series"
  matches nothing, because a wrong series gets priced whereas a blank one
  gets typed.
- Matching is word-order sensitive, and checking the live catalogue caught
  what that costs: this app's own `Hippers Animal Series` resolved to the
  bare `Hippers` line rather than `Animal Hippers`, which prices
  differently. Aliased, with the rest.
- Of the 16 drifted catalogue rows, 9 now resolve automatically. The other
  8 can't be recovered from the row at all — `Animal Series` seven times
  with four numbered ones to choose from, and one `Animals Series 2`.
- 27 new tests. Full suite (1243) green.

## 2026-09-08 — Navigation drawer on the admin app too

The shop got a drawer; the admin didn't have one, and it needed it more.
Only `/dashboard` listed every module, so from any other page reaching a
different one meant going back to the dashboard and out again. Now every
authenticated page carries the same slide-out drawer with all four groups
(card capture, bulk scan, buying & selling, business), matching the
storefront's interaction: hamburger toggle, scrim or Escape to close,
`aria-expanded` kept in sync.

- The drawer's link to the storefront is resolved client-side from the
  host in use (`location.hostname + ':8444'`) rather than hardcoded, so it
  works over Tailscale and locally without another setting to maintain.
- `/login` deliberately renders no drawer -- there's nothing to navigate
  to before signing in.
- Also added a shop link to the dashboard's Business group, after landing
  on the admin app and concluding the shop wasn't there.
- 3 new tests. One of them caught the same false-positive trap as the
  earlier topbar work: asserting a page lacks "navtoggle" fails even when
  the button is absent, because the always-included stylesheet defines
  `.navtoggle`. The assertions check rendered elements instead.
  Full suite (730) green.

## 2026-09-08 — Storefront: side navigation and checkout

- **Hideable side navigation.** A slide-out drawer rather than a permanent
  column: on a phone a fixed sidebar eats half the screen, and on desktop
  the product grid is what people came for. Toggle in the header, closes
  on scrim click or Escape, `aria-expanded` kept in sync. Its "Bulk by
  set" section is built from live stock, so the shop never advertises a
  category that lands on an empty page.
- **Checkout**, via Stripe Checkout (the hosted page) so no card details
  ever touch this server -- the shopper pays on Stripe and returns with a
  session id to reconcile. Same gating as the eBay integration: with no
  `STRIPE_SECRET_KEY` set, the button says payments aren't live and the
  order is still written, rather than half-failing at the till.
- **Stock is decremented only on confirmed payment**, never at add-to-cart,
  so an abandoned basket can't quietly take things off sale. Selling the
  last one flips the listing to SOLD_OUT. Amounts sent to Stripe are
  recomputed in cents from the order row, never from anything the browser
  supplied.
- Extracted `storefront/common.py`: `app.py` registers checkout's routes
  while checkout needs app's cart and shipping maths, which is a cycle if
  the helpers live in either. Caught by the import failing outright, not
  at runtime.
- 12 new checkout tests (including that stock survives an unpaid order,
  that Stripe line items are built in cents, and that a bulk lot can be
  bought); full suite (727) green. Verified live end to end: cart → form →
  order NLV-… written as PENDING_PAYMENT, then removed from the production
  tables afterwards.

## 2026-09-07 — NL Vault storefront (Module 24)

The public shop, plus the "Bulk Master" section.

- **Deliberately a separate application** (`storefront/`, own process on
  :8090) rather than routes on the admin app. The admin app holds
  inventory control, scan management, eBay credentials and the pricing
  engine behind one session cookie; the shop is the one part strangers
  are meant to reach. Two apps means an auth mistake in one cannot expose
  the other — asserted by a test that every admin path 404s on the shop.
- Brand: the **Ligature** mark (concept 02), drawn as inline SVG so it
  stays crisp at any size and inverts by inheriting `currentColor`.
  Ink/brass/bone palette, deliberately not the dashboard's cyan/violet —
  that palette reads as a tool, this one has to read as a shop.
- New tables: `shop_listings`, `shipping_rates`, `shop_orders`,
  `shop_order_items`, `bulk_lots`. Listing is an explicit act
  (`scripts/list_for_sale.py`) — nothing is ever put on sale as a side
  effect of scanning, and inventory is never mutated by listing.
- **Bulk Master**: the value distribution made the case — 951 of ~980
  owned cards are worth under $2 each, which cannot ship profitably as
  singles against $9.95 postage. `scripts/build_bulk_lots.py` groups
  those into per-set lots priced at a percentage of combined market value
  with a postage floor, and never sweeps in a card already listed
  individually. 24 lots covering 1,328 cards. Filters on the page:
  search, sort, minimum card count, maximum price.
- Cart is a cookie, not a table — an abandoned cart shouldn't leave rows
  behind, and there are no accounts. Cart keys are typed (`B:` prefix for
  a bulk lot) so one cart holds both singles and lots. The cookie is a
  wish and the database is the truth: anything withdrawn or sold since it
  was added silently drops out, and a tampered cookie is treated as empty
  rather than an error.
- Shipping: AU flat rate with a free-over threshold, plus an
  international rate, as rows rather than constants so a postage rise
  isn't a code change.
- 17 new tests; full suite (715) green. Served over Tailscale at :8444
  alongside the admin app, both still tailnet-only.

## 2026-09-07 — eBay prices now surface in inventory valuation

Spotted while answering "does this reflect on the Pokémon cards I have
scanned?": the eBay prices were being written but never displayed. The
inventory and dashboard look prices up by `(card_id, variant)`, while eBay
rows are deliberately stored with `variant=None` (an eBay listing usually
doesn't say whether it's a reverse holo), so the lookup never matched them
and kept showing the older pokemonpricetracker figure.

- New `_latest_ebay_prices()` keyed by card alone rather than
  (card, variant), using `ebay_active_low` — the conservative figure —
  rather than the median, which skews high on active listings and would
  overstate holdings.
- `/inventory/set/{id}` shows the eBay figure as a separate "eBay low"
  line beside the variant-accurate price, and falls back to it (clearly
  badged) when no variant-specific price exists. A real variant price is
  never overridden.
- The same fallback applied to set totals and the dashboard's inventory
  value, so the three can't disagree about what a holding is worth.
- 4 new tests covering both behaviours plus the two things that must not
  happen: eBay overriding a variant price, and the median being used for
  valuation. Full suite (698) green; verified against real production
  data after restart.

## 2026-09-07 — eBay switched to production, and real prices are flowing

The production keyset was returning `invalid_client`. Diagnosed it down
to eBay's side rather than ours: an identical token request succeeded with
sandbox credentials and failed with production ones, ruling out our code
and the credential format. The actual cause was eBay's **Marketplace
Account Deletion** compliance requirement — the keyset was disabled until
either a deletion-notification webhook was implemented or an exemption was
granted. Took the exemption route (accurate here: this app persists no
eBay user data — only its own OAuth token and anonymous price figures),
which avoids exposing this deliberately-private, Tailscale-only server on
a public HTTPS endpoint.

- `EBAY_ENV=production` with production credentials; `start-control-plane.sh`'s
  guard relaxed to accept `sandbox|production` while still rejecting typos.
  Production unlocks **reads only** — the adapter still has no mutation
  methods and `listing_pipeline/publish.py` is still an unimplemented
  placeholder, so no code path can create or edit a listing.
- Cleared the stale sandbox OAuth token row, which would have been
  mislabelled against production.
- **Two real data-quality bugs caught before they could poison anything:**
  every Pokémon row has an empty `set_code` (they use `set_id` +
  `card_sets`), so all 20,479 of them were searching eBay by bare card
  name; and collectibles had no brand in the query, so a Sonny Angel
  called "Hamburger" was being priced against kitchen gadgets. Queries now
  use set code + number (One Piece), set name + number (Pokémon), or
  brand + name (collectibles), plus a relevance filter that discards
  results whose title doesn't mention the item.
- Secret/chase collectibles now include "secret" in the query. On the one
  owned secret figure this moved the median from $45.81 to $198.51 -- the
  naive query had been pricing it against regular versions of the same
  character and understating it roughly 4x.
- 4 new tests (Pokémon set-name queries, collectible brand queries, secret
  variant targeting, relevance filtering). Full suite (694) green;
  service restarted and healthy in production mode; 50 real price rows
  written and verified; DB integrity clean. Backups taken before every
  step.

## 2026-09-07 — Module 23: eBay active-listing prices

Asked to add eBay prices. Checked what's actually possible first: eBay has
no freely-available sold-comp API any more. The Finding API's
`findCompletedItems` was deprecated Jan 2024 and decommissioned Feb 2025,
and Marketplace Insights (the only official sold-data source, 90 days) is
Limited Release requiring business-partner approval a single-operator
seller won't generally get. The Browse API is free and works, but returns
*currently listed* items only.

- New `scripts/import_ebay_prices.py`: one Browse search per card, writes
  two source-tagged `card_prices` rows -- `ebay_active_low` and
  `ebay_active_median`. Named "active" deliberately: asking prices skew
  optimistic and must not be mistakable for sold comps anywhere
  downstream.
- Defaults to pricing only cards actually owned (`inventory_items`) rather
  than the whole 20k+ catalogue, because eBay's default application-token
  quota is ~5,000 Browse calls/day and this makes one call per card.
  `--all`, `--game`, `--limit`, and `--marketplace` are available;
  `--limit` defaults to 200 and `--all` warns past 1,000.
- Reuses the existing read-only `EbaySandboxReadAdapter` and application
  token (not a user OAuth token) -- no new eBay capability, no listing
  writes, and none of the three production guardrails touched.
- Verified end-to-end against the live eBay **sandbox** API: authenticated,
  made real Browse calls, handled zero-result responses without writing a
  fabricated price. Sandbox returns eBay's fake catalogue, so real numbers
  need production credentials -- the script says so on every run while
  `EBAY_ENV=sandbox`.
- Found and documented a real usability gap while testing: `Settings` has
  `env_file=None`, so scripts don't pick up `.env.local` (only
  `start-control-plane.sh` sources it for the service). The script exits
  with a clear message rather than a confusing auth error, and its
  docstring shows the `set -a; . ./.env.local; set +a` line needed first.
- 9 new tests, all mocked -- no real API calls in the suite. Full suite
  (690) green.

## 2026-09-07 — Fixed deal-check's results table on phone screens

Same class of bug as the topbar search box earlier tonight: `.dc-row`'s
5-column grid (`1fr 100px 90px 90px 70px`) had no mobile breakpoint, so on
a narrow phone the fixed-width columns forced the whole row wider than the
viewport -- visible in a real screenshot from testing `/deal-check` live.
Added a `@media (max-width: 640px)` breakpoint that stacks each result
into a small card of label:value lines (via `data-label` attributes +
`::before`) instead of trying to force a table into ~360px. 1 new
regression test; full suite (681) green; `ezbay.service` restarted.

## 2026-09-07 — One Piece pricing, for free, in place of a $49/month API

Asked to look into a "price charting API" and report the cost. Researched
PriceCharting.com specifically before adding anything: API access requires
their $49/month "Legendary" tier (their $6/mo tier doesn't include API
access at all), and -- despite the name -- their own docs say the API
"only supports current item values... historic prices and historic sales
are not supported." Reported that cost and limitation before spending
anything. optcgapi.com, the same free API used minutes earlier for the
One Piece catalogue, already returns `market_price` in the identical
per-card response at no cost, so that's what got wired in instead.

- Extended `scripts/import_one_piece_from_optcgapi.py` (no new script) to
  also write one `card_prices` row per card per run
  (`source="optcgapi_market"`, `currency="USD"`) using the same
  `catalog_item_id` every other One Piece row already uses. The FK on
  `card_prices.card_id` is declared against `catalog_cards` (Pokémon's
  table specifically), but it's not enforced by SQLite, and everything
  that actually reads `card_prices` -- `_latest_prices`, `deal_check.py`'s
  matcher, `market_search.py`'s catalogue cross-reference -- already keys
  off this id-space rather than joining through `catalog_cards`, so this
  works with zero reader-side changes. Append-only like the rest of
  `card_prices`, so re-running the script periodically builds real (if
  coarse) price history for a game that otherwise has none -- for free.
  Added `--no-prices` to keep the catalogue-only path available.
- 3 new tests (correct price recorded and preferring the non-parallel
  printing's value, append-only re-run behavior, `--no-prices` skips
  writing); full suite (680) green.
- Run against production after a manual backup
  (`~/backups/automation-pre-one-piece-prices-20260907-202714.db`): 2,708
  price observations recorded (some cards legitimately appear in more
  than one set's listing, e.g. bundled promos, hence more observations
  than the 2,379 distinct cards). Verified via `fx.to_aud()` directly and
  a live `/search` query against production; `ezbay.service` restarted.

## 2026-09-07 — One Piece catalogue populated from a real API

Asked to "get data to the platform" -- scoped to the One Piece catalogue
first (pricing is a separate, later decision). Researched what's actually
available rather than assume: found optcgapi.com, a free, public,
no-API-key REST API with the full One Piece card pool, confirmed live by
hitting it directly. This is the real API `import_tcg_catalog_csv.py`'s
own docstring said to adopt once one existed, replacing the CSV-only
stopgap for this game.

- New `scripts/import_one_piece_from_optcgapi.py`: pulls every set
  (`/api/allSets/`) and every card in each (`/api/sets/{set_id}/`),
  upserts using the same `_slug(game, set_code, number)` id convention the
  CSV importer already used -- so it updated the 2 example rows from
  Phase 1 instead of duplicating them. Dedupes each card's parallel
  printing (same `card_set_id`, different name/price) down to one
  catalogue row per physical card design.
- Caught a real bug during rehearsal, before touching production: the
  first regex assumed every code looked like "OP01-076" (letters+digits,
  dash, digits) and silently skipped all 22 promo cards, which use a
  bare "P-055" shape (letters, dash, digits, no digits after the letters).
  Fixed by splitting on the *last* dash generically instead of assuming a
  specific shape -- rehearsing against a throwaway DB copy first is what
  caught this before it ever ran for real.
- Rehearsed twice against a throwaway copy of production, then run for
  real after a manual backup
  (`~/backups/automation-pre-one-piece-import-20260907-201151.db`):
  22 sets, 2,377 created, 0 skipped. Verified 2,379 total `one_piece`
  rows across 46 set codes, `PRAGMA foreign_key_check` clean.
- 5 new tests (parallel dedup, idempotent re-import, dry-run, unknown-set
  argument, unparseable-code fallback), all mocking `httpx.Client` -- no
  real network calls in the test suite. Full suite (677) green;
  `ezbay.service` restarted clean.
- Deliberately catalogue-only: optcgapi.com's response also includes
  `market_price`/`inventory_price` per card, but writing pricing data was
  scoped out as a separate decision -- see the next entry.

## 2026-09-07 — Module 22: deal check (read a listing's text, not just a physical card)

Prompted by a real example: a screenshot of a Facebook Marketplace-style
listing description with ten One Piece cards, each with a set/number code
and an asking price ("OP13-076 ... $185 ONO"). Every AI recognition path
built so far reads a *photo of the physical card* (`extract_card_details`)
or a *photo of a collectible* (`extract_collectible_details`) -- nothing
read a seller's own description of what they're offering.

- `card_recognition.py`: new `extract_listing_items()` -- takes a photo of
  a listing, pasted listing text, or both, and extracts every item as
  `{description, set_code, card_number, condition, asking_price}`. Same
  contract as the other `extract_*` functions (empty list means nothing
  found; `RecognitionError` means the response didn't parse).
- New `automation_control/deal_check.py`: `GET /deal-check` (paste text
  and/or upload a screenshot) and `POST /deal-check/extract`. Matches each
  item against `tcg_cards` by `(set_code, card_number)` -- not by name,
  since a listing's free-text title won't reliably match a stored card
  name the way an exact code does -- and reuses `buying.py`'s existing
  traffic-light verdict (GREEN/YELLOW/RED) when a match has real market
  data. Honest when it doesn't: a matched One Piece card (or any non-Pokémon
  game) shows "no price data" rather than a fabricated verdict, since
  `card_prices` has no feed for those games yet -- see DATABASE.md.
- Caught a real bug via the test suite before it shipped: CSV-imported
  card numbers keep their source zero-padding ("062"), but the AI-extracted
  number was being de-padded before matching, so a padded stored number
  never matched. Fixed by trying both forms.
- 12 new tests for the endpoint (matched-priced, matched-unpriced,
  unmatched, missing-code fallback, multi-item, failure handling,
  photo-only submission) plus 4 for `extract_listing_items` itself
  (text-only, photo-only, neither-provided, unparseable response). Full
  suite (672) green; `ezbay.service` restarted; both routes smoke-tested
  (401, correctly gated); production DB confirmed clean.

## 2026-09-07 — Photo uploads now offer the library, not just the camera

Three photo inputs (`/cards/capture`'s front/back photos, `/collectibles/capture`'s
photo, and the quick-price widget on buying/purchase-lots/potential-stock/Whatnot)
had `capture='environment'` set, which forces mobile browsers straight into the
camera and hides the native "choose from library" option entirely. Removed it
from all three -- the bulk-upload forms never had it and already worked
correctly, which is what made this an inconsistency rather than a deliberate
choice. 3 new regression tests (`capture=` must not appear in any of these
pages' HTML); full suite (656) green; `ezbay.service` restarted and all three
routes smoke-tested (401, correctly gated).

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
