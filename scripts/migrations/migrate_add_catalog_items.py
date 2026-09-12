#!/usr/bin/env python
"""One-time migration: introduce the generic catalogue (Phase 1, Module 1 --
see ARCHITECTURE.md and DATABASE.md) alongside the existing Pokémon-specific
tables, without touching them.

What this does, in order:
  1. Creates the new tables (catalog_items, tcg_cards, sealed_products,
     collectible_products, storage_locations) via Base.metadata.create_all
     -- safe, since create_all only ever adds *missing* tables.
  2. Adds new nullable columns to the existing inventory_items table via
     ALTER TABLE (create_all cannot alter an existing table -- this project
     has no migration framework, see migrate_add_pricing_columns.py for the
     established pattern this follows).
  3. Backfills: one catalog_items + tcg_cards row per existing catalog_cards
     row (item_type=TCG_CARD, game='pokemon'), reusing catalog_cards.id as
     the new id so every existing foreign key into catalog_cards.id keeps
     resolving unchanged. Then backfills inventory_items.catalog_item_id
     from its existing card_id.

Nothing existing is dropped, renamed, or altered. card_id and catalog_cards
remain exactly as they are and keep being what scan_ingest reads and
writes -- catalog_item_id is additive, read by nothing yet.

    .venv/bin/python -m scripts.migrate_add_catalog_items --dry-run
    .venv/bin/python -m scripts.migrate_add_catalog_items
"""

import argparse
import sqlite3
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import Base, engine
# Importing the models module is what actually registers every table class
# (CatalogItem, TcgCard, ...) onto Base.metadata -- without this, Base.metadata
# is empty and create_all(engine, tables=[...]) silently creates nothing,
# which is exactly what happened on this migration's first real run.
from automation_control import models  # noqa: F401

PROJECT_DIR = Path(__file__).resolve().parent.parent

NEW_INVENTORY_COLUMNS = [
    ("catalog_item_id", "VARCHAR(64)"),
    ("storage_location_id", "VARCHAR(36)"),
    # SQLAlchemy's Enum column stores the member *name* ("AVAILABLE"), not
    # its .value ("available") -- see migrate_add_pricing_columns.py's own
    # comment on the same gotcha, hit for real on that migration.
    ("status", "VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE'"),
    ("allocated_cost_basis", "NUMERIC(10, 2)"),
    ("grading_company", "VARCHAR(64)"),
    ("certification_number", "VARCHAR(64)"),
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
    conn.row_factory = sqlite3.Row
    try:
        existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        new_tables = {"catalog_items", "tcg_cards", "sealed_products", "collectible_products", "storage_locations"} - existing_tables
        existing_inv_columns = {r[1] for r in conn.execute("PRAGMA table_info(inventory_items)")}
        missing_columns = [(n, t) for n, t in NEW_INVENTORY_COLUMNS if n not in existing_inv_columns]

        catalog_card_count = conn.execute("SELECT COUNT(*) FROM catalog_cards").fetchone()[0]
        already_migrated = conn.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0] if "catalog_items" in existing_tables else 0
        to_backfill = catalog_card_count - already_migrated

        inventory_total = conn.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        inventory_needing_link = 0
        if "catalog_item_id" in existing_inv_columns:
            inventory_needing_link = conn.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE catalog_item_id IS NULL"
            ).fetchone()[0]
        else:
            inventory_needing_link = inventory_total

        print(f"new tables to create: {sorted(new_tables) or 'none'}")
        print(f"new inventory_items columns to add: {[n for n, _ in missing_columns] or 'none'}")
        print(f"catalog_items/tcg_cards rows to backfill from catalog_cards: {to_backfill} (of {catalog_card_count} total)")
        print(f"inventory_items rows to link to catalog_item_id: {inventory_needing_link} (of {inventory_total} total)")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        # Step 1: new tables.
        Base.metadata.create_all(engine, tables=[
            t for t in Base.metadata.sorted_tables
            if t.name in {"catalog_items", "tcg_cards", "sealed_products", "collectible_products", "storage_locations"}
        ])

        # Step 2: new inventory_items columns.
        existing_inv_columns = {r[1] for r in conn.execute("PRAGMA table_info(inventory_items)")}
        for name, coltype in NEW_INVENTORY_COLUMNS:
            if name in existing_inv_columns:
                continue
            conn.execute(f"ALTER TABLE inventory_items ADD COLUMN {name} {coltype}")
            print(f"added column: inventory_items.{name} {coltype}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_inventory_items_catalog_item_id ON inventory_items (catalog_item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_inventory_items_storage_location_id ON inventory_items (storage_location_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_inventory_items_status ON inventory_items (status)")
        conn.commit()

        # Step 3a: backfill catalog_items + tcg_cards from catalog_cards,
        # one row each, reusing the same id.
        rows = conn.execute("""
            SELECT id, name, image_url, local_image_path, set_id, number, rarity, artist
            FROM catalog_cards
            WHERE id NOT IN (SELECT id FROM catalog_items)
        """).fetchall()
        for row in rows:
            conn.execute(
                "INSERT INTO catalog_items (id, item_type, name, image_url, created_at) "
                "VALUES (?, 'TCG_CARD', ?, ?, datetime('now'))",
                (row["id"], row["name"], row["local_image_path"] or row["image_url"]),
            )
            conn.execute(
                "INSERT INTO tcg_cards (catalog_item_id, game, set_id, number, character, rarity, artist, language) "
                "VALUES (?, 'pokemon', ?, ?, ?, ?, ?, 'English')",
                (row["id"], row["set_id"], row["number"], row["name"], row["rarity"], row["artist"]),
            )
        conn.commit()
        print(f"backfilled {len(rows)} catalog_items/tcg_cards row(s)")

        # Step 3b: link inventory_items.catalog_item_id from its card_id.
        result = conn.execute(
            "UPDATE inventory_items SET catalog_item_id = card_id WHERE catalog_item_id IS NULL"
        )
        conn.commit()
        print(f"linked {result.rowcount} inventory_items row(s) to catalog_item_id")

        print("\nDone.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
