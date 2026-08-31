#!/usr/bin/env python
"""One-time migration: add art_phash to catalog_cards.

The artwork-window hash is what identifies reverse holos, whose foil covers
everything except that window (see scan_ingest/identify.ART_REGION).
Base.metadata.create_all() only creates missing tables, never alters an
existing one, and this project has no migration framework.

Adding the column does not populate it. Follow with:
    .venv/bin/python -m scripts.backfill_art_phash

Run from the automation-platform directory:
    .venv/bin/python scripts/migrate_add_catalog_card_art_phash.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "automation.db"


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(catalog_cards)")}
        if "art_phash" in existing:
            print("skip (already present): art_phash")
        else:
            conn.execute("ALTER TABLE catalog_cards ADD COLUMN art_phash VARCHAR(64)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_catalog_cards_art_phash ON catalog_cards (art_phash)")
            print("added column: art_phash VARCHAR(64)")
        conn.commit()
        print("\nNow run: .venv/bin/python -m scripts.backfill_art_phash")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
