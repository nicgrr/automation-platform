#!/usr/bin/env python
"""One-time migration: add the eBay bulk-export duplicate-prevention ledger.

Purely additive: creates ``ebay_export_batches`` and
``ebay_export_items`` when missing. No existing table, row, or column is
changed.

    .venv/bin/python -m scripts.migrations.migrate_add_ebay_export_ledger --dry-run
    .venv/bin/python -m scripts.migrations.migrate_add_ebay_export_ledger
"""

import argparse

from sqlalchemy import inspect

from automation_control import models  # noqa: F401 -- registers model tables
from automation_control.database import Base, engine

NEW_TABLES = ["ebay_export_batches", "ebay_export_items"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [name for name in NEW_TABLES if name not in existing]
    print(f"tables to create: {to_create or 'none'}")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(
        engine,
        tables=[table for table in Base.metadata.sorted_tables if table.name in to_create],
    )
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
