"""One-off: reconcile the 9 SDBH cards commit_sdbh_sheet_112823.py added,
onto the real catalogue scan_ingest/cardotaku_catalog.py built shortly
after that script ran.

commit_sdbh_sheet_112823.py hand-typed each card from the photo and
created fresh CatalogItem/TcgCard rows under game="dragonball_heroes" --
a different key from games.py's "super_dragon_ball_heroes", with no
CardSet/phash, so these 9 were invisible to the price source, the games
registry, and future-scan dedup.

This does two things:
  1. Every one of the 9 gets its TcgCard.game corrected to
     "super_dragon_ball_heroes" -- cheap and safe regardless of whether a
     real catalogue match exists.
  2. Card 4 (Son Gohan: Boyhood (SSJ2), set_code UGM5-002) is a genuine
     match in the cardotaku catalogue (sdbh-ugm5-002) -- repointed onto
     it so it gets a reference photo, pHash, and pricing, and its
     now-unused ad hoc CatalogItem/TcgCard rows are dropped. The scanned
     photo of the actual owned copy (not the catalogue's stock photo) is
     kept as InventoryItem.scan_image_path.

The other 8 stay hand-catalogued (no code, or a code cardotaku doesn't
stock -- ABS-11 isn't in that catalogue) rather than force a low-confidence
pHash guess onto owned inventory.

    .venv/bin/python -m scripts.reconcile_sdbh_sheet_112823 --dry-run
    .venv/bin/python -m scripts.reconcile_sdbh_sheet_112823
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, CatalogItem, InventoryItem, TcgCard

OLD_GAME = "dragonball_heroes"
NEW_GAME = "super_dragon_ball_heroes"
REAL_MATCH = ("1a6169b3-c7ba-463a-97ba-5e6f2a77eb3f", "sdbh-ugm5-002")  # (ad hoc catalog_item_id, real CatalogCard id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        rows = list(session.scalars(select(TcgCard).where(TcgCard.game == OLD_GAME)))
        if not rows:
            print(f"no rows with game={OLD_GAME!r} -- already reconciled?")
            return 0

        ad_hoc_id, real_id = REAL_MATCH
        real_card = session.get(CatalogCard, real_id)
        if real_card is None:
            raise SystemExit(f"expected catalogue card {real_id!r} not found -- has cardotaku_catalog run?")
        existing_holding = session.scalar(
            select(InventoryItem).where(InventoryItem.card_id == real_id, InventoryItem.variant == "normal", InventoryItem.condition == "Near Mint")
        )
        if existing_holding is not None:
            raise SystemExit(f"{real_id!r} already has an inventory row -- merge manually, this script assumes none exists")

        print(f"{len(rows)} row(s) with game={OLD_GAME!r}:")
        for row in rows:
            tag = " <- repoint onto real catalogue" if row.catalog_item_id == ad_hoc_id else ""
            print(f"  {row.catalog_item_id}  {row.character or row.set_code or row.number}{tag}")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        for row in rows:
            row.game = NEW_GAME

        item = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id == ad_hoc_id))
        item.catalog_item_id = real_id
        item.card_id = real_id
        # scan_image_path is left alone: it's the actual scanned photo of
        # the owned copy, not the catalogue's stock photo -- keep it.

        session.delete(session.get(TcgCard, ad_hoc_id))
        session.delete(session.get(CatalogItem, ad_hoc_id))
        session.commit()

        print(f"\nRepointed inventory item {item.id} onto {real_id} ({real_card.name}); "
              f"dropped its ad hoc catalog rows. Corrected game on the other {len(rows) - 1}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
