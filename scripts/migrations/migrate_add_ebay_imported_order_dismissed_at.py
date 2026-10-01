#!/usr/bin/env python
"""One-time migration: add dismissed_at to ebay_imported_orders.

This is purely the operator's own "I'm done looking at this" marker for
the /ebay-sales page -- separate from applied_at, which is the sync
poller's own idempotency marker (has stock actually been decremented)
and must never be repurposed for this. Set once a human marks an order
complete or discards it from the active view; the underlying row is
never deleted, matching this table's own append-only-ledger design (see
EbayImportedOrder's docstring).

Base.metadata.create_all() only creates missing tables, never alters an
existing one, and this project has no migration framework.

Run from the automation-platform directory:
    .venv/bin/python scripts/migrations/migrate_add_ebay_imported_order_dismissed_at.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "automation.db"


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} does not exist, nothing to migrate.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(ebay_imported_orders)")}
        if "dismissed_at" in existing:
            print("skip (already present): dismissed_at")
        else:
            conn.execute("ALTER TABLE ebay_imported_orders ADD COLUMN dismissed_at TIMESTAMP")
            print("added column: dismissed_at TIMESTAMP")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
