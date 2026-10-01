"""One-off: apply the confident corrections found by
scripts/audit_committed_foil_variants.py's first pass (2026-09-19) --
cards filed as Holo that the trained classifier is highly confident
(>=0.95) are actually Reverse Holo. Restricted to this one swap direction
because it matches documented history: the me3 (Perfect Order) batch had
a hardcoded --variant normal bug and a multi-day holo/reverse-holo mixup
already partially fixed earlier this session, and these look like more of
the same, not a new failure mode. The audit's other flag types (Normal ->
Holo on old Steam Siege commons, Holo -> Normal on a few me3 cards) are
NOT applied here -- a manual spot-check of the Steam Siege cluster found
a plainly flat, non-foil common (Ponyta) predicted Holo at 100% confidence,
so that direction isn't trusted without more scrutiny.

    .venv/bin/python -m scripts.fix_audited_holo_mislabels
"""

from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from sqlalchemy import select

# (set_id, number, name for logging) -- each currently filed Holo, moving
# every unit of that row to Reverse Holo.
CARDS = [
    ("sv4pt5", "88", "Professor's Research"),
    ("me3", "50", "Gengar"),
    ("me3", "73", "Energy Swatter"),
    ("me3", "28", "Luxray"),
    ("me3", "32", "Mawile"),
    ("sv8pt5", "47", "Larvitar"),
    ("sv8pt5", "5", "Leafeon"),
]


def main() -> int:
    with SessionLocal() as session:
        for set_id, number, name in CARDS:
            card = session.scalar(select(CatalogCard).where(CatalogCard.set_id == set_id, CatalogCard.number == number))
            if card is None:
                print(f"{name} #{number} ({set_id}): card not found, skipping")
                continue

            holo_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == CardVariant.HOLO)
            )
            if holo_row is None or holo_row.quantity < 1:
                print(f"{name} #{number} ({set_id}): no Holo unit found, skipping")
                continue

            moved = holo_row.quantity
            rh_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == CardVariant.REVERSE_HOLO)
            )
            if rh_row is None:
                rh_row = InventoryItem(card_id=card.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=0)
                session.add(rh_row)
                session.flush()
                # Keep whichever photo already exists -- both rows may
                # share the same underlying scan since the mistake was the
                # variant label, not the image.
                rh_row.scan_image_path = holo_row.scan_image_path
            rh_row.quantity += moved
            session.delete(holo_row)
            print(f"{name} #{number} ({set_id}): Holo {moved} -> Reverse Holo (now {rh_row.quantity})")

        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
