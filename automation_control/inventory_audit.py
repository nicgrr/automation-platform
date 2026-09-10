"""Shared logic: does an inventory item's stored photo actually match the
card it's filed under?

Used by both `scripts/audit_inventory_photos.py` (the hourly cron/CLI run)
and the `/inventory-audit` review page -- one implementation, so what the
timer finds and what a human reviews in the browser can never drift apart.
See that script's docstring for the real-world case this exists to catch
and its known blind spots (reprints shared across sets, crops that catch
the neighbouring card).
"""

from pathlib import Path

import imagehash
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CardSet, CatalogCard, InventoryItem
from .scan_ingest.identify import art_phash

DEFAULT_THRESHOLD = 12


def _hashes(path: Path):
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return imagehash.phash(rgb).hash.flatten(), art_phash(rgb).hash.flatten()


def find_mismatches(session: Session, threshold: int = DEFAULT_THRESHOLD) -> list[dict]:
    """Every InventoryItem whose photo hashes further from its own filed
    card than `threshold`, together with the reference card its photo
    actually looks closest to. Sorted most-confident mismatch first."""
    cards = [c for c in session.scalars(select(CatalogCard)) if c.phash]
    set_names = {s.id: s.name for s in session.scalars(select(CardSet))}
    by_id = {c.id: c for c in cards}

    whole = np.array([imagehash.hex_to_hash(c.phash).hash.flatten() for c in cards], dtype=bool)
    art_index = [i for i, c in enumerate(cards) if c.art_phash]
    art = np.array([imagehash.hex_to_hash(cards[i].art_phash).hash.flatten() for i in art_index], dtype=bool) \
        if art_index else None

    def label(card) -> str:
        return f"{set_names.get(card.set_id, card.set_id)} #{card.number} {card.name}"

    items = session.scalars(select(InventoryItem)).all()
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
        if own <= threshold:
            continue

        distances = np.count_nonzero(whole != scan, axis=1)
        if art is not None:
            art_distances = np.count_nonzero(art != scan_art, axis=1)
            for position, index in enumerate(art_index):
                distances[index] = min(distances[index], art_distances[position])
        best = int(np.argmin(distances))
        looks_like = cards[best]

        mismatches.append({
            "item_id": item.id,
            "card_id": filed.id,
            "filed_label": label(filed),
            "looks_like_id": looks_like.id,
            "looks_like_label": label(looks_like),
            "own_distance": own,
            "looks_like_distance": int(distances[best]),
        })

    mismatches.sort(key=lambda m: -m["own_distance"])
    return mismatches
