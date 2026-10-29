# Handoff

Read this at the start of a session; update it before stopping. See the
handoff protocol in `AGENTS.md`.

## Current task

Document agent work history on branch
`lens-results-and-collectible-vocabulary` and commit the shared instructions
and handoff. Attribution review is complete; historical commits without an
agent trailer remain unattributed.

## Done

- Recorded eBay orders for listings that have no SKU (14cf2ae)
- Documented the October checkpoint and the public storefront boundary (3e16063)
- Recorded the late-October live eBay operations run (401aa1c)
- Reviewed all 220 existing commits for agent attribution; documented all 41
  without a Claude trailer by theme, with absolute dates and commit references.
- Added shared instructions in `AGENTS.md` and its `CLAUDE.md` include alongside
  this handoff. No tests or services were run for this documentation task.

## Next steps

- Operator to define the remaining lens-results and collectible-vocabulary
  goal; this documentation task does not establish further implementation work.

## Work history by agent

Each agent keeps its own section. Summaries come from commits carrying that
agent's `Co-Authored-By` trailer; `git log -i --grep='Co-Authored-By: Claude'`
lists the commits behind the Claude section.

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

No historical commit can be reliably attributed to Codex. Reviewed all 220
commits across local refs before this handoff commit: every author and committer
is Nic, and no commit message, trailer or author/committer identity mentions
Codex. The 179 commits with a `Co-Authored-By: Claude` trailer are excluded below.

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

## Last updated

Codex, 2026-10-29
