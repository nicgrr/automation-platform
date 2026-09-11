"""Bring existing active card listings back under scripts.list_for_sale's
MAX_MARKUP cap -- one-off cleanup for listings priced before that cap
existed.

Confirmed live 2026-09-11: 99 active singles priced 3x-10x their best
known market signal (mostly bulk commons floored to a flat $3.99/$4.99
under an earlier, higher ShopCostConfig). list_for_sale.py now caps new
listings at MAX_MARKUP times market; this reprices what's already live
the same way, rather than leaving old listings exempt from the same rule.

Only touches TCG_CARD/GRADED_CARD listings -- sealed products and
collectibles don't have the same market-comparison methodology (see
deal_check.py's sealed-product docstring) and shouldn't be swept into a
"cards" repricing pass.

    .venv/bin/python -m scripts.cap_card_markup --dry-run
    .venv/bin/python -m scripts.cap_card_markup
"""

import argparse
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, ShopListing, ShopListingStatus
from scripts.list_for_sale import MAX_MARKUP, best_price


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        rows = session.execute(
            select(ShopListing, CatalogItem)
            .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .where(ShopListing.status == ShopListingStatus.ACTIVE,
                   CatalogItem.item_type.in_([CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD]))
        ).all()

        repriced = skipped_unpriced = 0
        for listing, catalog_item in rows:
            found = best_price(session, catalog_item.id)
            if found is None:
                skipped_unpriced += 1
                continue
            market, source = found
            cap = _round(market * MAX_MARKUP)
            if listing.price <= cap:
                continue

            print(f"  {'would reprice' if args.dry_run else 'repriced'}: {listing.title} "
                  f"${listing.price} -> ${cap} (market ${market} from {source}, {MAX_MARKUP}x cap)")
            repriced += 1
            if not args.dry_run:
                listing.price = cap

        if not args.dry_run:
            session.commit()

        print(f"\n{'would reprice' if args.dry_run else 'repriced'} {repriced} listing(s). "
              f"{skipped_unpriced} skipped (no market signal on file). "
              f"{len(rows) - repriced - skipped_unpriced} already within the cap.")
        if args.dry_run:
            print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
