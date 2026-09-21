"""One-off: undo scripts/import_collector_export.py entirely.

That script is the only place InventoryStatus.PERSONAL_COLLECTION is ever
set (confirmed by grep -- personal_collection.py only reads it, never
writes it), so every InventoryItem with that status is one of its rows,
regardless of whether scripts/heal_inventory_card_id_links.py has since
linked it onto a real card_id.

Removes, in order:
  1. The CardPrice(source="collector_import") rows it created.
  2. Every InventoryItem with status=PERSONAL_COLLECTION.
  3. The CatalogItem/TcgCard rows that exist *only* for this import --
     "pc-"-prefixed ids (the script's own collision-proof prefix for a
     card it couldn't match to a real catalogue entry), and only once no
     InventoryItem references them any more.

What this does NOT touch: a row whose catalog_item_id *did* find a real
catalogue match (e.g. a real pokemontcg.io/tcgcsv-cached card) -- those
CatalogCard/CatalogItem/TcgCard rows are shared catalogue data other
things use (future scans of the same card, its price history, any other
holding of it), so only the ownership record (the InventoryItem row) is
removed, never the card definition.

Confirmed before writing this: zero of the affected rows are referenced
by any shop_listings row, so nothing is left dangling.

    .venv/bin/python -m scripts.remove_collector_export_import --dry-run
    .venv/bin/python -m scripts.remove_collector_export_import
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogItem, InventoryItem, InventoryStatus, TcgCard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        items = list(session.scalars(select(InventoryItem).where(InventoryItem.status == InventoryStatus.PERSONAL_COLLECTION)))
        prices = list(session.scalars(select(CardPrice).where(CardPrice.source == "collector_import")))
        exclusive_ids = {i.catalog_item_id for i in items if i.catalog_item_id and i.catalog_item_id.startswith("pc-")}

        total_qty = sum(i.quantity for i in items)
        print(f"{len(items)} InventoryItem row(s), {total_qty} total copies")
        print(f"{len(prices)} CardPrice(source=collector_import) row(s)")
        print(f"{len(exclusive_ids)} catalog entr(ies) exclusive to this import (will also be removed)")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        for price in prices:
            session.delete(price)
        for item in items:
            session.delete(item)
        session.flush()  # so the "still referenced?" check below sees the deletions

        removed_catalog = 0
        for catalog_item_id in exclusive_ids:
            still_used = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id == catalog_item_id))
            if still_used is not None:
                continue  # defensive -- shouldn't happen, but never delete a catalog row something still points at
            tcg_card = session.get(TcgCard, catalog_item_id)
            if tcg_card is not None:
                session.delete(tcg_card)
            catalog_item = session.get(CatalogItem, catalog_item_id)
            if catalog_item is not None:
                session.delete(catalog_item)
            removed_catalog += 1

        session.commit()
        print(f"\nDone: removed {len(items)} inventory row(s), {len(prices)} price row(s), "
              f"{removed_catalog} exclusive catalogue entr(ies).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
