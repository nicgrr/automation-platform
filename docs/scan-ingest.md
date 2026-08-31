# Bulk card scan ingest

Digitises physical Pokémon cards into the `InventoryItem` table by scanning
8-up sheets on the flatbed, splitting them into per-card crops, identifying
each card against a cached set catalogue, and committing the confident ones.

Code lives in `automation_control/scan_ingest/`. Nothing here talks to eBay
or to any AI model — identification is OCR + perceptual hashing against
locally cached reference art.

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
