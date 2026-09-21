"""Build "Bulk Master" lots from low-value inventory, grouped by set.

Most of a scanned collection is commons: 951 of ~980 owned cards are under
$2 each. Individually those can't be sold once postage is $9.95, but by
the set they're a real product -- which is what this builds.

Cards already listed individually are left alone, so a valuable single is
never quietly swept into a bulk lot. Pricing is a percentage of the lot's
combined market value (bulk sells at a discount to singles, always), with
a floor so a lot never prices below what it costs to post.

    .venv/bin/python -m scripts.build_bulk_lots --dry-run
    .venv/bin/python -m scripts.build_bulk_lots --max-card-price 2 --pct 65
    .venv/bin/python -m scripts.build_bulk_lots --activate
    .venv/bin/python -m scripts.build_bulk_lots --clear
"""

import argparse
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.database import SessionLocal
from automation_control.models import (
    BulkLot, CardSet, CatalogCard, InventoryItem, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus,
)
from automation_control.shop_pricing import default_costs, price_floor
from scripts.list_for_sale import best_price
from scripts.reprice_for_ebay import DEFAULT_EBAY_FEE_PCT

MIN_CARDS_PER_LOT = 5


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--max-card-price", type=float, default=2.0,
                        help="cards worth up to this go into bulk (default 2)")
    parser.add_argument("--pct", type=float, default=65.0,
                        help="percent of combined market value to charge (default 65 -- a 35% bulk discount)")
    parser.add_argument("--min-cards", type=int, default=MIN_CARDS_PER_LOT,
                        help=f"skip sets with fewer cards than this (default {MIN_CARDS_PER_LOT})")
    parser.add_argument("--activate", action="store_true", help="publish immediately instead of drafting")
    parser.add_argument("--clear", action="store_true", help="delete existing lots and exit")
    parser.add_argument("--ebay-fee", type=float, default=float(DEFAULT_EBAY_FEE_PCT),
                        help=f"eBay's final value fee percentage, used in place of the website's own ~1.75%% "
                             f"Stripe rate when computing the floor -- lots are sold on eBay, not the website's "
                             f"own checkout, and eBay's cut already bundles payment processing into this one "
                             f"number (default {DEFAULT_EBAY_FEE_PCT}, matching reprice_for_ebay.py)")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if args.clear:
            lots = session.scalars(select(BulkLot)).all()
            for lot in lots:
                session.delete(lot)
            if not args.dry_run:
                session.commit()
            print(f"{'would delete' if args.dry_run else 'deleted'} {len(lots)} lot(s).")
            return 0

        # A real floor rather than a guessed one: what it costs to fulfil,
        # grossed up for payment fees, plus the target margin. Assumes the
        # customer pays postage, which is the normal case -- the free
        # shipping threshold is flagged on /shop-pricing instead.
        # payment_pct is eBay's own final value fee (--ebay-fee), not the
        # website's ~1.75% Stripe rate default_costs() would otherwise use --
        # these lots are sold on eBay, and eBay's real cut is roughly 7x
        # higher, so reusing the Stripe figure understated the true floor.
        au_rate = session.scalar(select(ShippingRate).where(ShippingRate.zone == ShippingZone.AU).limit(1))
        floor_model = price_floor(
            default_costs(session),
            shipping_charged=Decimal(str(au_rate.price)) if au_rate else Decimal("0"),
            payment_pct=Decimal(str(args.ebay_fee)),
        )
        price_floor_value = floor_model.floor
        print(f"floor: ${price_floor_value} (break-even ${floor_model.break_even} "
              f"+ {floor_model.margin_pct}% margin, customer pays postage)\n")

        listed_singles = {row for row in session.scalars(select(ShopListing.inventory_item_id)) if row}
        existing_sets = {row for row in session.scalars(select(BulkLot.set_id)) if row}

        cap = Decimal(str(args.max_card_price))
        by_set: dict[str, dict] = {}

        items = session.scalars(
            select(InventoryItem).where(InventoryItem.catalog_item_id.isnot(None), InventoryItem.quantity > 0)
        ).all()

        for item in items:
            if item.id in listed_singles:
                continue  # already for sale as a single -- never sweep it into bulk
            found = best_price(session, item.catalog_item_id)
            if found is None:
                continue
            market, _ = found
            if market > cap:
                continue
            card = session.get(CatalogCard, item.catalog_item_id)
            if card is None or not card.set_id:
                continue
            bucket = by_set.setdefault(card.set_id, {"cards": 0, "value": Decimal("0")})
            bucket["cards"] += item.quantity
            bucket["value"] += market * item.quantity

        created = skipped_small = skipped_existing = 0
        status = ShopListingStatus.ACTIVE if args.activate else ShopListingStatus.DRAFT
        multiplier = Decimal(str(args.pct)) / Decimal("100")

        for set_id, bucket in sorted(by_set.items(), key=lambda kv: -kv[1]["cards"]):
            if bucket["cards"] < args.min_cards:
                skipped_small += 1
                continue
            if set_id in existing_sets:
                skipped_existing += 1
                continue

            card_set = session.get(CardSet, set_id)
            set_name = card_set.name if card_set else set_id
            price = max(_round(bucket["value"] * multiplier), price_floor_value)
            title = f"{set_name} — bulk lot ({bucket['cards']} cards)"

            print(f"  {'would build' if args.dry_run else 'built'}: {title} — ${price} "
                  f"(market ${_round(bucket['value'])})")
            created += 1
            if args.dry_run:
                continue

            session.add(BulkLot(
                set_id=set_id, game="pokemon", title=title,
                description=(
                    f"{bucket['cards']} cards from {set_name}, sorted and sleeved. "
                    "Exact contents vary; no duplicates beyond what's listed."
                ),
                card_count=bucket["cards"], market_value=_round(bucket["value"]),
                price=price, currency="AUD", quantity=1, status=status,
                image_url=card_set.logo_url if card_set else None,
            ))

        if not args.dry_run:
            session.commit()

    verb = "would build" if args.dry_run else "built"
    print(f"\n{verb} {created} lot(s) as {status.value}. "
          f"Skipped: {skipped_small} sets under {args.min_cards} cards, {skipped_existing} already have a lot.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
