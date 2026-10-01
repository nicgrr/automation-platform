"""One-off: 6 Prismatic Evolutions cards were auto-committed as Holo while
the live watcher was still set to --variant holo, but the physical sheets
being scanned were actually reverse holo (the user switched from holo to
reverse-holo scanning; the service's session.env wasn't updated until 2
sheets in). Moves each unit from the wrongly-created Holo row to the
correct Reverse Holo row.

    .venv/bin/python -m scripts.fix_pre_reverse_holo_mislabel
"""

from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from sqlalchemy import select

# (card name, number) -- each had exactly 1 unit wrongly added as Holo
CARDS = [
    ("Groudon", "49"),
    ("Cottonee", "7"),
    ("Hoothoot", "77"),
    ("Goldeen", None),
    ("Riolu", None),
    ("Pyroar", None),
]


def main() -> int:
    with SessionLocal() as session:
        for name, number in CARDS:
            query = select(CatalogCard).where(CatalogCard.set_id == "sv8pt5", CatalogCard.name == name)
            if number:
                query = query.where(CatalogCard.number == number)
            card = session.scalar(query)
            if card is None:
                print(f"{name}: card not found, skipping")
                continue

            holo_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == CardVariant.HOLO)
            )
            if holo_row is None or holo_row.quantity < 1:
                print(f"{name} #{card.number}: no Holo unit found, skipping")
                continue

            if holo_row.quantity == 1:
                session.delete(holo_row)
                print(f"{name} #{card.number}: removed the Holo row entirely (was the only unit)")
            else:
                holo_row.quantity -= 1
                print(f"{name} #{card.number}: Holo {holo_row.quantity + 1} -> {holo_row.quantity}")

            rh_row = session.scalar(
                select(InventoryItem).where(InventoryItem.catalog_item_id == card.id, InventoryItem.variant == CardVariant.REVERSE_HOLO)
            )
            if rh_row is None:
                rh_row = InventoryItem(card_id=card.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=0)
                session.add(rh_row)
            rh_row.quantity += 1
            print(f"{name} #{card.number}: Reverse Holo -> {rh_row.quantity}")

        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
