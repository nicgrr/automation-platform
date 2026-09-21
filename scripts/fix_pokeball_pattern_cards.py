"""One-off: 3 Prismatic Evolutions cards the user identified as Poke Ball
pattern (a distinct chase variant, not plain Holo/Normal -- see the
CardVariant docstring) were filed under the wrong bucket from earlier scans.
Moves 1 unit each to the new POKEBALL_PATTERN variant.

    .venv/bin/python -m scripts.fix_pokeball_pattern_cards
"""

from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from sqlalchemy import select

# (card number, wrong variant it's currently filed under)
# Black Belt's Training #97's Holo unit is the one from the original 12
# legit-holo commits list checked earlier this session; Regigigas and
# Explorer's Guidance only ever had a Normal bucket.
CARDS = [
    ("Black Belt's Training", "97", CardVariant.HOLO),
    ("Regigigas", "86", CardVariant.NORMAL),
    ("Explorer's Guidance", "107", CardVariant.NORMAL),
]


def main() -> int:
    with SessionLocal() as session:
        for name, number, wrong_variant in CARDS:
            card = session.scalar(
                select(CatalogCard).where(CatalogCard.set_id == "sv8pt5", CatalogCard.number == number)
            )
            if card is None:
                print(f"{name} #{number}: card not found, skipping")
                continue

            wrong_row = session.scalar(
                select(InventoryItem).where(InventoryItem.card_id == card.id, InventoryItem.variant == wrong_variant)
            )
            if wrong_row is None or wrong_row.quantity < 1:
                print(f"{name} #{number}: no {wrong_variant.value} unit found, skipping")
                continue

            if wrong_row.quantity == 1:
                session.delete(wrong_row)
                print(f"{name} #{number}: removed the {wrong_variant.value} row entirely (was the only unit)")
            else:
                wrong_row.quantity -= 1
                print(f"{name} #{number}: {wrong_variant.value} {wrong_row.quantity + 1} -> {wrong_row.quantity}")

            pb_row = session.scalar(
                select(InventoryItem).where(InventoryItem.card_id == card.id, InventoryItem.variant == CardVariant.POKEBALL_PATTERN)
            )
            if pb_row is None:
                pb_row = InventoryItem(card_id=card.id, variant=CardVariant.POKEBALL_PATTERN, condition="Near Mint", quantity=0)
                session.add(pb_row)
            pb_row.quantity += 1
            print(f"{name} #{number}: Pokeball Pattern -> {pb_row.quantity}")

        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
