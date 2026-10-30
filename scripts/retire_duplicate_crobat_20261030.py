"""Retire the older Team Rocket's Crobat ex 130/222 listing and fold the new
one onto the canonical catalogue entry.

The operator owns one Chinese Team Rocket's Crobat ex 130/222. On
2026-10-30 the one-at-a-time photo confirm matched a new photo of it to a
duplicate local catalogue entry (printed number "130", no set total), so
"Extra copy" created new stock and a listing that went live on eBay at
AUD 3.99 beside the existing AUD 21.95 listing. The operator chose to keep
the new listing (it has the new photos) and ended eBay 267824797612 with
scripts/end_ebay_listing. This withdraws the old CRM listing, moves the kept
listing and the photo pair onto canonical entry c1f26d7b… with the original
stock record (still quantity 1, the one physical card), and deletes the
extra stock record and the now-unused duplicate catalogue entry. Stock is
unique per catalogue entry, finish and condition, so the extra record
cannot simply be moved beside the original.
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import func, select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import (
    CardSet,
    CatalogItem,
    InventoryItem,
    InventoryStatus,
    ListingPhotoPair,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)

CANONICAL_CATALOG_ID = "c1f26d7b-9e75-4669-82ee-675234f54223"
DUPLICATE_CATALOG_ID = "local-e8908eacae92fc9fc8f2308c"
DUPLICATE_SET_ID = "local-b8b862cc0f3afb49b655e309"
OLD_SHOP_ID = "c7d2c9fb-5a59-4577-831c-d462e0b6d0fd"
OLD_INVENTORY_ID = "67fb27d2-656e-4c37-a308-130a2691ff5e"
ENDED_EBAY_ID = "267824797612"
KEPT_SHOP_ID = "aa7a892a-f136-4f8a-8b1f-6be1bff5a690"
KEPT_INVENTORY_ID = "fbeb7db2-2e44-4442-97d5-3922ad8821a6"
KEPT_EBAY_ID = "267827676356"
CONFIRMATION = "RETIRE-CROBAT-130-20261030"


def _append_note(inventory: InventoryItem, note: str) -> None:
    existing = (inventory.notes or "").strip()
    if note not in existing:
        inventory.notes = "\n".join(value for value in (existing, note) if value)


def _validate(session):
    old_shop, old_inv = session.get(ShopListing, OLD_SHOP_ID), session.get(InventoryItem, OLD_INVENTORY_ID)
    kept_shop, kept_inv = session.get(ShopListing, KEPT_SHOP_ID), session.get(InventoryItem, KEPT_INVENTORY_ID)
    canonical, duplicate = session.get(TcgCard, CANONICAL_CATALOG_ID), session.get(TcgCard, DUPLICATE_CATALOG_ID)
    if None in (old_shop, old_inv, kept_shop, kept_inv, canonical, duplicate):
        raise RuntimeError("one or more exact Crobat records are missing")
    if old_shop.inventory_item_id != OLD_INVENTORY_ID or kept_shop.inventory_item_id != KEPT_INVENTORY_ID:
        raise RuntimeError("a Crobat listing/stock relationship changed")
    if old_shop.status != ShopListingStatus.ACTIVE or old_shop.catalog_item_id != CANONICAL_CATALOG_ID:
        raise RuntimeError("the old Crobat listing is no longer the active canonical listing")
    if old_inv.quantity != 1 or kept_inv.quantity != 1:
        raise RuntimeError("Crobat stock quantities changed; physical reconciliation required")
    if kept_shop.catalog_item_id != DUPLICATE_CATALOG_ID or kept_inv.catalog_item_id != DUPLICATE_CATALOG_ID:
        raise RuntimeError("the kept Crobat listing is no longer on the duplicate catalogue entry")
    if canonical.number != "130/222" or duplicate.number != "130":
        raise RuntimeError("catalogue entries no longer match the audited identities")
    return old_shop, old_inv, kept_shop, kept_inv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"--confirm must exactly equal {CONFIRMATION}")

    with SessionLocal() as session:
        old_shop, old_inv, kept_shop, kept_inv = _validate(session)
        print(f"validated: retire listing {OLD_SHOP_ID} (eBay {ENDED_EBAY_ID}); keep {KEPT_SHOP_ID} (eBay {KEPT_EBAY_ID})")
        if not args.apply:
            print(f"dry-run: nothing written; use --apply --confirm {CONFIRMATION}")
            return 0

        old_shop.status = ShopListingStatus.WITHDRAWN
        old_shop.quantity = 0
        kept_shop.catalog_item_id = CANONICAL_CATALOG_ID
        kept_shop.inventory_item_id = OLD_INVENTORY_ID
        old_inv.status = InventoryStatus.AVAILABLE
        _append_note(old_inv, f"2026-10-30: one physical card. Old listing {OLD_SHOP_ID} / eBay {ENDED_EBAY_ID} "
                              f"ended; now sold through listing {KEPT_SHOP_ID} / eBay {KEPT_EBAY_ID}. Duplicate "
                              f"stock record {KEPT_INVENTORY_ID} from a mis-matched photo confirm was removed.")
        for pair in session.scalars(select(ListingPhotoPair).where(
                ListingPhotoPair.proposed_catalog_item_id == DUPLICATE_CATALOG_ID)):
            pair.proposed_catalog_item_id = CANONICAL_CATALOG_ID
        session.flush()
        session.delete(kept_inv)
        session.flush()
        session.delete(session.get(TcgCard, DUPLICATE_CATALOG_ID))
        session.delete(session.get(CatalogItem, DUPLICATE_CATALOG_ID))
        session.flush()
        if not session.scalar(select(func.count()).select_from(TcgCard).where(TcgCard.set_id == DUPLICATE_SET_ID)):
            dup_set = session.get(CardSet, DUPLICATE_SET_ID)
            if dup_set is not None:
                session.delete(dup_set)
        record_event(
            session, actor_type="user", actor_id="nic", action="inventory.misidentification.consolidate",
            resource_type="inventory_item", resource_id=OLD_INVENTORY_ID, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={"ended_ebay_item_id": ENDED_EBAY_ID, "kept_ebay_item_id": KEPT_EBAY_ID,
                     "kept_shop_listing_id": KEPT_SHOP_ID, "canonical_catalog_item_id": CANONICAL_CATALOG_ID,
                     "deleted_duplicate_catalog_item_id": DUPLICATE_CATALOG_ID,
                     "deleted_duplicate_inventory_item_id": KEPT_INVENTORY_ID},
        )
        session.commit()
        print("retired the old Crobat listing; the kept listing uses the canonical 130/222 entry and original stock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
