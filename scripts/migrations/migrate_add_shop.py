"""One-time migration: add the NL Vault storefront tables -- Module 24.

    shop_listings      what is actually offered for sale, at a chosen price
    shipping_rates     flat rate per zone (AU / international)
    shop_orders        customer orders
    shop_order_items   the lines on an order

All brand new, no backfill. Nothing existing is touched: the storefront
reads catalog_items/inventory_items but owns its own tables, so listing
something for sale never mutates inventory.

Seeds two default shipping rates (AU and international) if none exist --
placeholder amounts, meant to be corrected to real postage costs.

    .venv/bin/python -m scripts.migrate_add_shop --dry-run
    .venv/bin/python -m scripts.migrate_add_shop
"""

import argparse
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from automation_control import models  # noqa: F401  (registers tables on Base.metadata)
from automation_control.database import Base, engine
from automation_control.models import ShippingRate, ShippingZone

NEW_TABLES = ["shop_listings", "shipping_rates", "shop_orders", "shop_order_items"]

DEFAULT_RATES = [
    (ShippingZone.AU, "Australia — tracked parcel", Decimal("9.95"), Decimal("150.00")),
    (ShippingZone.INTERNATIONAL, "International — tracked", Decimal("29.95"), None),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [t for t in NEW_TABLES if t not in existing]
    print(f"tables to create: {to_create or 'none'}")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in to_create])

    with Session(engine) as session:
        if session.scalar(select(ShippingRate).limit(1)) is None:
            for zone, label, price, free_over in DEFAULT_RATES:
                session.add(ShippingRate(zone=zone, label=label, price=price, free_over=free_over, is_default=True))
            session.commit()
            print(f"seeded {len(DEFAULT_RATES)} default shipping rate(s) -- correct these to real postage costs")
        else:
            print("shipping rates already present, left alone")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
