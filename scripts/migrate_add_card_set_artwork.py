#!/usr/bin/env python
"""One-time migration: add logo_url/symbol_url to the existing card_sets
table (see automation_control/models.py's CardSet).

Base.metadata.create_all() only creates missing tables, it never alters an
existing one -- this project has no migration framework (Alembic etc), so
schema changes to an already-live table need an explicit ALTER TABLE step
like this rather than just editing the SQLAlchemy model.

Adding the columns does not populate them. Follow with:
    .venv/bin/python -m scripts.backfill_set_artwork

Run from the automation-platform directory:
    .venv/bin/python scripts/migrate_add_card_set_artwork.py
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "automation.db"

COLUMNS = {"logo_url": "VARCHAR(512)", "symbol_url": "VARCHAR(512)"}


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(card_sets)")}
        for name, sql_type in COLUMNS.items():
            if name in existing:
                print(f"skip (already present): {name}")
                continue
            conn.execute(f"ALTER TABLE card_sets ADD COLUMN {name} {sql_type}")
            print(f"added column: {name} {sql_type}")
        conn.commit()
        print("\nDone. Now run: .venv/bin/python -m scripts.backfill_set_artwork")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
