"""Check that every committed card's photo is actually that card.

Identification can be confidently wrong. With ~20k reference cards an
unrelated card sometimes hashes closer than the right one, and same-numbered
cards in different sets are an easy confusion -- a Sword & Shield Joltik
(070/202) was committed as an Unbroken Bonds Gengar (70/214), carrying that
card's $33 valuation with it.

This compares each inventory item's stored photo against the card it is
filed under, and for anything that doesn't match, says what the photo looks
like instead. Read-only: it reports, it never edits inventory, because
choosing the right card is exactly the judgement that got this wrong.

Treat the output as triage, not a verdict. On its first real run six items
were flagged and three were correct all along -- a crop that catches the
edge of the next card, or a foil, hashes badly against its own reference
without being the wrong card. Equally, it misses cards filed under the
wrong *set* when the artwork is a reprint shared by both, which is how ten
Paldean Fates cards sat under Scarlet & Violet with only five flagged. Read
the number printed on the scan before changing anything.

    .venv/bin/python -m scripts.audit_inventory_photos [--threshold 12]
"""

import argparse
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image
from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardSet, CatalogCard, InventoryItem
from automation_control.scan_ingest.identify import art_phash


def _hashes(path: Path):
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return imagehash.phash(rgb).hash.flatten(), art_phash(rgb).hash.flatten()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify each committed card's photo matches the card")
    parser.add_argument("--threshold", type=int, default=12,
                        help="Hamming distance above which a photo is considered a mismatch")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        cards = [c for c in session.scalars(select(CatalogCard)) if c.phash]
        set_names = {s.id: s.name for s in session.scalars(select(CardSet))}
        by_id = {c.id: c for c in cards}

        whole = np.array([imagehash.hex_to_hash(c.phash).hash.flatten() for c in cards], dtype=bool)
        art_index = [i for i, c in enumerate(cards) if c.art_phash]
        art = np.array([imagehash.hex_to_hash(cards[i].art_phash).hash.flatten() for i in art_index], dtype=bool) \
            if art_index else None

        items = session.scalars(select(InventoryItem)).all()
        print(f"auditing {len(items)} inventory item(s) against {len(cards):,} reference cards\n", flush=True)

        mismatches = []
        for item in items:
            if not item.scan_image_path:
                continue
            path = Path(item.scan_image_path)
            if not path.exists():
                continue
            filed = by_id.get(item.card_id)
            if filed is None or not filed.phash:
                continue

            try:
                scan, scan_art = _hashes(path)
            except Exception:
                continue

            own = int(np.count_nonzero(imagehash.hex_to_hash(filed.phash).hash.flatten() != scan))
            if filed.art_phash:
                own = min(own, int(np.count_nonzero(imagehash.hex_to_hash(filed.art_phash).hash.flatten() != scan_art)))
            if own <= args.threshold:
                continue

            # What does the photo actually look like?
            distances = np.count_nonzero(whole != scan, axis=1)
            if art is not None:
                art_distances = np.count_nonzero(art != scan_art, axis=1)
                for position, index in enumerate(art_index):
                    distances[index] = min(distances[index], art_distances[position])
            best = int(np.argmin(distances))
            mismatches.append((own, item, filed, cards[best], int(distances[best])))

        if not mismatches:
            print("Every committed card's photo matches the card it's filed under.")
            return 0

        print(f"{len(mismatches)} item(s) whose photo does not match the card they're filed under:\n")
        print(f"  {'filed as':<46}{'photo looks like':<46}")
        print("  " + "-" * 92)
        for own, item, filed, looks_like, distance in sorted(mismatches, key=lambda m: -m[0]):
            filed_label = f"{set_names.get(filed.set_id, filed.set_id)[:20]} #{filed.number} {filed.name}"
            actual_label = f"{set_names.get(looks_like.set_id, looks_like.set_id)[:20]} #{looks_like.number} {looks_like.name}"
            print(f"  {filed_label[:44]:<46}{actual_label[:44]:<46}")
            print(f"  {'  d' + str(own) + ' from what it says it is':<46}{'  d' + str(distance) + ' from this':<46}")
        print("\nNothing was changed. Correct one in the browser, or re-scan the card.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
