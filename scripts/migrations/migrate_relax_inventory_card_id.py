"""One-time migration: make inventory_items.card_id nullable so a non-TCG
collectible (Sonny Angel, Smiski -- no catalog_cards row at all) can have
real ownership tracking via catalog_item_id instead.

SQLite can't ALTER a NOT NULL column away, so this rebuilds the table:
rename the existing table aside, create a fresh one from the current
(already-updated) model definition, copy every row across unchanged, then
drop the old one. Row count and every id are preserved exactly -- nothing
else references this table by anything but id, so no other table needs
touching.

scan-ingest.service writes to this table continuously and MUST be stopped
before running this for real:

    sudo systemctl stop scan-ingest.service
    .venv/bin/python -m scripts.migrate_relax_inventory_card_id --dry-run
    .venv/bin/python -m scripts.migrate_relax_inventory_card_id
    sudo systemctl start scan-ingest.service
"""

import argparse

from sqlalchemy import inspect, text

from automation_control import models  # noqa: F401  (registers tables on Base.metadata)
from automation_control.database import Base, engine

COLUMNS = [
    "id", "card_id", "variant", "condition", "quantity", "scan_image_path",
    "session_id", "notes", "added_at", "updated_at", "catalog_item_id",
    "storage_location_id", "status", "allocated_cost_basis", "grading_company",
    "certification_number",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    insp = inspect(engine)
    card_id_col = next(c for c in insp.get_columns("inventory_items") if c["name"] == "card_id")
    if card_id_col["nullable"]:
        print("card_id is already nullable -- nothing to do.")
        return 0

    with engine.connect() as conn:
        before_count = conn.execute(text("SELECT COUNT(*) FROM inventory_items")).scalar_one()
    print(f"inventory_items currently has {before_count} rows; card_id is NOT NULL.")
    print("plan: rename table aside, recreate from current model (card_id nullable), copy all rows, drop old table.")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    column_list = ", ".join(COLUMNS)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE inventory_items RENAME TO inventory_items_old"))
            # Renaming a table does NOT rename its own explicitly-named indexes
            # (only the auto-generated ones for PRIMARY KEY / UNIQUE) -- they'd
            # collide by name with the fresh indexes the new table is about to
            # get, so drop them here. The autoindexes go away on their own when
            # inventory_items_old is dropped below.
            named_indexes = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='inventory_items_old' "
                "AND name NOT LIKE 'sqlite_autoindex%'"
            )).scalars().all()
            for index_name in named_indexes:
                conn.execute(text(f"DROP INDEX {index_name}"))

            Base.metadata.tables["inventory_items"].create(conn)
            conn.execute(text(f"INSERT INTO inventory_items ({column_list}) SELECT {column_list} FROM inventory_items_old"))
            after_count = conn.execute(text("SELECT COUNT(*) FROM inventory_items")).scalar_one()
            if after_count != before_count:
                raise RuntimeError(f"row count mismatch after copy: before={before_count} after={after_count} -- aborting")
            conn.execute(text("DROP TABLE inventory_items_old"))
    except Exception:
        print("\nMIGRATION FAILED mid-way. Attempting automatic recovery...")
        with engine.begin() as conn:
            tables = set(conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars().all())
            if "inventory_items_old" in tables:
                if "inventory_items" in tables:
                    conn.execute(text("DROP TABLE inventory_items"))
                conn.execute(text("ALTER TABLE inventory_items_old RENAME TO inventory_items"))
                print("Recovered: inventory_items_old restored as inventory_items. Original data is intact. Re-run this script after investigating the failure above.")
            else:
                print("inventory_items_old no longer exists -- recovery could not run automatically. "
                      "Restore from the pre-migration backup before doing anything else.")
        raise

    print(f"\nDone. inventory_items now has {after_count} rows, card_id is nullable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
