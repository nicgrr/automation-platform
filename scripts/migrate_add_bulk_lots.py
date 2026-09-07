"""One-time migration: add bulk_lots -- the Bulk Master section.

Brand new table, no backfill, nothing existing touched.

    .venv/bin/python -m scripts.migrate_add_bulk_lots --dry-run
    .venv/bin/python -m scripts.migrate_add_bulk_lots
"""

import argparse

from sqlalchemy import inspect

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine

NEW_TABLES = ["bulk_lots"]


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
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
