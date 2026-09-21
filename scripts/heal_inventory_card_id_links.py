"""One-off: heal InventoryItem.card_id for holdings that only ever got
catalog_item_id set.

Several import paths (scripts/import_collector_export.py,
scripts/import_one_piece_from_optcgapi.py, and tonight's
tcgplayer_catalog.py/cardotaku_catalog.py) create a CatalogItem/TcgCard
pair without ever touching InventoryItem.card_id -- that field only gets
set by the scan-ingest commit path (commit_card). /inventory's whole tile
grid is built on card_id -> CatalogCard.set_id -> CardSet, so a holding
with card_id=None is invisible there no matter what it's worth or how
it's grouped, regardless of any game filter.

Deliberately id-convention-based, not fuzzy matching: tonight's importers
reuse the exact `_slug(game, set_code, number)` id scheme
import_one_piece_from_optcgapi.py established specifically so a later
catalogue run enriches an existing row rather than duplicating it (see
tcgplayer_catalog.py's module docstring) -- so wherever catalog_item_id
already equals a real catalog_cards.id, it's the same physical card
definition, not a guess.

Two real risks, both handled by skipping rather than guessing:
  1. `(card_id, variant, condition)` is uniquely constrained. Setting
     card_id to a value another row already holds (same card+variant+
     condition) would collide -- and every real collision found here is
     a PERSONAL_COLLECTION-status row (a personal keeper, imported from a
     collector export) colliding with an unrelated AVAILABLE-status row
     (for-sale scanned stock). Merging them would silently fold a
     personal keeper into sellable inventory, which is not this script's
     call to make -- those are left untouched (still visible on
     /personal-collection, just not linked).
  2. Some catalog_item_ids have no matching catalog_cards row at all (a
     "pc-" prefixed personal-collection item with no clean catalogue
     match, or a hand-catalogued SDBH card -- see
     reconcile_sdbh_sheet_112823.py). Nothing to heal onto; left alone.

    .venv/bin/python -m scripts.heal_inventory_card_id_links --dry-run
    .venv/bin/python -m scripts.heal_inventory_card_id_links
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, InventoryItem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        candidates = list(session.scalars(select(InventoryItem).where(InventoryItem.card_id.is_(None))))
        card_ids = {i.catalog_item_id for i in candidates if i.catalog_item_id}
        real_cards = {c.id for c in session.scalars(select(CatalogCard).where(CatalogCard.id.in_(card_ids)))}

        existing_identities = {
            (i.card_id, i.variant, i.condition)
            for i in session.scalars(select(InventoryItem).where(InventoryItem.card_id.is_not(None)))
        }

        healed = skipped_no_match = skipped_collision = 0
        for item in candidates:
            if item.catalog_item_id not in real_cards:
                skipped_no_match += 1
                continue
            identity = (item.catalog_item_id, item.variant, item.condition)
            if identity in existing_identities:
                skipped_collision += 1
                continue
            if not args.dry_run:
                item.card_id = item.catalog_item_id
            existing_identities.add(identity)  # a second candidate with the same identity would now collide too
            healed += 1

        print(f"{healed} row(s) {'would be ' if args.dry_run else ''}healed (card_id set from catalog_item_id)")
        print(f"{skipped_collision} row(s) skipped -- would collide with an existing (card_id, variant, condition), left for manual review")
        print(f"{skipped_no_match} row(s) skipped -- no matching catalog_cards row exists yet")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
