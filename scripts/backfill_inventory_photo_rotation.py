"""One-off backfill: correct the orientation of already-committed scan
photos that were saved sideways or upside down.

Why this exists (2026-09-29): the fully-automatic scan path has always
baked the correct rotation into a saved crop (see
scan_ingest/detect.py's `_crop_card`), but the manual `--set-id` path
only took one fixed `--rotate` flag for an entire session -- a pile
scanned sideways without remembering that flag produced wrong-orientation
crops. That manual-path gap is now closed (session.py's `run_session`
detects rotation per sheet, same as the automatic path), but crops
already committed *before* that fix can still be wrong today. That's the
whole reason storefront/common.py shows stock art instead of the
seller's own photo -- this script is the other half of actually being
able to revert that.

For each InventoryItem with a scan_image_path, this checks all 4
rotations of the saved file against its OWN already-known card's
reference hash (phash and, where available, the artwork-window
art_phash) -- not the whole catalogue, since which card this is isn't in
question, only which way up the photo was saved. Rotates the file in
place only when a non-zero rotation matches meaningfully better than the
as-stored orientation, so a photo that's already correct (the overwhelming
majority) is left untouched.

    .venv/bin/python -m scripts.backfill_inventory_photo_rotation --dry-run
    .venv/bin/python -m scripts.backfill_inventory_photo_rotation
"""

import argparse
import uuid
from pathlib import Path

import imagehash
from PIL import Image
from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, InventoryItem
from automation_control.scan_ingest.identify import art_phash

# Same shape as identify.py's own _ROTATION_CANDIDATES /
# _PIL_TRANSPOSE_FOR_CLOCKWISE -- kept local rather than importing those
# private names cross-module.
_ROTATIONS = (0, 90, 180, 270)
_TRANSPOSE_FOR_CLOCKWISE = {
    0: None,
    90: Image.Transpose.ROTATE_270,  # PIL rotates counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

# A rotation must beat the as-stored orientation by at least this many
# bits, and itself land within the app's normal match confidence, before
# this touches a file -- conservative on purpose, since this rewrites a
# real photo in place.
MIN_IMPROVEMENT_BITS = 8


def _best_distance(image: "Image.Image", reference_hash, reference_art_hash) -> int:
    scan_hash = imagehash.phash(image)
    distance = scan_hash - reference_hash if reference_hash else 64
    if reference_art_hash:
        distance = min(distance, art_phash(image) - reference_art_hash)
    return distance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)
    settings = get_settings()
    max_distance = settings.scan_phash_max_distance

    with SessionLocal() as session:
        items = session.execute(
            select(InventoryItem, CatalogCard)
            .join(CatalogCard, CatalogCard.id == InventoryItem.card_id)
            .where(InventoryItem.scan_image_path.isnot(None))
        ).all()
        print(f"{len(items)} inventory item(s) with a saved scan photo and a known reference hash")

        checked = skipped_no_hash = skipped_missing_file = skipped_unreadable = fixed = 0
        for item, card in items:
            if not card.phash:
                skipped_no_hash += 1
                continue
            path = Path(item.scan_image_path)
            if not path.exists():
                skipped_missing_file += 1
                continue

            reference_hash = imagehash.hex_to_hash(card.phash)
            reference_art_hash = imagehash.hex_to_hash(card.art_phash) if card.art_phash else None

            try:
                with Image.open(path) as original:
                    original = original.convert("RGB")
                    distances = {}
                    for degrees in _ROTATIONS:
                        transpose = _TRANSPOSE_FOR_CLOCKWISE[degrees]
                        candidate = original if transpose is None else original.transpose(transpose)
                        distances[degrees] = _best_distance(candidate, reference_hash, reference_art_hash)
            except Exception as exc:
                skipped_unreadable += 1
                print(f"  skip (unreadable): {path} -- {exc}")
                continue
            checked += 1

            best_degrees = min(distances, key=distances.get)
            if best_degrees == 0:
                continue
            if distances[0] - distances[best_degrees] < MIN_IMPROVEMENT_BITS or distances[best_degrees] > max_distance:
                continue

            fixed += 1
            print(f"  {'would rotate' if args.dry_run else 'rotating'} {best_degrees}deg  {card.name} #{card.number}  "
                 f"{path}  (as-stored dist={distances[0]}, {best_degrees}deg dist={distances[best_degrees]})")
            if args.dry_run:
                continue

            with Image.open(path) as original:
                original = original.convert("RGB")
                corrected = original.transpose(_TRANSPOSE_FOR_CLOCKWISE[best_degrees])
                corrected.save(path)
            record_event(
                session, actor_type="user", actor_id=args.user, action="inventory.backfill_photo_rotation",
                resource_type="inventory_item", resource_id=item.id, outcome="success",
                correlation_id=str(uuid.uuid4()),
                details={"path": str(path), "rotated_degrees": best_degrees,
                        "as_stored_distance": distances[0], "corrected_distance": distances[best_degrees]},
            )

        print(f"\nchecked {checked} readable photo(s) against their own reference hash; "
             f"{fixed} {'would be' if args.dry_run else 'were'} rotated.")
        print(f"skipped: {skipped_no_hash} no reference hash, {skipped_missing_file} file missing, "
             f"{skipped_unreadable} unreadable.")
        if args.dry_run:
            print("--dry-run: nothing written.")
            return 0

        session.commit()
        print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
