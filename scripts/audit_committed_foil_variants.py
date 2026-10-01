"""Re-run the trained foil classifier (deployed 2026-09-19, see
scripts/train_foil_classifier.py) against every already-committed card's
own stored scan photo, and flag any it's now highly confident disagrees
with the variant it's actually filed under.

This only catches variant (foil-type) mistakes, not card-identity mistakes
-- the classifier judges holo/reverse-holo/normal from the photo, it
doesn't re-identify which card is in the photo. Cards still sitting in
/review are a separate, identity problem this doesn't touch.

Read-only: prints a report, changes nothing. Fixing a specific flagged
card is a manual follow-up, the same one-off pattern used earlier this
session (decrement the wrong variant row, increment/create the right one).

    .venv/bin/python -m scripts.audit_committed_foil_variants --offset 0 --limit 400
    .venv/bin/python -m scripts.audit_committed_foil_variants --offset 400 --limit 400

--offset/--limit process one slice of the (stable, ordered-by-id) row set
per call -- the background runner here has repeatedly killed this script
partway through an unbroken ~20-minute pass over all ~1800 rows, for
reasons that didn't show up in dmesg or free -h, so this makes progress
resumable in ~5-minute slices instead of re-litigating that.
"""

import argparse
import os

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from automation_control.scan_ingest import foil_features

CONFIDENCE_FLAG = 0.90  # a bit below the live auto-commit bar, to surface borderline cases too


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with SessionLocal() as session:
        rows = session.execute(
            select(InventoryItem, CatalogCard)
            .join(CatalogCard, CatalogCard.id == InventoryItem.catalog_item_id)
            .where(InventoryItem.variant.in_([CardVariant.NORMAL, CardVariant.HOLO, CardVariant.REVERSE_HOLO]))
            .order_by(InventoryItem.id)
        ).all()
        total = len(rows)
        window = rows[args.offset:args.offset + args.limit] if args.limit else rows[args.offset:]
        print(f"processing rows {args.offset}..{args.offset + len(window)} of {total}", flush=True)

        checked = flagged = no_image = 0
        for i, (inv, card) in enumerate(window):
            if i % 50 == 0:
                print(f"...{args.offset + i}/{total}", flush=True)
            if not inv.scan_image_path or not os.path.exists(inv.scan_image_path):
                no_image += 1
                continue
            features = foil_features.extract_features(inv.scan_image_path)
            if features is None:
                continue
            prediction = foil_features.predict(features)
            if prediction is None:
                print("no trained model available -- run scripts/train_foil_classifier.py first")
                return 1
            checked += 1
            predicted_value, confidence = prediction
            if predicted_value != inv.variant.value and confidence >= CONFIDENCE_FLAG:
                flagged += 1
                print(f"  FLAG  {card.name} #{card.number} ({card.set_id})  filed as {inv.variant.value}  "
                      f"-> classifier says {predicted_value} ({confidence:.2f})  qty={inv.quantity}  "
                      f"[{inv.scan_image_path}]", flush=True)

    print(f"\nslice done: checked {checked}, {no_image} had no photo on disk, "
          f"{flagged} flagged (>= {CONFIDENCE_FLAG:.2f} confidence, prediction != filed variant).")
    if args.limit and args.offset + args.limit < total:
        print(f"next: --offset {args.offset + args.limit} --limit {args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
