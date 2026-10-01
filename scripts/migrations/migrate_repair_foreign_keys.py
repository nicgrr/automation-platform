"""Repair legacy SQLite foreign keys and the rows they exposed.

The inventory-items rebuild in ``migrate_relax_inventory_card_id`` left
three child tables pointing at SQLite's temporary ``inventory_items_old``
name.  Separately, ``card_prices.card_id`` outgrew its original Pokemon-only
``catalog_cards`` id-space and now stores generic ``catalog_items`` ids.
SQLite did not report either problem during normal use because foreign-key
enforcement was not enabled on application connections.

This migration is deliberately loss-minimising:

* valid generic catalogue prices are preserved;
* only price rows whose id exists in neither current catalogue are removed;
* historical foil labels are relinked to the replacement holding when an
  exact card/confirmed-variant match exists, otherwise their optional
  inventory link is cleared while the training sample is retained;
* active shop listings are relinked to the sole current holding for the
  same catalogue item, with their displayed condition updated to its real
  variant;
* affected child tables are rebuilt from the current SQLAlchemy metadata so
  their targets and ON DELETE policies match the models.

Stop both EzBay writers and take a SQLite backup before applying::

    sudo systemctl stop ezbay.service scan-ingest.service
    sqlite3 automation.db ".backup backups/automation-pre-fk-repair.db"
    .venv/bin/python -m scripts.migrations.migrate_repair_foreign_keys --dry-run
    .venv/bin/python -m scripts.migrations.migrate_repair_foreign_keys
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable

from sqlalchemy import inspect, text

from automation_control import models  # noqa: F401 - registers all tables
from automation_control.database import Base, engine

REBUILD_TABLES = (
    "card_prices",
    "foil_labels",
    "sale_items",
    "whatnot_show_items",
    "shop_listings",
)

VARIANT_LABELS = {
    "NORMAL": "Normal",
    "HOLO": "Holo",
    "REVERSE_HOLO": "Reverse Holo",
}


def _rows(conn, statement: str) -> list[dict]:
    return [dict(row) for row in conn.execute(text(statement)).mappings()]


def _replacement_inventory(conn, *, catalog_item_id: str, variant: str | None = None) -> str | None:
    clauses = ["catalog_item_id = :catalog_item_id"]
    params: dict[str, str] = {"catalog_item_id": catalog_item_id}
    if variant:
        clauses.append("upper(variant) = :variant")
        params["variant"] = variant.upper()
    rows = conn.execute(
        text(f"SELECT id FROM inventory_items WHERE {' AND '.join(clauses)} ORDER BY id"), params
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None


def _rebuild_table(conn, table_name: str) -> None:
    """Recreate one child table from current metadata, preserving its rows."""
    legacy = f"{table_name}__fk_legacy"
    if inspect(conn).has_table(legacy):
        raise RuntimeError(f"refusing to overwrite leftover migration table {legacy}")

    conn.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{legacy}"'))
    named_indexes = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:table "
        "AND name NOT LIKE 'sqlite_autoindex%'"
    ), {"table": legacy}).scalars().all()
    for index_name in named_indexes:
        conn.execute(text(f'DROP INDEX "{index_name}"'))

    Base.metadata.tables[table_name].create(conn)
    old_columns = {column["name"] for column in inspect(conn).get_columns(legacy)}
    new_columns = [
        column.name for column in Base.metadata.tables[table_name].columns
        if column.name in old_columns
    ]
    names = ", ".join(f'"{name}"' for name in new_columns)
    conn.execute(text(f'INSERT INTO "{table_name}" ({names}) SELECT {names} FROM "{legacy}"'))
    conn.execute(text(f'DROP TABLE "{legacy}"'))


def _format_counts(items: Iterable[tuple[str, int]]) -> str:
    return ", ".join(f"{name}={count}" for name, count in items)


def migrate(*, dry_run: bool) -> None:
    with engine.connect() as conn:
        missing_prices = conn.execute(text("""
            SELECT COUNT(*) FROM card_prices AS price
            LEFT JOIN catalog_items AS item ON item.id = price.card_id
            WHERE item.id IS NULL
        """)).scalar_one()
        generic_prices = conn.execute(text("""
            SELECT COUNT(*) FROM card_prices AS price
            JOIN catalog_items AS item ON item.id = price.card_id
            LEFT JOIN catalog_cards AS card ON card.id = price.card_id
            WHERE card.id IS NULL
        """)).scalar_one()
        foil_variants_to_backfill = conn.execute(text(
            "SELECT COUNT(*) FROM foil_labels WHERE actual_variant IS NULL"
        )).scalar_one()
        orphan_foil = _rows(conn, """
            SELECT label.id, label.inventory_item_id, label.card_id,
                   COALESCE(
                       label.actual_variant,
                       CASE WHEN label.confirmed THEN label.suggested_variant ELSE label.filed_variant END
                   ) AS actual_variant,
                   label.filed_variant
            FROM foil_labels AS label
            LEFT JOIN inventory_items AS inventory ON inventory.id = label.inventory_item_id
            WHERE label.inventory_item_id IS NOT NULL AND inventory.id IS NULL
        """)
        orphan_listings = _rows(conn, """
            SELECT listing.id, listing.inventory_item_id, listing.catalog_item_id
            FROM shop_listings AS listing
            LEFT JOIN inventory_items AS inventory ON inventory.id = listing.inventory_item_id
            WHERE listing.inventory_item_id IS NOT NULL AND inventory.id IS NULL
        """)

        foil_relinks: dict[str, str] = {}
        foil_detach: list[str] = []
        for label in orphan_foil:
            variant = label["actual_variant"]
            replacement = _replacement_inventory(
                conn, catalog_item_id=label["card_id"], variant=variant
            ) if variant else None
            if replacement:
                foil_relinks[label["id"]] = replacement
            else:
                foil_detach.append(label["id"])

        listing_relinks: dict[str, tuple[str, str]] = {}
        for listing in orphan_listings:
            candidates = conn.execute(text("""
                SELECT id, variant FROM inventory_items
                WHERE catalog_item_id = :catalog_item_id ORDER BY id
            """), {"catalog_item_id": listing["catalog_item_id"]}).mappings().all()
            if len(candidates) != 1:
                raise RuntimeError(
                    f"shop listing {listing['id']} has {len(candidates)} replacement inventory candidates"
                )
            listing_relinks[listing["id"]] = (candidates[0]["id"], candidates[0]["variant"])

        print(f"valid generic catalogue price rows preserved: {generic_prices}")
        print(f"price rows with no catalogue item removed: {missing_prices}")
        print(f"legacy foil verdicts backfilled: {foil_variants_to_backfill}")
        print(f"foil labels relinked: {len(foil_relinks)}; detached but preserved: {len(foil_detach)}")
        print(f"shop listings relinked: {len(listing_relinks)}")
        print("tables rebuilt: " + ", ".join(REBUILD_TABLES))
        if dry_run:
            print("\n--dry-run: nothing written.")
            return

        # The read-only planning queries above start SQLAlchemy's implicit
        # transaction.  End it before opening the single atomic write block.
        conn.rollback()
        # This database already contains invalid legacy references, and one
        # of them names a table that no longer exists at all.  SQLite checks
        # that broken parent even when updating a different column, so the
        # child tables cannot be repaired while enforcement is active.  Turn
        # it off for this one connection, rebuild and repair atomically, run
        # `foreign_key_check`, then turn enforcement straight back on.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError("could not disable SQLite foreign-key enforcement for repair")
        conn.commit()
        # `shop_listings` is referenced by three order/eBay tables.  While
        # it is temporarily renamed, keep SQLite from rewriting those
        # inbound definitions to the temporary name; they must continue to
        # refer to the newly-created `shop_listings` table.
        conn.exec_driver_sql("PRAGMA legacy_alter_table=ON")
        conn.commit()
        if conn.exec_driver_sql("PRAGMA legacy_alter_table").scalar_one() != 1:
            raise RuntimeError("could not enable legacy ALTER TABLE semantics for repair")
        conn.commit()

        # Python's sqlite3 legacy transaction mode does not begin a real
        # SQLite transaction for DDL.  An explicit BEGIN is essential here:
        # otherwise an INSERT failure after ALTER TABLE could leave half a
        # rebuild committed despite SQLAlchemy rolling its transaction back.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            # Older verdicts predate the explicit three-way actual_variant
            # field.  In that schema, confirmed meant "the suggestion was
            # right" and unconfirmed meant "the filed variant was right".
            conn.execute(text("""
                UPDATE foil_labels
                SET actual_variant = CASE
                    WHEN confirmed THEN suggested_variant ELSE filed_variant
                END
                WHERE actual_variant IS NULL
            """))

            # Rebuild first: the legacy foil-label column is NOT NULL and
            # several old constraints name `inventory_items_old`, so the
            # row-level repairs are impossible against the old definitions.
            # Enforcement is off on this connection, allowing the invalid
            # rows to survive the copy just long enough to be fixed below.
            for table_name in REBUILD_TABLES:
                _rebuild_table(conn, table_name)

            conn.execute(text("""
                DELETE FROM card_prices
                WHERE NOT EXISTS (
                    SELECT 1 FROM catalog_items WHERE catalog_items.id = card_prices.card_id
                )
            """))
            for label_id, inventory_id in foil_relinks.items():
                conn.execute(text(
                    "UPDATE foil_labels SET inventory_item_id=:inventory_id WHERE id=:id"
                ), {"inventory_id": inventory_id, "id": label_id})
            for label_id in foil_detach:
                conn.execute(text(
                    "UPDATE foil_labels SET inventory_item_id=NULL WHERE id=:id"
                ), {"id": label_id})
            for listing_id, (inventory_id, variant) in listing_relinks.items():
                condition = conn.execute(text(
                    "SELECT condition FROM inventory_items WHERE id=:id"
                ), {"id": inventory_id}).scalar_one()
                display_condition = f"{condition} · {VARIANT_LABELS.get(variant, variant.replace('_', ' ').title())}"
                conn.execute(text("""
                    UPDATE shop_listings
                    SET inventory_item_id=:inventory_id, condition=:condition
                    WHERE id=:id
                """), {"inventory_id": inventory_id, "condition": display_condition, "id": listing_id})

            violations = conn.execute(text("PRAGMA foreign_key_check")).all()
            if violations:
                counts: dict[str, int] = {}
                for table_name, *_ in violations:
                    counts[table_name] = counts.get(table_name, 0) + 1
                raise RuntimeError("foreign-key violations remain: " + _format_counts(sorted(counts.items())))
            conn.commit()
        except Exception:
            conn.rollback()
            conn.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
            raise

        conn.exec_driver_sql("PRAGMA legacy_alter_table=OFF")
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
        if conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 1:
            raise RuntimeError("could not restore SQLite foreign-key enforcement")
        conn.commit()

    print("\nDone. PRAGMA foreign_key_check is clean.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    migrate(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
