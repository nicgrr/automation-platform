"""Same job as refresh_foil_candidates.py, sliced into chunks that each
finish well under the ~600s ceiling this environment kills long-running
commands past -- refresh_foil_candidates.py's single ~1500-item, ~17-minute
pass got killed twice outright with nothing written. Each call here
computes candidates for one slice of inventory items and merges them into
the same cache file /foil-review reads (foil_candidates_cache.json),
replacing only that slice's previous entries so re-running a chunk is safe.

    .venv/bin/python -m scripts.refresh_foil_candidates_chunked --offset 0 --limit 400
    .venv/bin/python -m scripts.refresh_foil_candidates_chunked --offset 400 --limit 400
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.foil_review import IDENTITY_MISMATCH_THRESHOLD, _cache_path, assess_foil
from automation_control.models import CardSet, CatalogCard, InventoryItem


def _candidates_for_slice(session, offset: int, limit: int | None) -> tuple[list[dict], int, set[str]]:
    set_names = {s.id: s.name for s in session.scalars(select(CardSet))}
    cards_by_id = {c.id: c for c in session.scalars(select(CatalogCard))}
    items = session.scalars(select(InventoryItem).order_by(InventoryItem.id)).all()
    total = len(items)
    window = items[offset:offset + limit] if limit else items[offset:]
    window_ids = {item.id for item in window}

    candidates = []
    for item in window:
        if not item.scan_image_path:
            continue
        path = Path(item.scan_image_path)
        if not path.exists():
            continue
        card = cards_by_id.get(item.card_id)
        if card is None:
            continue
        assessment = assess_foil(path, card)
        if assessment.suggestion is None or assessment.suggestion == item.variant:
            continue
        own_distance = min(
            assessment.metrics.get("whole_d", float("inf")),
            assessment.metrics.get("art_d", float("inf")),
        )
        if own_distance > IDENTITY_MISMATCH_THRESHOLD:
            continue
        bias = assessment.metrics.get("body_bias")
        candidates.append({
            "item_id": item.id, "card_id": card.id,
            "label": f"{set_names.get(card.set_id, card.set_id)} #{card.number} {card.name}",
            "filed": item.variant.value, "suggested": assessment.suggestion.value,
            "confidence": assessment.confidence, "reason": assessment.reason,
            "metrics": assessment.metrics, "magnitude": abs(bias) if bias is not None else 0.0,
        })
    return candidates, total, window_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with SessionLocal() as session:
        new_candidates, total, window_ids = _candidates_for_slice(session, args.offset, args.limit)

    cache_path = _cache_path()
    existing = []
    if cache_path.exists():
        try:
            existing = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []
    # Replace only this slice's items -- everything outside window_ids from
    # a previous chunk (or a previous full run) is left untouched.
    kept = [c for c in existing if c["item_id"] not in window_ids]
    merged = kept + new_candidates
    merged.sort(key=lambda c: -c["magnitude"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(merged))

    print(f"slice {args.offset}..{args.offset + len(window_ids)} of {total}: "
          f"{len(new_candidates)} candidate(s) in this slice, {len(merged)} total cached now.")
    if args.limit and args.offset + args.limit < total:
        print(f"next: --offset {args.offset + args.limit} --limit {args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
