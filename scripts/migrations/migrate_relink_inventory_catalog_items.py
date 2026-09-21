"""One-time repair: give every inventory row its catalog_item_id back.

migrate_add_catalog_items.py added `inventory_items.catalog_item_id` and
backfilled it from `card_id`, on the understanding (written into
InventoryItem's own docstring) that `card_id` would stay the source of
truth "until every reader is moved over to catalog_item_id in a later,
separate change".

The readers moved. The writers didn't. `scan_ingest/commit.py` and
`inventory_review.py` kept creating rows with `card_id` only, so every
card committed after that migration has `catalog_item_id = NULL` -- and
that is the id `card_prices` is keyed by (import_ebay_prices.py writes
`card_id=item.id` from a CatalogItem), the id `/inventory` and the
dashboard gauge value holdings by, and the id the eBay importer's "what do
I own" query selects targets with. An unlinked row is therefore not an
unpriced card: it is a card no price can reach and no pricing run will
ever pick up, counted in the denominator of "71% priced" and valued at
nothing.

Both write paths now set the link (and heal an unlinked row when the same
card is scanned again), so this script is for the rows already written.

The repair is `catalog_item_id = card_id`: catalog_items ids are a
superset of catalog_cards ids by construction, 20,480 of 20,481 shared.
Any row whose card_id has no catalog_items row is left alone and
reported -- a dangling id would be worse than a null, since SQLite has
foreign keys switched off here and nothing downstream would catch it.

scan-ingest.service writes to this table continuously and should be
stopped first:

    sudo systemctl stop scan-ingest.service
    .venv/bin/python -m scripts.migrations.migrate_relink_inventory_catalog_items --dry-run
    .venv/bin/python -m scripts.migrations.migrate_relink_inventory_catalog_items
    sudo systemctl start scan-ingest.service
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, InventoryItem

BACKUP_DIR = Path("backups")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true", help="skip writing backups/ JSON (dry runs never write one)")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        unlinked = session.scalars(
            select(InventoryItem).where(InventoryItem.catalog_item_id.is_(None))
        ).all()
        if not unlinked:
            print("every inventory row already has a catalog_item_id -- nothing to do.")
            return 0

        known = {row[0] for row in session.execute(select(CatalogItem.id))}
        repairable = [i for i in unlinked if i.card_id and i.card_id in known]
        unresolved = [i for i in unlinked if not (i.card_id and i.card_id in known)]

        print(f"inventory rows with no catalog_item_id: {len(unlinked)}")
        print(f"  repairable (card_id is a catalog_items id): {len(repairable)}")
        print(f"  left alone (card_id resolves nowhere):      {len(unresolved)}")
        for item in unresolved:
            print(f"    {item.id}  card_id={item.card_id!r}  qty={item.quantity}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        # The rows as they stood, so the repair can be undone row by row
        # without restoring the whole database.
        if not args.no_backup:
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
            path = BACKUP_DIR / f"inventory-catalog-relink-{stamp}.json"
            path.write_text(json.dumps(
                [{"id": i.id, "card_id": i.card_id, "catalog_item_id": None,
                  "variant": i.variant.value, "condition": i.condition, "quantity": i.quantity}
                 for i in unlinked], indent=2))
            print(f"\nbackup written: {path}")

        for item in repairable:
            item.catalog_item_id = item.card_id
        session.commit()
        print(f"relinked {len(repairable)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
