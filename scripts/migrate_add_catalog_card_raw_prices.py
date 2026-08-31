#!/usr/bin/env python
"""One-time migration: add the raw_prices column to the existing
catalog_cards table (see automation_control/scan_ingest/pricing.py).
Base.metadata.create_all() only creates missing tables, it never alters an
existing one -- this project has no migration framework (Alembic etc), so
schema changes to an already-live table need an explicit ALTER TABLE step
like this rather than just editing the SQLAlchemy model.

Adding the column alone does not populate it for already-cached sets --
re-run `scan-ingest cache-set --set-id <id> --force` for each set you want
pricing data backfilled into (no image re-download needed, but this does
re-hit the API for the card list).

Run from the automation-platform directory:
    .venv/bin/python scripts/migrate_add_catalog_card_raw_prices.py
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "automation.db"


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(catalog_cards)")}
        if "raw_prices" in existing:
            print("skip (already present): raw_prices")
        else:
            conn.execute("ALTER TABLE catalog_cards ADD COLUMN raw_prices JSON")
            print("added column: raw_prices JSON")
        conn.commit()
        print("\nDone. Re-run cache-set --force per set to backfill pricing data.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
