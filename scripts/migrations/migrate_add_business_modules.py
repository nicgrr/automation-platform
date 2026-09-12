"""One-time migration: create the Phase 2 business-logic tables (purchase
lots, potential stock, marketplaces/fee rules, sales, customers, suppliers,
goals, release calendar). All brand new -- no existing table is touched,
no backfill is needed since none of these concepts existed before. Safe to
run any time; Base.metadata.create_all only ever adds missing tables.

    .venv/bin/python -m scripts.migrate_add_business_modules --dry-run
    .venv/bin/python -m scripts.migrate_add_business_modules
"""

import argparse

from sqlalchemy import inspect

from automation_control import models  # noqa: F401  (registers tables on Base.metadata)
from automation_control.database import Base, engine

NEW_TABLES = [
    "buy_threshold_configs", "purchase_lots", "purchase_lot_items", "potential_purchases",
    "marketplaces", "marketplace_fee_rules", "customers", "sales", "sale_items",
    "suppliers", "supplier_products", "goals", "release_calendar",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [t for t in NEW_TABLES if t not in existing]

    print(f"tables already present: {sorted(set(NEW_TABLES) & existing) or 'none'}")
    print(f"tables to create: {to_create or 'none'}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in to_create])
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
