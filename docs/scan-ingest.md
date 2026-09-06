# Bulk card scan ingest

Digitises physical Pokémon cards into the `InventoryItem` table by scanning
9-up sheets on the flatbed, splitting them into per-card crops, identifying
each card against a cached set catalogue, and committing the confident ones.

Code lives in `automation_control/scan_ingest/`. Identification is OCR +
perceptual hashing against locally cached reference art first; a crop
neither can place falls back to Claude reading it directly
(`identify.identify_card_via_vision`, gated on `ANTHROPIC_API_KEY`).
Pricing prefers real market/eBay-sold data from pokemonpricetracker.com when
`POKEMONPRICETRACKER_API_KEY` is configured, falling back to pokemontcg.io's
snapshot otherwise — see `scan_ingest/pricing.py`'s module docstring.

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

`SET_ID` is **blank by default**, which means auto-detect set and rotation
per sheet. Set it only to pin the service to one fixed set, then
`sudo systemctl restart scan-ingest`.

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

`inventory-audit.timer` runs `scripts/audit_inventory_photos.py` once daily
at midnight (read-only — it only reports, never edits inventory) and
appends its findings to `scan_ingest_data/logs/inventory-audit.log`. It
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
