#!/usr/bin/env python
"""Create the additive cross-market listing and alert-ledger tables."""

import argparse
from sqlalchemy import inspect

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine

TABLES = ["market_listings", "market_alert_deliveries"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    existing = set(inspect(engine).get_table_names())
    missing = [name for name in TABLES if name not in existing]
    print(f"tables to create: {missing or 'none'}")
    if not args.dry_run:
        Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in missing])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
