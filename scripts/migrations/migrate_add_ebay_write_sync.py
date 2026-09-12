#!/usr/bin/env python
"""One-time migration: schema for eBay write-sync (Phase 1 of the two-way
storefront <-> eBay stock sync -- see the plan this came from for the full
picture). Purely additive:

  1. Creates ebay_listing_links, ebay_imported_orders, ebay_category_mappings
     via Base.metadata.create_all -- safe, only ever adds *missing* tables.
  2. Adds one new nullable column to the existing ebay_credentials table via
     ALTER TABLE (create_all cannot alter an existing table -- same pattern
     migrate_add_catalog_items.py already established).

Nothing existing is dropped, renamed, or altered. No behavior changes: no
code path reads or writes these tables yet until later phases wire them up.

    .venv/bin/python -m scripts.migrate_add_ebay_write_sync --dry-run
    .venv/bin/python -m scripts.migrate_add_ebay_write_sync
"""

import argparse
import sqlite3
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import Base, engine
# Importing the models module registers every table class (EbayListingLink,
# ...) onto Base.metadata -- without this, Base.metadata is empty and
# create_all(engine, tables=[...]) silently creates nothing.
from automation_control import models  # noqa: F401

PROJECT_DIR = Path(__file__).resolve().parent.parent

NEW_TABLES = ["ebay_listing_links", "ebay_imported_orders", "ebay_category_mappings"]
NEW_CREDENTIAL_COLUMNS = [
    ("last_order_sync_at", "DATETIME"),
]


def _db_path() -> Path:
    url = get_settings().automation_database_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise SystemExit(f"expected a sqlite:/// URL, got {url!r} -- this script is SQLite-specific")
    return (PROJECT_DIR / url[len(prefix):]).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    db_path = _db_path()
    if not db_path.exists():
        raise SystemExit(f"{db_path} does not exist, nothing to migrate")

    conn = sqlite3.connect(db_path)
    try:
        existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        to_create = [t for t in NEW_TABLES if t not in existing_tables]

        existing_cred_columns = {r[1] for r in conn.execute("PRAGMA table_info(ebay_credentials)")}
        missing_columns = [(n, t) for n, t in NEW_CREDENTIAL_COLUMNS if n not in existing_cred_columns]

        print(f"new tables to create: {to_create or 'none'}")
        print(f"new ebay_credentials columns to add: {[n for n, _ in missing_columns] or 'none'}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        if to_create:
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in to_create])
            print(f"created {len(to_create)} table(s)")

        for name, coltype in missing_columns:
            conn.execute(f"ALTER TABLE ebay_credentials ADD COLUMN {name} {coltype}")
            print(f"added column: ebay_credentials.{name} {coltype}")
        conn.commit()

        print("\nDone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
