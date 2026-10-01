#!/usr/bin/env python
"""Add the retailer-program goal tracker and seed Gameology Guild.

Purely additive: creates new tables and seed rows. It does not alter or
delete any existing catalogue, inventory, transaction or price data.

    .venv/bin/python -m scripts.migrations.migrate_add_retailer_goals --dry-run
    .venv/bin/python -m scripts.migrations.migrate_add_retailer_goals
"""

import argparse

from sqlalchemy import inspect

from automation_control import models  # noqa: F401 -- register metadata
from automation_control.database import Base, SessionLocal, engine
from automation_control.retailer_goals_service import ensure_gameology_program

NEW_TABLES = [
    "retailers",
    "retailer_programs",
    "retailer_milestones",
    "retailer_budgets",
    "retailer_premium_configs",
    "retailer_products",
    "retailer_price_snapshots",
    "retailer_purchases",
    "retailer_fund_entries",
    "retailer_notification_preferences",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [name for name in NEW_TABLES if name not in existing]
    print(f"tables already present: {sorted(set(NEW_TABLES) & existing) or 'none'}")
    print(f"tables to create: {to_create or 'none'}")
    print("seed: Gameology Guild, $1k/$5k milestones, budget defaults, editable starter queue")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(
        engine,
        tables=[table for table in Base.metadata.sorted_tables if table.name in to_create],
    )
    with SessionLocal() as session:
        program = ensure_gameology_program(session)
        print(f"seeded program: {program.name} ({program.slug})")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
