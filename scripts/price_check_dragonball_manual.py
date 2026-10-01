"""Price-check the 65 hand-catalogued Dragon Ball items from this session's
needs_review cleanup (scripts/catalog_sheet_*.py), using the same
no-network snapshot sources session.py already routes each game through
(SNAPSHOT_PRICE_SOURCES) -- TCGplayer snapshot for Fusion World/DBS
(Panini)/DBZ, Cardotaku snapshot for SDBH/Divers. Dragon Ball Carddass has
no configured source at all (vintage, no bulk source -- see games.py), so
those always report "no quote".

Only touches items with no existing price for that (card, variant), same
dedup as backfill_inventory_prices.py, so re-running is safe.

    .venv/bin/python -m scripts.price_check_dragonball_manual [--dry-run]
"""

import argparse
from collections import defaultdict

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogCard, InventoryItem, TcgCard
from automation_control.scan_ingest.pricing import NullPriceSource, price_and_record
from automation_control.scan_ingest.session import SNAPSHOT_PRICE_SOURCES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    priced: list[tuple[str, str]] = []
    no_quote: list[tuple[str, str]] = []
    no_source: list[tuple[str, str]] = []

    with SessionLocal() as session:
        items = session.scalars(
            select(InventoryItem).where(InventoryItem.scan_image_path.like("%dragonball_manual%"))
        ).all()

        for item in items:
            card = session.get(CatalogCard, item.catalog_item_id)
            if card is None:
                continue

            existing = session.scalars(
                select(CardPrice).where(CardPrice.catalog_item_id == card.id, CardPrice.variant == item.variant)
            ).first()
            if existing:
                continue

            tcg = session.get(TcgCard, item.catalog_item_id)
            game = tcg.game if tcg else None
            source_cls = SNAPSHOT_PRICE_SOURCES.get(game)
            source = source_cls() if source_cls else NullPriceSource()

            if args.dry_run:
                quote = source.get_price(card, item.variant)
            else:
                quote = price_and_record(session, source, card, item.variant)

            label = f"{card.name} ({card.id})"
            if quote:
                priced.append((label, f"${quote.price} {quote.currency}"))
            elif source_cls is None:
                no_source.append((label, game or "?"))
            else:
                no_quote.append((label, game or "?"))

        if args.dry_run:
            session.rollback()

    verb = "would price" if args.dry_run else "priced"
    print(f"\n{verb} {len(priced)} item(s) from cached snapshot data:")
    for label, price in priced:
        print(f"  {label}: {price}")

    print(f"\n{len(no_quote)} item(s) have a snapshot source but no cached quote for this printing:")
    for label, game in no_quote:
        print(f"  {label} ({game})")

    print(f"\n{len(no_source)} item(s) have no configured price source at all (game has no bulk pricing feed):")
    for label, game in no_source:
        print(f"  {label} ({game})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
