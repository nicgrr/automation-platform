"""Retire the E-110 Gold misidentification in favour of canonical E-118.

The canonical E-118 record already has quantity two, professional front/back
photos, and one active eBay listing.  The old flatbed scan is the same E-118
artwork.  This conservatively retires the duplicate CRM row without increasing
physical quantity; a third copy can be added later only after a physical count.
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import (
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
)


WRONG_INVENTORY_ID = "9c3a2f72-fa62-47a9-8412-7a7cf33795b2"
WRONG_SHOP_ID = "bed555b3-d34c-463c-aaac-b4632da378ec"
CANONICAL_INVENTORY_ID = "0d798266-2a68-460f-8a36-eb81489b5156"
CANONICAL_SHOP_ID = "91c0de3c-e318-4072-8aab-0256b90974d7"
CANONICAL_EBAY_ID = "267821905374"
CONFIRMATION = "CONSOLIDATE-E118-20261025"


def _append_note(inventory: InventoryItem, note: str) -> None:
    existing = (inventory.notes or "").strip()
    if note not in existing:
        inventory.notes = "\n".join(value for value in (existing, note) if value)


def _validate(session) -> tuple[InventoryItem, ShopListing, InventoryItem, ShopListing]:
    wrong_inventory = session.get(InventoryItem, WRONG_INVENTORY_ID)
    wrong_shop = session.get(ShopListing, WRONG_SHOP_ID)
    canonical_inventory = session.get(InventoryItem, CANONICAL_INVENTORY_ID)
    canonical_shop = session.get(ShopListing, CANONICAL_SHOP_ID)
    if None in (wrong_inventory, wrong_shop, canonical_inventory, canonical_shop):
        raise RuntimeError("one or more exact E-118 consolidation records are missing")
    if wrong_shop.inventory_item_id != wrong_inventory.id:
        raise RuntimeError("wrong ShopListing/inventory relationship changed")
    if canonical_shop.inventory_item_id != canonical_inventory.id:
        raise RuntimeError("canonical ShopListing/inventory relationship changed")
    if wrong_inventory.catalog_item_id != "tcgp-678758" or wrong_inventory.quantity != 1:
        raise RuntimeError("old E-110 Gold inventory no longer matches the audited row")
    if wrong_shop.status != ShopListingStatus.ACTIVE:
        raise RuntimeError("old E-110 Gold ShopListing is no longer active in CRM")
    if canonical_inventory.catalog_item_id != "dragon-ball-fusion-world-e-118":
        raise RuntimeError("canonical E-118 catalogue relationship changed")
    if canonical_inventory.quantity != 2 or canonical_shop.quantity != 2:
        raise RuntimeError("canonical E-118 quantity changed; physical reconciliation required")
    link = session.scalar(select(EbayListingLink).where(
        EbayListingLink.shop_listing_id == CANONICAL_SHOP_ID,
    ))
    if (
        link is None
        or link.status != EbayListingLinkStatus.ACTIVE
        or link.ebay_listing_id != CANONICAL_EBAY_ID
    ):
        raise RuntimeError("canonical E-118 eBay link changed")
    remote = session.scalar(select(EbayListing).where(
        EbayListing.ebay_listing_id == CANONICAL_EBAY_ID,
    ))
    if remote is None or remote.listing_status != "Active" or remote.quantity != 2:
        raise RuntimeError("canonical E-118 live listing changed")
    return wrong_inventory, wrong_shop, canonical_inventory, canonical_shop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"--confirm must exactly equal {CONFIRMATION}")

    with SessionLocal() as session:
        wrong_inventory, wrong_shop, canonical_inventory, canonical_shop = _validate(session)
        print(
            "validated: retire tcgp-678758 / E-110 Gold row; keep canonical "
            f"E-118 inventory quantity {canonical_inventory.quantity} and eBay {CANONICAL_EBAY_ID}"
        )
        if not args.apply:
            print(f"dry-run: nothing written; use --apply --confirm {CONFIRMATION}")
            return 0

        wrong_shop.status = ShopListingStatus.WITHDRAWN
        wrong_shop.quantity = 0
        wrong_inventory.quantity = 0
        wrong_inventory.status = InventoryStatus.RETURNED
        _append_note(
            wrong_inventory,
            "Duplicate CRM record retired 2026-10-25: scan is English Energy Marker E-118, not E-110 Gold. Canonical inventory is 0d798266-2a68-460f-8a36-eb81489b5156.",
        )
        _append_note(
            canonical_inventory,
            "Misidentified E-110 Gold CRM row consolidated here on 2026-10-25. Quantity remains 2 to avoid overselling; verify physically before adding a possible third copy.",
        )
        record_event(
            session,
            actor_type="user", actor_id="nic", action="inventory.misidentification.consolidate",
            resource_type="inventory_item", resource_id=wrong_inventory.id, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={
                "wrong_catalog_item_id": "tcgp-678758",
                "canonical_catalog_item_id": canonical_inventory.catalog_item_id,
                "canonical_inventory_item_id": canonical_inventory.id,
                "canonical_shop_listing_id": canonical_shop.id,
                "canonical_ebay_item_id": CANONICAL_EBAY_ID,
                "canonical_quantity": canonical_inventory.quantity,
            },
        )
        session.commit()
        print("retired the duplicate E-110 Gold CRM row; canonical E-118 remains live at quantity 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
