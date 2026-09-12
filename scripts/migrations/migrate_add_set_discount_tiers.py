"""One-time migration: add set_discount_tiers -- bulk-set buying discounts.

Seeds three tiers: buy 3+ from one TCG set or collectible series and get
5% off those items, 5+ gets 10%, 10+ gets 15%. Tune these directly in the
table once real order sizes tell you what actually moves a cart.

    .venv/bin/python -m scripts.migrate_add_set_discount_tiers --dry-run
    .venv/bin/python -m scripts.migrate_add_set_discount_tiers
"""

import argparse
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine
from automation_control.models import SetDiscountTier

NEW_TABLES = ["set_discount_tiers"]

DEFAULT_TIERS = [
    (3, Decimal("5.00"), "3+ from one set"),
    (5, Decimal("10.00"), "5+ from one set"),
    (10, Decimal("15.00"), "10+ from one set"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [t for t in NEW_TABLES if t not in existing]
    print(f"tables to create: {to_create or 'none'}")
    if args.dry_run:
        print(f"would seed {len(DEFAULT_TIERS)} tier(s) if the table is new.")
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in to_create])

    with Session(engine) as session:
        if session.scalar(select(SetDiscountTier).limit(1)) is None:
            for min_quantity, discount_pct, label in DEFAULT_TIERS:
                session.add(SetDiscountTier(min_quantity=min_quantity, discount_pct=discount_pct, label=label))
            session.commit()
            print(f"seeded {len(DEFAULT_TIERS)} tier(s).")
        else:
            print("tiers already exist; left untouched.")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
