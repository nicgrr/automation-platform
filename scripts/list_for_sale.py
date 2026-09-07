"""Put owned inventory on the NL Vault storefront -- Module 24.

Listing is deliberately an explicit act, not a side effect of scanning:
this reads inventory_items, applies your filters, and creates
shop_listings rows. Nothing appears in the shop until this is run, and
inventory is never mutated by it.

Pricing starts from the best market signal on file for each card
(`ebay_active_low` first -- the conservative figure -- then any other
source), with an optional margin. Anything with no price signal at all is
skipped rather than guessed at, since a wrong price in a public shop is a
real-money mistake.

    # see what would be listed, write nothing
    .venv/bin/python -m scripts.list_for_sale --min-price 5 --dry-run

    # list everything worth $5+, at market, ready to review as drafts
    .venv/bin/python -m scripts.list_for_sale --min-price 5

    # list at 10% over market and publish immediately
    .venv/bin/python -m scripts.list_for_sale --min-price 20 --margin 10 --activate

    # take everything back off sale
    .venv/bin/python -m scripts.list_for_sale --withdraw-all
"""

import argparse
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.database import SessionLocal
from automation_control.models import (
    CardPrice, CatalogCard, CatalogItem, InventoryItem, ShopListing, ShopListingStatus,
)

PREFERRED_SOURCES = ["ebay_active_low", "optcgapi_market", "pokemonpricetracker_market"]


def best_price(session: Session, catalog_item_id: str) -> tuple[Decimal, str] | None:
    """Best available market signal for one item, most trusted source
    first, newest within a source. Returns None when nothing is known --
    the caller skips rather than inventing a number."""
    rows = session.scalars(
        select(CardPrice).where(CardPrice.card_id == catalog_item_id).order_by(CardPrice.fetched_at.desc())
    ).all()
    if not rows:
        return None
    for source in PREFERRED_SOURCES:
        for row in rows:
            if row.source == source and row.price and row.price > 0:
                return Decimal(str(row.price)), row.source
    for row in rows:
        if row.price and row.price > 0:
            return Decimal(str(row.price)), row.source
    return None


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--min-price", type=float, default=5.0, help="skip items worth less than this (default 5)")
    parser.add_argument("--margin", type=float, default=0.0, help="percent to add over the market signal")
    parser.add_argument("--limit", type=int, default=500, help="max listings to create in one run")
    parser.add_argument("--activate", action="store_true", help="publish immediately instead of creating drafts")
    parser.add_argument("--withdraw-all", action="store_true", help="take every listing off sale and exit")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if args.withdraw_all:
            listings = session.scalars(select(ShopListing)).all()
            for listing in listings:
                listing.status = ShopListingStatus.WITHDRAWN
            if not args.dry_run:
                session.commit()
            print(f"{'would withdraw' if args.dry_run else 'withdrew'} {len(listings)} listing(s).")
            return 0

        already = {row for row in session.scalars(select(ShopListing.inventory_item_id)) if row}
        items = session.scalars(
            select(InventoryItem).where(InventoryItem.catalog_item_id.isnot(None), InventoryItem.quantity > 0)
        ).all()

        created = skipped_cheap = skipped_unpriced = skipped_existing = 0
        min_price = Decimal(str(args.min_price))
        multiplier = Decimal("1") + Decimal(str(args.margin)) / Decimal("100")
        status = ShopListingStatus.ACTIVE if args.activate else ShopListingStatus.DRAFT

        for item in items:
            if created >= args.limit:
                break
            if item.id in already:
                skipped_existing += 1
                continue

            found = best_price(session, item.catalog_item_id)
            if found is None:
                skipped_unpriced += 1
                continue
            market, source = found
            if market < min_price:
                skipped_cheap += 1
                continue

            catalog_item = session.get(CatalogItem, item.catalog_item_id)
            if catalog_item is None:
                skipped_unpriced += 1
                continue
            card = session.get(CatalogCard, item.catalog_item_id)

            price = _round(market * multiplier)
            title = catalog_item.name
            if card is not None and card.number:
                title = f"{catalog_item.name} #{card.number}"
            variant = item.variant.value.replace("_", " ").title() if item.variant else None
            condition = " · ".join(part for part in [item.condition, variant] if part)

            print(f"  {'would list' if args.dry_run else 'listed'}: {title} — ${price} (from {source})")
            created += 1
            if args.dry_run:
                continue

            session.add(ShopListing(
                catalog_item_id=item.catalog_item_id, inventory_item_id=item.id,
                title=title, condition=condition or None, price=price, currency="AUD",
                quantity=item.quantity, status=status,
                image_url=catalog_item.image_url,
            ))

        if not args.dry_run:
            session.commit()

    verb = "would create" if args.dry_run else "created"
    print(
        f"\n{verb} {created} listing(s) as {status.value}. "
        f"Skipped: {skipped_cheap} under ${args.min_price}, {skipped_unpriced} with no price on file, "
        f"{skipped_existing} already listed."
    )
    if args.dry_run:
        print("--dry-run: nothing written.")
    elif not args.activate:
        print("These are DRAFTS -- re-run with --activate, or flip status to 'active', to make them public.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
