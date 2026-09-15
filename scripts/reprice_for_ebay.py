"""Reprice every active card listing to a 20% markup over its market
signal, grossed up for eBay's ~13% final value fee -- so 20% is what you
actually keep after eBay's cut, not a raw markup that fee then erodes.

    listed_price = market_price * (1 + TARGET_MARGIN_PCT/100) / (1 - EBAY_FEE_PCT/100)

This is lower than scripts.list_for_sale's MAX_MARKUP=2 (100%) cap for
virtually every card -- that's the point, per the request this was built
for: account for eBay's cut, but make the cards more affordable than the
old cap allowed. A $2 market card capped as high as $4 before now lists
at about $2.76.

Deliberately NOT floored by automation_control.shop_pricing.price_floor:
tried that first and it forced every cheap common up to ~$14.56 rather
than down, which is backwards from what this script exists to do. That
floor bakes in the *website's* fulfilment assumptions -- $9.95 postage
folded into the item price, plus a 25% target margin -- neither of which
applies here. On eBay postage is charged to the buyer separately via
your shipping policy (the $4 letter-mail policy set up earlier), and
eBay's own cut is already grossed up in the formula above; reusing a
floor built around a $9.95-postage-inside-the-price assumption isn't a
safety net for this listing model, it's a different, incompatible one.
The only floor that still makes sense here is real packaging cost --
materials aren't free even once postage and fees are accounted for
elsewhere -- so that's the one applied below.

Only touches TCG_CARD/GRADED_CARD listings, matching cap_card_markup.py's
scope -- sealed products, collectibles, and Bulk Master lots have their
own separate pricing methodology and aren't swept into a market-signal
repricing pass.

    .venv/bin/python -m scripts.reprice_for_ebay --dry-run
    .venv/bin/python -m scripts.reprice_for_ebay
    .venv/bin/python -m scripts.reprice_for_ebay --markup 15 --ebay-fee 13.4
"""

import argparse
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, ShopListing, ShopListingStatus
from automation_control.shop_pricing import default_costs
from scripts.list_for_sale import best_price

DEFAULT_TARGET_MARKUP_PCT = Decimal("20")
DEFAULT_EBAY_FEE_PCT = Decimal("13")


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def target_price(market: Decimal, markup_pct: Decimal, ebay_fee_pct: Decimal) -> Decimal:
    divisor = Decimal("1") - ebay_fee_pct / Decimal("100")
    if divisor <= 0:
        raise ValueError("ebay_fee_pct must be below 100")
    return _round(market * (Decimal("1") + markup_pct / Decimal("100")) / divisor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markup", type=Decimal, default=DEFAULT_TARGET_MARKUP_PCT,
                        help=f"target markup percent you keep after eBay's cut (default {DEFAULT_TARGET_MARKUP_PCT})")
    parser.add_argument("--ebay-fee", type=Decimal, default=DEFAULT_EBAY_FEE_PCT,
                        help=f"eBay's final value fee percent (default {DEFAULT_EBAY_FEE_PCT})")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        rows = session.execute(
            select(ShopListing, CatalogItem)
            .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .where(ShopListing.status == ShopListingStatus.ACTIVE,
                   CatalogItem.item_type.in_([CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD]))
        ).all()

        packaging_floor = Decimal(str(default_costs(session).packaging_cost))

        repriced = unchanged = skipped_unpriced = 0
        for listing, catalog_item in rows:
            found = best_price(session, catalog_item.id)
            if found is None:
                skipped_unpriced += 1
                continue
            market, source = found
            target = max(target_price(market, args.markup, args.ebay_fee), packaging_floor)
            if target == listing.price:
                unchanged += 1
                continue

            direction = "up" if target > listing.price else "down"
            print(f"  {'would reprice' if args.dry_run else 'repriced'} ({direction}): {listing.title} "
                  f"${listing.price} -> ${target} (market ${market} from {source})")
            repriced += 1
            if not args.dry_run:
                listing.price = target

        if not args.dry_run:
            session.commit()

        print(f"\n{'would reprice' if args.dry_run else 'repriced'} {repriced} listing(s). "
              f"{unchanged} already at target. {skipped_unpriced} skipped (no market signal on file).")
        if args.dry_run:
            print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
