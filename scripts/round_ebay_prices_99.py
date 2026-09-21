"""Round every active single-card listing price up to the next .99 --
charm pricing for when these go live on eBay. Always rounds *up*, never to
the nearest .99: the price already sitting there is a deliberately computed
break-even floor (see reprice_for_ebay.py), and rounding down to a nearer
.99 could push a listing back under cost once eBay's cut is accounted for.

    listed_price = ceil(price - 0.99) + 0.99

e.g. $1.20 -> $1.99, $2.57 -> $2.99, $1.99 -> $1.99 (unchanged).

Same scope as reprice_for_ebay.py: only active TCG_CARD/GRADED_CARD
listings -- sealed products, collectibles, and Bulk Master lots have their
own pricing and aren't touched here.

    .venv/bin/python -m scripts.round_ebay_prices_99 --dry-run
    .venv/bin/python -m scripts.round_ebay_prices_99
"""

import argparse
import math
from decimal import Decimal

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, ShopListing, ShopListingStatus


def round_up_to_99(price: Decimal) -> Decimal:
    whole = math.ceil(price - Decimal("0.99"))
    return Decimal(whole) + Decimal("0.99")


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

        changed = unchanged = 0
        for listing, _ in rows:
            target = round_up_to_99(listing.price)
            if target == listing.price:
                unchanged += 1
                continue
            print(f"  {'would round' if args.dry_run else 'rounded'}: {listing.title} ${listing.price} -> ${target}")
            changed += 1
            if not args.dry_run:
                listing.price = target

        if not args.dry_run:
            session.commit()

        print(f"\n{'would round' if args.dry_run else 'rounded'} {changed} listing(s). {unchanged} already at .99.")
        if args.dry_run:
            print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
