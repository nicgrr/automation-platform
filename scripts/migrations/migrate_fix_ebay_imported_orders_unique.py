#!/usr/bin/env python
"""Fix a real production bug: EbayImportedOrder.ebay_order_id was
declared `unique=True`, but the table is meant to hold one row per order
*line* (see the model's own docstring, "One row per eBay order line").
The import script (scripts/import_ebay_orders.py) inserts one row per
(ebay_order_id, sku) pair -- correct for a multi-item order -- but the
single-column unique index made every row after the first on the same
order raise a sqlite3.IntegrityError, crashing the whole run.

Confirmed against a real order (20-15191-43304, 5 line items) that hit
exactly this: the first line item was applied and committed, the second
line item's insert crashed with "UNIQUE constraint failed:
ebay_imported_orders.ebay_order_id", and ebay-order-import.timer has
been failing every 10 minutes since, leaving the other 4 line items'
ShopListings still ACTIVE and un-decremented -- a real oversell risk on
already-sold, paid-for stock.

This migration replaces the single-column unique index with a composite
unique index on (ebay_order_id, sku) -- the actual natural key of one
order line -- and keeps a plain (non-unique) index on ebay_order_id
alone so "every line of this order" lookups stay fast. Index-only change:
no table rebuild, no data loss, nothing existing is dropped except the
one wrong index.

    .venv/bin/python -m scripts.migrations.migrate_fix_ebay_imported_orders_unique --dry-run
    .venv/bin/python -m scripts.migrations.migrate_fix_ebay_imported_orders_unique
"""

import argparse
import sqlite3
from pathlib import Path

from automation_control.config import get_settings

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

OLD_UNIQUE_INDEX = "ix_ebay_imported_orders_ebay_order_id"
NEW_PLAIN_INDEX = "ix_ebay_imported_orders_ebay_order_id"
NEW_COMPOSITE_UNIQUE_INDEX = "uq_ebay_imported_orders_order_sku"


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
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        has_old_unique = OLD_UNIQUE_INDEX in indexes
        has_new_unique = NEW_COMPOSITE_UNIQUE_INDEX in indexes

        # Sanity-check the old index really is the wrong single-column
        # unique one before touching anything, in case this has already
        # been partially applied by hand.
        old_index_info = list(conn.execute(f"PRAGMA index_info({OLD_UNIQUE_INDEX!r})")) if has_old_unique else []
        old_is_single_column_unique = len(old_index_info) == 1

        print(f"old single-column unique index present: {has_old_unique} "
              f"(single-column: {old_is_single_column_unique})")
        print(f"new composite unique index already present: {has_new_unique}")

        if has_new_unique:
            print("\nnothing to do -- migration already applied.")
            return 0

        if args.dry_run:
            print("\n--dry-run: would drop the old unique index, recreate it as a plain index, "
                  "and add a unique index on (ebay_order_id, sku).")
            return 0

        if has_old_unique and old_is_single_column_unique:
            conn.execute(f"DROP INDEX {OLD_UNIQUE_INDEX}")
            print(f"dropped: {OLD_UNIQUE_INDEX} (was unique on ebay_order_id alone)")

        conn.execute(f"CREATE INDEX IF NOT EXISTS {NEW_PLAIN_INDEX} ON ebay_imported_orders (ebay_order_id)")
        print(f"created plain index: {NEW_PLAIN_INDEX}")

        conn.execute(
            f"CREATE UNIQUE INDEX {NEW_COMPOSITE_UNIQUE_INDEX} ON ebay_imported_orders (ebay_order_id, sku)"
        )
        print(f"created composite unique index: {NEW_COMPOSITE_UNIQUE_INDEX} on (ebay_order_id, sku)")

        conn.commit()
        print("\nDone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
