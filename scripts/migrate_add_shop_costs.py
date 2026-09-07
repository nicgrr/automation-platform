"""One-time migration: add shop_cost_configs -- Module 25.

Seeds one default row from real Australian figures (Stripe AU domestic
1.65% + $0.30, AusPost tracked parcel, a sleeve/toploader/mailer), meant
to be corrected to whatever you actually pay.

    .venv/bin/python -m scripts.migrate_add_shop_costs --dry-run
    .venv/bin/python -m scripts.migrate_add_shop_costs
"""

import argparse
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine
from automation_control.models import ShopCostConfig

NEW_TABLES = ["shop_cost_configs"]


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
        if session.scalar(select(ShopCostConfig).limit(1)) is None:
            session.add(ShopCostConfig(
                label="Default (Stripe AU + AusPost)",
                postage_cost_au=Decimal("9.95"), postage_cost_intl=Decimal("29.95"),
                packaging_cost=Decimal("1.20"),
                payment_pct=Decimal("1.650"), payment_fixed=Decimal("0.30"),
                target_margin_pct=Decimal("25.00"), is_default=True,
            ))
            session.commit()
            print("seeded default cost config -- correct these to your real costs")
        else:
            print("cost config already present, left alone")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
