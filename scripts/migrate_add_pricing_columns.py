#!/usr/bin/env python
"""One-time migration: add pricing columns to the existing captured_cards
table. Base.metadata.create_all() only creates missing tables, it never
alters an existing one -- this project has no migration framework (Alembic
etc), so schema changes to an already-live table need an explicit ALTER
TABLE step like this rather than just editing the SQLAlchemy model.

Run from the automation-platform directory:
    .venv/bin/python scripts/migrate_add_pricing_columns.py
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "automation.db"

NEW_COLUMNS = [
    ("suggested_price", "NUMERIC(10, 2)"),
    ("price_source", "VARCHAR(64)"),
    ("price_checked_at", "DATETIME"),
    ("price_lookup_error", "VARCHAR(512)"),
    ("approved_price", "NUMERIC(10, 2)"),
    ("bundle_id", "VARCHAR(36)"),
    # SQLAlchemy's Enum column stores/queries by the Python enum member's
    # *name* (e.g. "NOT_PRICED"), not its .value ("not_priced") -- got this
    # wrong on the first pass of this script, which silently broke every
    # migrated row's pricing lookup (fixed live via a follow-up UPDATE).
    ("pricing_status", "VARCHAR(21) NOT NULL DEFAULT 'NOT_PRICED'"),
]


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(captured_cards)")}
        for name, coltype in NEW_COLUMNS:
            if name in existing:
                print(f"skip (already present): {name}")
                continue
            conn.execute(f"ALTER TABLE captured_cards ADD COLUMN {name} {coltype}")
            print(f"added column: {name} {coltype}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_captured_cards_bundle_id ON captured_cards (bundle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_captured_cards_pricing_status ON captured_cards (pricing_status)")
        conn.commit()
        print("\nDone.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
