"""Record a price for inventory that was committed before pricing existed.

Stage 4 (commit) shipped before Stage 6 (pricing), so cards scanned in that
window have `InventoryItem` rows with no matching `CardPrice` observation and
show as $0.00 in the inventory view. Their `CatalogCard.raw_prices` were
captured at cache time, so this needs no network call -- it just runs the
same `price_and_record` path a live scan would have.

Only touches items with no existing price for that (card, variant), so
re-running is safe and won't pad the price history with duplicates.

    .venv/bin/python -m scripts.backfill_inventory_prices [--dry-run]
"""

import argparse
import sys
from collections import defaultdict

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CardSet, CatalogCard, InventoryItem
from automation_control.scan_ingest.pricing import PokemonTcgPriceSource, price_and_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill prices for already-committed inventory")
    parser.add_argument("--dry-run", action="store_true", help="report what would be priced, write nothing")
    args = parser.parse_args(argv)

    source = PokemonTcgPriceSource()
    priced, no_quote = defaultdict(int), defaultdict(list)

    with SessionLocal() as session:
        items = session.scalars(select(InventoryItem)).all()
        for item in items:
            existing = session.scalars(
                select(CardPrice).where(
                    CardPrice.card_id == item.card_id,
                    CardPrice.variant == item.variant,
                )
            ).first()
            if existing:
                continue

            card = session.get(CatalogCard, item.card_id)
            if card is None:
                continue

            if args.dry_run:
                quote = source.get_price(card, item.variant)
            else:
                record = price_and_record(session, source, card, item.variant)
                quote = record and True

            if quote:
                priced[card.set_id] += 1
            else:
                no_quote[card.set_id].append(f"{card.number} {card.name}")

        set_names = {
            s.id: s.name
            for s in session.scalars(
                select(CardSet).where(CardSet.id.in_(set(priced) | set(no_quote)))
            )
        }

    verb = "would price" if args.dry_run else "priced"
    if not priced and not no_quote:
        print("Nothing to backfill -- every inventory item already has a price.")
        return 0

    for set_id in sorted(set(priced) | set(no_quote)):
        name = set_names.get(set_id, set_id)
        print(f"{name} ({set_id}): {verb} {priced.get(set_id, 0)}")
        misses = no_quote.get(set_id, [])
        if misses:
            print(f"  no quote available for {len(misses)}: {', '.join(misses[:6])}" + (" ..." if len(misses) > 6 else ""))

    total_priced = sum(priced.values())
    total_missed = sum(len(v) for v in no_quote.values())
    print(f"\n{verb} {total_priced} item(s); {total_missed} had no usable quote in the cached catalog data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
