"""One-off: reconcile the 7 remaining commit_sdbh_sheet_112823.py cards
that turned out to be Dragon Ball Super Divers, not Super Dragon Ball
Heroes -- identified 2026-09-22 via eBay/Google Lens reverse image search
(see reconcile_sdbh_sheet_112823.py for the 8th, UGM5-002, which really
was SDBH and was already reconciled).

Repoints each ad hoc InventoryItem onto its real cardotaku_catalog.py
CatalogCard (now that scripts/cache_cardotaku_scan_catalog.py --game
dragon_ball_super_divers has cached them), so each gets a reference
photo, pHash, and pricing; the scanned photo of the actual owned copy is
kept as InventoryItem.scan_image_path rather than overwritten with the
catalogue's stock photo. Their now-unused ad hoc CatalogItem/TcgCard rows
are dropped.

    .venv/bin/python -m scripts.reconcile_dbsd_sheet_112823 --dry-run
    .venv/bin/python -m scripts.reconcile_dbsd_sheet_112823
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, CatalogItem, InventoryItem, TcgCard

# (ad hoc catalog_item_id from commit_sdbh_sheet_112823.py, real CatalogCard id, card name -- for the printout)
MATCHES = [
    ("2680862b-7a98-416a-bb5a-f314484b16c2", "dbsd-sdv11-pur1", "Son Goku: BR"),
    ("62665fd8-d31c-45f8-a255-71be4baafeaa", "dbsd-sdv11-pur3", "Trunks: Future"),
    ("64667048-cb4c-43e6-a50c-d108cc8b8ea4", "dbsd-sdv10-pur1", "Son Goku"),
    ("554570e4-3f38-4d52-ac48-20f89e4d5aef", "dbsd-sdv1-039", "Whis"),
    ("7c8e275a-2e59-405b-ab1e-c253d6eae500", "dbsd-sdv11-pur2", "Son Gohan: SH"),
    ("d5fd3c30-f602-4c44-9e46-788b895dc4c9", "dbsd-sdv8-sec2", "Broly: BR"),
    ("a98cfe30-c346-4146-98ec-f64e47ac3ea2", "dbsd-sdv11-pur4", "Piccolo"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        repointed = 0
        for ad_hoc_id, real_id, name in MATCHES:
            item = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id == ad_hoc_id))
            if item is None:
                print(f"skip {name}: no inventory row for {ad_hoc_id!r} -- already reconciled?")
                continue
            real_card = session.get(CatalogCard, real_id)
            if real_card is None:
                raise SystemExit(f"{name}: expected catalogue card {real_id!r} not found -- has the import run?")
            existing_holding = session.scalar(
                select(InventoryItem).where(InventoryItem.card_id == real_id, InventoryItem.variant == item.variant, InventoryItem.condition == item.condition)
            )
            if existing_holding is not None:
                print(f"skip {name}: {real_id!r} already has an inventory row -- merge manually")
                continue

            print(f"{name}: {ad_hoc_id} -> {real_id} ({real_card.name})")
            if args.dry_run:
                repointed += 1
                continue

            item.catalog_item_id = real_id
            item.card_id = real_id
            # scan_image_path is left alone: it's the actual scanned photo
            # of the owned copy, not the catalogue's stock photo.
            session.delete(session.get(TcgCard, ad_hoc_id))
            session.delete(session.get(CatalogItem, ad_hoc_id))
            repointed += 1

        if args.dry_run:
            print(f"\n--dry-run: {repointed} row(s) would be repointed, nothing written.")
            return 0

        session.commit()
        print(f"\nRepointed {repointed} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
