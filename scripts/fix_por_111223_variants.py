"""One-off correction for sheet sheet-20260914-160328-111223.png (Perfect
Order / me3): the persistent scan-ingest.service is hardcoded to
`--variant normal`, so this sheet's 5 foil cards were auto-committed as
plain Normal prints when they were really holo/reverse-holo. The user
confirmed the real foil type for each card by eye; this moves each unit
from the wrongly-created Normal row to the correct one.

    .venv/bin/python -m scripts.fix_por_111223_variants
"""

from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from sqlalchemy import select

# (card number, units wrongly filed as Normal today, correct variant)
CORRECTIONS = [
    ("45", 1, CardVariant.HOLO),          # Tyrantrum
    ("86", 2, CardVariant.HOLO),          # Growing Grass Energy
    ("42", 1, CardVariant.REVERSE_HOLO),  # Binacle
    ("14", 1, CardVariant.REVERSE_HOLO),  # Talonflame
]


def main() -> int:
    with SessionLocal() as session:
        for number, units, correct_variant in CORRECTIONS:
            card = session.scalar(select(CatalogCard).where(CatalogCard.set_id == "me3", CatalogCard.number == number))
            if card is None:
                print(f"#{number}: card not found, skipping")
                continue

            normal_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == CardVariant.NORMAL)
            )
            if normal_row is None or normal_row.quantity < units:
                print(f"{card.name} #{number}: expected >= {units} Normal units, found "
                      f"{normal_row.quantity if normal_row else 0} -- skipping, check manually")
                continue

            if normal_row.quantity == units:
                session.delete(normal_row)
                print(f"{card.name} #{number}: removed the Normal row entirely (all {units} units were today's mistake)")
            else:
                normal_row.quantity -= units
                print(f"{card.name} #{number}: Normal {normal_row.quantity + units} -> {normal_row.quantity}")

            correct_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == correct_variant)
            )
            if correct_row is None:
                correct_row = InventoryItem(card_id=card.id, variant=correct_variant, condition="Near Mint", quantity=0)
                session.add(correct_row)
            correct_row.quantity += units
            print(f"{card.name} #{number}: {correct_variant.value} -> {correct_row.quantity}")

        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
