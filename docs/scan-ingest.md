# Bulk card scan ingest

Digitises physical trading cards into the `InventoryItem` table by scanning
9-up sheets on the flatbed, splitting them into per-card crops, identifying
each card against a cached set catalogue, and committing the confident ones.
Started as Pokémon-only; as of 2026-09-21 it also covers One Piece, three
English-language Dragon Ball TCGs, Japanese-print Pokémon, and Super Dragon
Ball Heroes — see "Games and catalogue sources" below for what each one is
sourced from and `scan_ingest/games.py` for the registry itself. A pile can
mix any of them; each sheet is routed to whichever game/set it matches, no
reconfiguration between piles.

Code lives in `automation_control/scan_ingest/`. Identification is OCR +
perceptual hashing against locally cached reference art first; a crop
neither can place falls back to Claude reading it directly
(`identify.identify_card_via_vision`, gated on `ANTHROPIC_API_KEY`).
Pricing is per-game: English Pokémon prefers real market/eBay-sold data
from pokemonpricetracker.com when `POKEMONPRICETRACKER_API_KEY` is
configured, falling back to pokemontcg.io's snapshot otherwise; One
Piece/Dragon Ball/SDBH price from a snapshot captured at catalogue-cache
time (no live call); Japanese Pokémon has no price source yet and commits
with price unknown. See `scan_ingest/pricing.py`'s module docstring and
`session.SNAPSHOT_PRICE_SOURCES`.

## Games and catalogue sources

| Game | `TcgCard.game` | Source | Cache command |
|---|---|---|---|
| Pokémon (English) | `pokemon` | pokemontcg.io | `scan_ingest.cli cache-set --set-id <id>` |
| One Piece | `one_piece` | tcgcsv.com (TCGplayer mirror) | `cache_tcgplayer_scan_catalog --game one_piece` |
| Dragon Ball Super: Fusion World | `dragon_ball_fusion_world` | tcgcsv.com | `cache_tcgplayer_scan_catalog --game dragon_ball_fusion_world` |
| Dragon Ball Super (Masters) | `dragon_ball_super` | tcgcsv.com | `cache_tcgplayer_scan_catalog --game dragon_ball_super` |
| Dragon Ball Z (Panini, 2014+) | `dragon_ball_z` | tcgcsv.com | `cache_tcgplayer_scan_catalog --game dragon_ball_z` |
| Pokémon (Japanese-print) | `pokemon_japan` | TCGdex (api.tcgdex.net) | `cache_tcgdex_scan_catalog` |
| Super Dragon Ball Heroes | `super_dragon_ball_heroes` | cardotaku.com's public Shopify catalogue | `cache_cardotaku_scan_catalog` |

Every cache command lives under `scripts/` and is safe to re-run (updates
in place, only downloads images it doesn't already have).

Two of these are worth knowing the limits of:

- **Super Dragon Ball Heroes has no official API.** It's a Japan-only
  arcade/card game never sold through TCGplayer, and the one fan card
  database found (sdbh.dbz.space) sits behind a Cloudflare bot-challenge
  with no API — not something a background service can scrape. The
  catalogue instead comes from one retailer's public storefront listing
  (~3,200 singles as of 2026-09-21, out of many more released over SDBH's
  ~10 years) — real coverage, but a single seller's stock, not an
  exhaustive database. A genuine SDBH card just not in stock there will
  land in the review queue with no match, same as an uncached set for any
  other game.
- **Japanese Pokémon has no price source.** TCGdex carries no pricing;
  every Japanese-print commit records price unknown rather than reusing
  an English-market source that would either match nothing or, worse,
  match the wrong printing (see `pricing.NullPriceSource`).

Unlike Pokémon's "71/114", One Piece/Dragon Ball/SDBH print a
set-prefixed code (`OP01-024`, `FB01-003`, `BM1-005`) that's unique across
the whole game rather than just one set. That lets a mixed pile of one of
these games route to the *whole game's* reference pool at once instead of
narrowing to one set first — see `session._merge_by_game` and
`identify.dominant_set_id`.

## Everyday use

Press the button on the Canon LiDE 300. That's the whole loop:

1. `scanbd` sees the button and runs `/etc/scanbd/scripts/capture.script`
2. that writes a sheet into `scan_ingest_data/watch/`
3. `scan-ingest.service` (always running) picks it up within ~2s, works out
   which set and which rotation, commits what it's confident about, and
   copies anything ambiguous to `scan_ingest_data/media/needs_review/`
4. the original sheet moves to `scan_ingest_data/archive/<session-id>/`

Watch it happen at **`/scan-ingest`** on the dashboard — service state,
per-sheet results, and a tailing log. Inventory is at `/inventory`.

A brand-new set needs no setup: when a sheet matches nothing cached, the
service OCRs the `199/264` printed on the cards, looks up which set has that
printed total, caches it, and retries the sheet.

## When a sheet doesn't go through

The log (and `/scan-ingest`) says which of these happened.

- **"none confidently match any cached set"** — auto-caching couldn't
  identify it either. Usually a scan too poor to OCR *and* too poor to
  match. Rescan it; the file is left in `watch/` untouched.
- **Cards set aside for review** — identified but below the confidence bar,
  or not identified at all. These are never auto-committed by design. The
  crops are named `<sheet>-card<N>-<best guess>.jpg`.
- **"Detection looks off"** — the card count or shapes look wrong. The sheet
  stays in `watch/` for a rescan; an annotated overlay is saved next to the
  crops showing what was found.

To re-run a sheet after fixing the cause (e.g. you cached the right set, or
a detection bug was fixed) without rescanning it physically:

```bash
.venv/bin/python -m automation_control.scan_ingest.cli reprocess \
  --sheet scan_ingest_data/archive/<session>/<sheet>.png --set-id <id> --unattended
```

## Configuration

`scan_ingest_data/session.env` (gitignored; see `session.env.example`).

`SET_ID` is **blank by default**, which means auto-detect set (and game)
and rotation per sheet. Set it only to pin the service to one fixed set,
then `sudo systemctl restart scan-ingest` — and clear it again once
you're done with that pile. A `SET_ID` left pinned from a previous session
is easy to miss (the service keeps running, just silently matching every
sheet against one set) and cost a real session on 2026-09-21: hours of
mixed-game scans went to review as "(unidentified)" because everything
was being compared only against one old Pokémon set. Check
`session.env` first if a session's sheets are unexpectedly all landing in
review.

Paths and the pHash distance threshold live in `automation_control/config.py`
(`scan_*` settings).

## Pricing backfill

`pokemonpricetracker-backfill.timer` runs
`scripts/backfill_pokemonpricetracker_prices.py` once daily (`12:00`
Sydney time — after the API's UTC-midnight credit reset) to price
already-scanned inventory that predates the `POKEMONPRICETRACKER_API_KEY`
switch, or that a run cut short earlier. The free tier is 100 credits/day
and a search costs a fixed 3 regardless of how many of a card's variants
come back priced, so `--max-cards` (default 25) keeps one run comfortably
inside budget; it works through the backlog a bit at a time and settles
into a fast no-op once everything held has a price. Safe to re-run anytime
— it only searches card+variant pairs that don't already have a
`pokemonpricetracker_market` price.

```bash
sudo systemctl status pokemonpricetracker-backfill.timer
sudo journalctl -u pokemonpricetracker-backfill -f
.venv/bin/python -m scripts.backfill_pokemonpricetracker_prices --dry-run  # preview without spending credits
```

## Inventory quality audit

`inventory-audit.timer` runs `scripts/audit_inventory_photos.py` hourly
from 22:00 through 14:00 the next day (read-only — it only reports, never
edits inventory), and appends its findings to
`scan_ingest_data/logs/inventory-audit.log`. Timed to run overnight and
through the working day rather than just at midnight, so a bad scan
surfaces well before the cards get put away. It
flags any item whose stored photo hashes further from the card it's filed
under than from some other cached card -- the real failure this catches is
two different sets sharing a card number (confirmed live: a "151" Slowpoke
and Scyther were both filed under Expedition Base Set, which happens to
have a #79 and #123 of its own).

Treat its output as triage, not a verdict — see the script's own docstring
for known false positives (a foil or an off-centre crop can hash badly
against its own correct reference without being the wrong card). Once a
flagged item is confirmed wrong by eye, `scripts/correct_inventory_item.py`
re-files it, merging into an existing holding if one exists rather than
creating a duplicate:

```bash
sudo systemctl status inventory-audit.timer
sudo journalctl -u inventory-audit -f
.venv/bin/python -m scripts.audit_inventory_photos              # run on demand
.venv/bin/python -m scripts.correct_inventory_item \
    --item-id <id> --correct-set-id sv3pt5 --correct-number 79 \
    --reason "photo shows Slowpoke, not Graveler" --dry-run     # preview, then drop --dry-run
```

## Useful commands

```bash
.venv/bin/python -m automation_control.scan_ingest.cli sets --cached
```

```bash
.venv/bin/python -m automation_control.scan_ingest.cli cache-set --set-id swsh8
```

```bash
sudo journalctl -u scan-ingest -f
```

The pokemontcg.io API fails a large fraction of requests with empty-body
5xx; `cache-set` retries internally, but a whole-set fetch can still fail
outright. Just run it again.

## Things that are easy to get wrong

These each cost real debugging time; they're documented because none are
obvious from the code alone.

- **Piles are not reliably one set.** Many cards carry no visible set
  symbol, so a "sorted" pile can span several sets. Let the per-sheet
  detection decide rather than pinning `SET_ID`.
- **Cards are laid sideways on the bed.** Rotation is auto-detected now, but
  a batch that identifies almost nothing is far more likely a rotation or
  set mismatch than a bad scan.
- **The collector number moved.** XY/SM-era cards print it bottom-right;
  Sword & Shield-era print it bottom-left. Both corners are tried
  (`NUMBER_REGIONS` in `identify.py`) — assuming one silently loses OCR for
  a whole era.
- **pHash beats OCR when they disagree.** Measured on real low-DPI scans,
  OCR produced well-formed but *wrong* numbers; the art match was right.
  See the reconciliation logic in `identify_card`.
- **Band detection alone isn't accurate enough to crop from.** It truncates
  cards whose edges are low-contrast and can't handle tilt. Every band is
  re-measured to the card's true corners and warped flat (`_find_card_quad`
  in `detect.py`) before cropping.
