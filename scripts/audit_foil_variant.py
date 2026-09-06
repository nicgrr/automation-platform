"""Flag inventory items whose stored photo hints at a different printing
than the variant they're filed under.

`assess_foil` runs on every card as it's scanned and its verdict is only
ever logged, never used to pick the committed variant (see that module's
docstring) -- it's deliberately in "shadow mode" with no confirmed sample
yet to say the signal is trustworthy. That log line scrolls past in
real time and isn't looked at again, so this re-runs the same assessment
against what's actually filed and surfaces the disagreements for a human
to glance at -- the same triage role `audit_inventory_photos.py` plays for
card identity.

Treat every line here as a hint, not a verdict -- the two independent
signals `assess_foil` computes (hash-distance bias and glare bias) already
disagree with each other on real scanned cards; this shares that same
weak confidence, just aimed at a batch of items instead of one at a time.
Read-only: it reports, it never edits inventory.

    .venv/bin/python -m scripts.audit_foil_variant
"""

from pathlib import Path

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardSet, CatalogCard, InventoryItem
from automation_control.scan_ingest.foil import assess_foil


def main(argv: list[str] | None = None) -> int:
    with SessionLocal() as session:
        set_names = {s.id: s.name for s in session.scalars(select(CardSet))}
        cards_by_id = {c.id: c for c in session.scalars(select(CatalogCard))}
        items = session.scalars(select(InventoryItem)).all()
        print(f"checking {len(items)} inventory item(s) for a foil/variant mismatch\n", flush=True)

        flagged = []
        for item in items:
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
            flagged.append((item, card, assessment))

        if not flagged:
            print("Nothing looks off -- every checked photo's foil signal agrees with its filed variant.")
            return 0

        print(f"{len(flagged)} item(s) where the photo's foil signal disagrees with the filed variant:\n")
        for item, card, assessment in flagged:
            label = f"{set_names.get(card.set_id, card.set_id)} #{card.number} {card.name}"
            print(f"  {label}")
            print(f"    filed as: {item.variant.value}  |  photo suggests: {assessment.suggestion.value}"
                  f"  (confidence {assessment.confidence:.2f}, {assessment.reason})")
        print("\nNothing was changed -- this is a hint to re-examine, not a confirmed error.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
