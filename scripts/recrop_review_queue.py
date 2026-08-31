"""Regenerate review-queue crops from their archived sheets.

A crop set aside for review was cut by whatever version of the detector was
running at the time. Detection has since improved (deskew, and rejoining a
card split down its own text panel), so old crops can be materially worse
than what the same sheet yields today -- measured at Hamming distance 14-20
to the correct card for old crops versus 8 for fresh ones, which is the
difference between "no match" and "identified".

This re-cuts each queued crop from its source sheet and keeps the new one
only when it actually matches the catalogue better. That check is what makes
it safe to run at any time: a sheet whose card count has changed (so the
crop indices no longer line up) simply fails to improve and is left alone.

Nothing is committed and no inventory is touched -- only the queued images
are replaced, so the review step still decides what becomes inventory.

    .venv/bin/python -m scripts.recrop_review_queue [--dry-run]
"""

import argparse
import re
import shutil
from pathlib import Path

import imagehash
from PIL import Image

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.detect import detect_cards
from automation_control.scan_ingest.identify import detect_sheet_set_and_rotation
from automation_control.scan_ingest.session import _load_all_catalogs

CROP_NAME = re.compile(r"^(sheet-[\d-]+?)-card(\d+)-")


def _best_distance(path: Path, catalogs) -> int:
    """Closest Hamming distance to any cached reference card."""
    try:
        with Image.open(path) as image:
            scan = imagehash.phash(image.convert("RGB"))
    except Exception:
        return 999
    best = 999
    for references in catalogs.values():
        for reference in references:
            if reference.phash:
                best = min(best, scan - imagehash.hex_to_hash(reference.phash))
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-cut review-queue crops with the current detector")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    media = Path(settings.scan_media_dir)
    review_dir = media / "needs_review"
    archive = Path(settings.scan_archive_dir)
    work = media / "work" / "_recrop"

    crops = sorted(review_dir.glob("*.jpg"))
    if not crops:
        print("Review queue is empty.")
        return 0

    with SessionLocal() as db:
        catalogs = _load_all_catalogs(db)
    if not catalogs:
        print("error: no sets cached to match against", file=__import__("sys").stderr)
        return 1

    resheets: dict[str, list[Path]] = {}
    improved = unchanged = orphaned = 0

    for crop in crops:
        match = CROP_NAME.match(crop.name)
        if not match:
            continue
        stem, index = match.group(1), int(match.group(2))

        if stem not in resheets:
            source = next(archive.rglob(f"{stem}*.png"), None)
            if source is None:
                resheets[stem] = []
            else:
                probe = detect_cards(source, work / f"{stem}-probe")
                found = detect_sheet_set_and_rotation(probe.crop_paths, catalogs, settings.scan_phash_max_distance)
                rotation = found[1] if found else 270
                resheets[stem] = detect_cards(source, work / stem, post_rotation_degrees=rotation).crop_paths

        fresh = resheets[stem]
        if not fresh:
            orphaned += 1
            continue
        if index - 1 >= len(fresh):
            unchanged += 1
            continue

        old = _best_distance(crop, catalogs)
        new = _best_distance(fresh[index - 1], catalogs)
        if new < old:
            print(f"  {crop.name[:58]:<58} {old:>3} -> {new:>3}")
            if not args.dry_run:
                shutil.copy(fresh[index - 1], crop)
            improved += 1
        else:
            unchanged += 1

    verb = "would improve" if args.dry_run else "improved"
    print(f"\n{verb} {improved}; {unchanged} left as-is; {orphaned} had no archived sheet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
