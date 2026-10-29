"""Retire the phantom UGM5-002 C row in favour of canonical UGM5-002 DA.

The operator owns one Son Gohan: Youth UGM5-002 Dramatic Art card. An older
CRM row catalogued it as the standard C printing (both stored photos show the
DA card), so the card was live on eBay twice: correctly at AUD 49.95 and as
"UGM5-002 C" at AUD 2.40. The operator confirmed one card photographed twice;
eBay item 267792969857 was ended on 2026-10-30 with scripts/end_ebay_listing.
This retires the phantom CRM row without touching the canonical DA listing.
"""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import (
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
)


WRONG_INVENTORY_ID = "8e6b8c1e-3cc4-4c44-a288-793935e43cc1"
WRONG_SHOP_ID = "cf0041ed-2b8e-4f46-93eb-c33591142a63"
ENDED_EBAY_ID = "267792969857"
CANONICAL_INVENTORY_ID = "a965ae85-b420-42d6-b0ae-da8bd6ad4de0"
CANONICAL_SHOP_ID = "54237f4a-d783-49a2-83dc-23c9ec795d7f"
CANONICAL_EBAY_ID = "267821899528"
CONFIRMATION = "RETIRE-UGM5-002-C-20261030"


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
        raise RuntimeError("one or more exact UGM5-002 records are missing")
    if wrong_shop.inventory_item_id != wrong_inventory.id:
        raise RuntimeError("phantom ShopListing/inventory relationship changed")
    if canonical_shop.inventory_item_id != canonical_inventory.id:
        raise RuntimeError("canonical ShopListing/inventory relationship changed")
    if wrong_inventory.catalog_item_id != "sdbh-ugm5-002" or wrong_inventory.quantity != 1:
        raise RuntimeError("phantom UGM5-002 C inventory no longer matches the audited row")
    if wrong_shop.status != ShopListingStatus.ACTIVE:
        raise RuntimeError("phantom UGM5-002 C ShopListing is no longer active in CRM")
    if canonical_inventory.catalog_item_id != "sdbh-ugm5-002-da" or canonical_inventory.quantity != 1:
        raise RuntimeError("canonical UGM5-002 DA inventory changed; physical reconciliation required")
    link = session.scalar(select(EbayListingLink).where(
        EbayListingLink.shop_listing_id == CANONICAL_SHOP_ID,
    ))
    if (
        link is None
        or link.status != EbayListingLinkStatus.ACTIVE
        or link.ebay_listing_id != CANONICAL_EBAY_ID
    ):
        raise RuntimeError("canonical UGM5-002 DA eBay link changed")
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
            "validated: retire sdbh-ugm5-002 (C) row; keep canonical UGM5-002 DA "
            f"inventory quantity {canonical_inventory.quantity} and eBay {CANONICAL_EBAY_ID}"
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
            "Duplicate CRM record retired 2026-10-30: photos show UGM5-002 DA, not the C printing; operator confirmed one card photographed twice. eBay 267792969857 ended. Canonical inventory is a965ae85-b420-42d6-b0ae-da8bd6ad4de0.",
        )
        _append_note(
            canonical_inventory,
            "Phantom UGM5-002 C CRM row retired into this record on 2026-10-30 (one physical card). Quantity remains 1.",
        )
        record_event(
            session,
            actor_type="user", actor_id="nic", action="inventory.misidentification.consolidate",
            resource_type="inventory_item", resource_id=wrong_inventory.id, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={
                "wrong_catalog_item_id": "sdbh-ugm5-002",
                "ended_ebay_item_id": ENDED_EBAY_ID,
                "canonical_catalog_item_id": canonical_inventory.catalog_item_id,
                "canonical_inventory_item_id": canonical_inventory.id,
                "canonical_shop_listing_id": canonical_shop.id,
                "canonical_ebay_item_id": CANONICAL_EBAY_ID,
                "canonical_quantity": canonical_inventory.quantity,
            },
        )
        session.commit()
        print("retired the phantom UGM5-002 C CRM row; canonical UGM5-002 DA remains live at quantity 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
