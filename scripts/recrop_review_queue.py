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
import sys
import time
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.detect import detect_cards
from automation_control.scan_ingest.identify import art_phash, detect_sheet_set_and_rotation
from automation_control.scan_ingest.session import _load_all_catalogs

CROP_NAME = re.compile(r"^(sheet-[\d-]+?)-card(\d+)-")
NO_MATCH = 64  # a full-width Hamming distance: worse than any real match


class Matcher:
    """Closest distance from an image to any cached reference card.

    Holds every reference hash as a bit-matrix so each image is one
    vectorised comparison rather than a Python loop over ~20k cards -- the
    loop version made a full pass take hours.

    Both signals are consulted: the whole card, and the artwork window that
    identifies reverse holos (see identify.ART_REGION). Here they're
    combined by taking the better of the two, which is right for this
    narrow question -- "did this crop get closer to *something* real?" --
    even though it would be wrong for choosing between candidates.
    """

    def __init__(self, catalogs):
        whole, art = [], []
        for references in catalogs.values():
            for reference in references:
                if not reference.phash:
                    continue
                whole.append(imagehash.hex_to_hash(reference.phash).hash.flatten())
                if reference.art_phash:
                    art.append(imagehash.hex_to_hash(reference.art_phash).hash.flatten())
        self.whole = np.array(whole, dtype=bool) if whole else None
        self.art = np.array(art, dtype=bool) if art else None

    def best_distance(self, path: Path) -> int:
        if self.whole is None:
            return NO_MATCH
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                scan = imagehash.phash(rgb).hash.flatten()
                scan_art = art_phash(rgb).hash.flatten()
        except Exception:
            return NO_MATCH
        best = int(np.count_nonzero(self.whole != scan, axis=1).min())
        if self.art is not None:
            best = min(best, int(np.count_nonzero(self.art != scan_art, axis=1).min()))
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
        print("error: no sets cached to match against", file=sys.stderr)
        return 1

    matcher = Matcher(catalogs)
    resheets: dict[str, list[Path]] = {}
    improved = unchanged = orphaned = vanished = 0
    started = time.time()

    for position, crop in enumerate(crops, start=1):
        # The queue is worked by hand while this runs, so a crop listed at
        # startup may since have been accepted or discarded. Writing to it
        # would resurrect a card the operator has already dealt with -- and
        # invite committing it twice -- so a crop that's gone is left gone.
        if not crop.exists():
            vanished += 1
            continue

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

        old = matcher.best_distance(crop)
        new = matcher.best_distance(fresh[index - 1])
        if new >= old:
            unchanged += 1
            continue

        print(f"  [{position}/{len(crops)}] {crop.name[:54]:<54} {old:>3} -> {new:>3}", flush=True)
        if not args.dry_run:
            # Re-checked immediately before writing: the gap between the
            # scan above and this copy is the only window in which the
            # operator could have settled it.
            if crop.exists():
                # copyfile, not copy/copy2: those also set mode/times on the
                # destination, which requires owning it. These crops are written
                # by the scan service under a different user, so only the contents
                # can be replaced.
                shutil.copyfile(fresh[index - 1], crop)
            else:
                vanished += 1
                continue
        improved += 1

    verb = "would improve" if args.dry_run else "improved"
    print(
        f"\n{verb} {improved}; {unchanged} left as-is; {orphaned} had no archived sheet; "
        f"{vanished} were settled while this ran. ({time.time() - started:.0f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
