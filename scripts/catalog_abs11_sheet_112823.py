"""One-off: give the 9th sheet-20260921-192456-112823 card (Son Gohan:
Boyhood, ABS-11) a real CardSet/CatalogCard entry, so it becomes a normal
Dragon Ball set tile instead of a Misc row with no photo or price.

Nic: "I want 9 cards in the dragon ball area please, photos of them and
the prices." The other 8 already got this via reconcile_sdbh_sheet_112823.py
and reconcile_dbsd_sheet_112823.py, matching them onto a real cardotaku.com
catalogue row -- but cardotaku doesn't stock ABS-11 (checked, confirmed no
match), so there's no shop photo to borrow the way the other 8 did.

Uses the scan itself as the reference image instead: it's the only photo
that exists of this printing, and it's a real photo of the actual owned
card, not a placeholder. Same reasoning tcgplayer_catalog.py etc use images
for -- one photo, reused as both the catalogue's reference art and the
inventory item's own scan (scan_image_path is left pointing at the same
file, since there's nothing to distinguish "reference" from "your copy"
here).

Creates a new CardSet ("sdbh-abs", ABS = the "11th Anniversary Special"
promo series these all belong to per real eBay listings -- see
scripts/price_dbsd_sheet_112823.py's docstring), one CatalogCard/CatalogItem/
TcgCard sharing id "sdbh-abs-11", repoints the InventoryItem onto it, and
records the eBay pricing already surveyed for this card (low $51.43,
median $69.50, high $77.24 AUD -- see the last turn's chat, not re-derived
here). Drops the now-unused ad hoc CatalogItem/TcgCard rows
commit_sdbh_sheet_112823.py created.

    .venv/bin/python -m scripts.catalog_abs11_sheet_112823 --dry-run
    .venv/bin/python -m scripts.catalog_abs11_sheet_112823
"""

import argparse
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CardSet, CatalogCard, CatalogItem, CatalogItemType, InventoryItem, TcgCard
from automation_control.scan_ingest.catalog import _compute_phashes

AD_HOC_ITEM_ID = "c6df2146-8ca5-4d34-84c5-9545d6cf3f3a"
NEW_ID = "sdbh-abs-11"
SET_ID = "sdbh-abs"
PHOTO_PATH = Path("scan_ingest_data/media/dragonball_heroes/c6df2146-8ca5-4d34-84c5-9545d6cf3f3a.jpg")
PRICES = {"ebay_active_low": "51.43", "ebay_active_median": "69.50", "ebay_active_high": "77.24"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not PHOTO_PATH.exists():
        raise SystemExit(f"expected scan photo not found: {PHOTO_PATH}")

    with SessionLocal() as session:
        item = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id == AD_HOC_ITEM_ID))
        if item is None:
            raise SystemExit(f"no inventory row for {AD_HOC_ITEM_ID!r} -- already done?")
        if session.get(CatalogCard, NEW_ID) is not None:
            raise SystemExit(f"{NEW_ID!r} already exists -- already done?")

        phash, art_phash = _compute_phashes(PHOTO_PATH)
        print(f"Son Gohan: Boyhood (ABS-11) -> {NEW_ID}, phash={'ok' if phash else 'FAILED'}")
        print(f"  price: low ${PRICES['ebay_active_low']}, median ${PRICES['ebay_active_median']}, high ${PRICES['ebay_active_high']} AUD")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        card_set = CardSet(
            id=SET_ID, name="Super Dragon Ball Heroes 11th Anniversary Special", code="ABS",
            series="Super Dragon Ball Heroes", game="super_dragon_ball_heroes", cached_at=datetime.now(UTC),
        )
        session.add(card_set)

        card = CatalogCard(
            id=NEW_ID, set_id=SET_ID, number="ABS-11", name="Son Gohan: Boyhood",
            local_image_path=str(PHOTO_PATH), phash=phash, art_phash=art_phash,
        )
        session.add(card)
        session.add(CatalogItem(id=NEW_ID, item_type=CatalogItemType.TCG_CARD, name="Son Gohan: Boyhood"))
        session.add(TcgCard(catalog_item_id=NEW_ID, game="super_dragon_ball_heroes", set_id=SET_ID,
                            set_code="ABS", number="ABS-11", character="孫悟飯:少年期", language="Japanese"))

        item.card_id = NEW_ID
        item.catalog_item_id = NEW_ID
        # scan_image_path already points at PHOTO_PATH -- left as-is.

        now = datetime.now(UTC)
        for source, value in PRICES.items():
            session.add(CardPrice(card_id=NEW_ID, variant=None, source=source, price=Decimal(value), currency="AUD", fetched_at=now))

        session.delete(session.get(TcgCard, AD_HOC_ITEM_ID))
        session.delete(session.get(CatalogItem, AD_HOC_ITEM_ID))

        session.commit()
        print(f"\nDone: {NEW_ID} created (set {SET_ID}), inventory item {item.id} repointed, 3 price rows written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
