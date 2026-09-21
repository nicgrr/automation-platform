#!/usr/bin/env python
"""One-time migration: add game to card_sets.

Every existing row is a pokemontcg.io set, so the column defaults to
"pokemon". One Piece and Dragon Ball sets cached by
scan_ingest/tcgplayer_catalog.py set it explicitly.

Run from the automation-platform directory:
    .venv/bin/python scripts/migrations/migrate_add_card_set_game.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "automation.db"


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(card_sets)")}
        if "game" in existing:
            print("skip (already present): game")
        else:
            conn.execute("ALTER TABLE card_sets ADD COLUMN game VARCHAR(32) NOT NULL DEFAULT 'pokemon'")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_card_sets_game ON card_sets (game)")
            print("added column: game VARCHAR(32) DEFAULT 'pokemon'")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
