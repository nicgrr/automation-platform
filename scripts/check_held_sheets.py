"""Report on sheets sitting in the watch folder that the live service has
already given up on -- it only leaves a sheet there when detection found
nothing confident to commit (see scan-ingest.md's "When a sheet doesn't go
through"), so anything here at check time needs a person, not a retry.

This doesn't touch anything. It classifies each held sheet by the same
pixel-variance signal `detect.py` itself uses, so the report says which of
two very different problems it is before anyone opens the image:

  - gapless cards: real column/row content variance, just no gap between
    cards for the grid detector to split on (confirmed cause of several
    held sheets on 2026-09-06 -- recoverable without a rescan via
    `scripts.recover_gapless_sheet`, since the cards are all still in frame)
  - blank/bad capture: variance stays low almost everywhere, meaning the
    scan itself failed (lid not seated, cards shifted mid-scan) -- there's
    no card data to recover, only a physical rescan gets this back

    .venv/bin/python -m scripts.check_held_sheets
"""

import time
from pathlib import Path

import cv2

from automation_control.config import get_settings
from automation_control.scan_ingest.detect import CONTENT_STD_RATIO, MIN_BAND_FRACTION, _content_bands

# A capture in progress writes its file over several seconds; anything newer
# than this might just be mid-scan, not actually stuck.
MIN_AGE_SECONDS = 120

# A sheet full of real card art peaks well above this; a blank/lid-open
# capture stays flat everywhere (confirmed against two real bad captures on
# 2026-09-06: column-std peaks of 18 and 32, against 60-75 for genuine
# card content on the same scanner).
BLANK_STD_CEILING = 35


def _classify(sheet_path: Path) -> str:
    sheet = cv2.imread(str(sheet_path))
    if sheet is None:
        return "unreadable -- possibly still being written"
    gray = cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    std_col = gray.std(axis=0)
    std_row = gray.std(axis=1)
    peak = max(std_col.max(initial=0), std_row.max(initial=0))

    if peak < BLANK_STD_CEILING:
        return f"blank/bad capture (peak variance {peak:.0f}) -- needs a physical rescan, no data to recover"

    col_bands = _content_bands(std_col, int(width * MIN_BAND_FRACTION), ratio=CONTENT_STD_RATIO)
    if len(col_bands) == 1 and (col_bands[0][1] - col_bands[0][0]) > width * 0.8:
        return f"real card content, but columns never separated (peak variance {peak:.0f}) -- likely cards touching with no gap; try scripts.recover_gapless_sheet"

    return f"real card content, columns separated fine (peak variance {peak:.0f}) -- worth a closer look, cause unclear"


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    watch = Path(settings.scan_watch_dir)
    if not watch.exists():
        print(f"watch folder does not exist: {watch}")
        return 0

    now = time.time()
    sheets = sorted(watch.glob("*.png"))
    held = [s for s in sheets if now - s.stat().st_mtime >= MIN_AGE_SECONDS]

    if not held:
        print(f"Nothing held in {watch} (checked {len(sheets)} file(s))." if sheets
              else f"{watch} is empty -- nothing held.")
        return 0

    print(f"{len(held)} held sheet(s) in {watch}:\n")
    for sheet in held:
        age_minutes = (now - sheet.stat().st_mtime) / 60
        print(f"  {sheet.name}  (held {age_minutes:.0f}m)")
        print(f"    {_classify(sheet)}")
    print("\nNothing was changed -- this only reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
