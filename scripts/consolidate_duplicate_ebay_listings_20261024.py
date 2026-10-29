"""One-off repair for duplicate live listings confirmed on 2026-10-24.

Three photographed Dragon Ball cards were published as new listings even
though the same physical cards already had legacy CSV listings.  Three older
Pokemon singles and two old language-split variation parents also duplicated
the canonical live listing while only one stock allocation existed.

Dry-run is the default.  The explicit flag and exact item IDs make an
accidental broad cleanup impossible::

    ./scripts/run-with-env.sh .venv/bin/python \
        -m scripts.consolidate_duplicate_ebay_listings_20261024
    ./scripts/run-with-env.sh .venv/bin/python \
        -m scripts.consolidate_duplicate_ebay_listings_20261024 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import unquote

from sqlalchemy import select

from automation_control.adapters.ebay_trading_write import EbayTradingListingAdapter
from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import ebay_services, valid_user_access_token
from automation_control.ebay_publish import revise_listing_on_ebay
from automation_control.models import (
    CardVariant,
    EbayExportItem,
    EbayExportItemStatus,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
)


@dataclass(frozen=True)
class Merge:
    keep_shop_id: str
    duplicate_shop_id: str
    keep_ebay_id: str
    duplicate_ebay_id: str
    title: str
    price: Decimal


MERGES = (
    Merge(
        "8c75bb68-9374-4da9-98f1-ba3a2a6073ed",
        "be45ce33-1158-4f2c-b276-39e82f085933",
        "267821935059", "267796015329",
        "Krillin No.480 Hondan Part 12 1992 Holo DB Carddass Japanese Lightly Played",
        Decimal("11.30"),
    ),
    Merge(
        "82f3c3b5-f934-40e5-99f4-f9fb35ab0889",
        "6e6210a8-9764-43fe-be88-f16ad7ead1a5",
        "267821935125", "267796015351",
        "Piccolo No.29 Total 675 Hondan Part 17 1993 Holo DB Carddass Japanese Mod Played",
        Decimal("5.95"),
    ),
    Merge(
        "b5e219fc-ad9b-40c1-9aaf-744b93b72b57",
        "c1f02b4a-ec20-465c-afea-e4a60a2f6daf",
        "267821935090", "267796015370",
        "Vegeta No.597 Super Battle Part 14 1993 Holo DB Carddass Japanese Lightly Played",
        Decimal("13.56"),
    ),
)

# Each left-hand item duplicates the right-hand canonical active item.
REMOTE_ONLY = (
    ("267785473769", "267805139512", "Terrakion-EX 071/124"),
    ("267785473773", "267805139852", "Rillaboom V SV105"),
    ("267785473774", "267805139567", "Shadow Rider Calyrex VMAX TG30"),
    ("267797340080", "267797338620", "Cross Force variation parent"),
    ("267797337176", "267797340079", "Saiyan's Pride variation parent"),
)


def _active_remote(session, item_id: str) -> EbayListing:
    row = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == item_id))
    if row is None or row.listing_status != "Active":
        raise RuntimeError(f"expected eBay item {item_id} to be Active")
    return row


def _validate(session) -> None:
    for merge in MERGES:
        keep = session.get(ShopListing, merge.keep_shop_id)
        duplicate = session.get(ShopListing, merge.duplicate_shop_id)
        if keep is None or duplicate is None:
            raise RuntimeError(f"missing ShopListing in merge {merge}")
        if keep.catalog_item_id != duplicate.catalog_item_id:
            raise RuntimeError(f"catalogue mismatch in merge {merge}")
        if keep.inventory_item_id == duplicate.inventory_item_id:
            raise RuntimeError(f"merge already shares one inventory item {merge}")
        link = session.scalar(
            select(EbayListingLink).where(EbayListingLink.shop_listing_id == keep.id)
        )
        if (
            link is None
            or link.status != EbayListingLinkStatus.ACTIVE
            or link.ebay_listing_id != merge.keep_ebay_id
        ):
            raise RuntimeError(f"canonical eBay link mismatch for {keep.id}")
        _active_remote(session, merge.keep_ebay_id)
        _active_remote(session, merge.duplicate_ebay_id)
    for duplicate_id, keep_id, _label in REMOTE_ONLY:
        _active_remote(session, duplicate_id)
        _active_remote(session, keep_id)


def _prepare_local_merges(session) -> None:
    now = datetime.now(UTC)
    for merge in MERGES:
        keep = session.get(ShopListing, merge.keep_shop_id)
        duplicate = session.get(ShopListing, merge.duplicate_shop_id)
        keep_inventory = session.get(InventoryItem, keep.inventory_item_id)
        duplicate_inventory = session.get(InventoryItem, duplicate.inventory_item_id)
        if keep_inventory is None or duplicate_inventory is None:
            raise RuntimeError(f"missing inventory in merge {merge}")

        keep_inventory.variant = CardVariant.HOLO
        keep.price = merge.price
        keep.ebay_title = merge.title

        duplicate.status = ShopListingStatus.WITHDRAWN
        duplicate.quantity = 0
        duplicate_inventory.quantity = 0
        duplicate_inventory.status = InventoryStatus.RETURNED
        merge_note = (
            f"Duplicate intake record consolidated into inventory {keep_inventory.id} / "
            f"eBay {merge.keep_ebay_id} on {now.isoformat()}."
        )
        duplicate_inventory.notes = "\n".join(
            value for value in (duplicate_inventory.notes, merge_note) if value
        )

        for export in session.scalars(
            select(EbayExportItem).where(EbayExportItem.ebay_listing_id == merge.duplicate_ebay_id)
        ):
            export.status = EbayExportItemStatus.FAILED
            export.error = f"Ended duplicate; canonical live item is {merge.keep_ebay_id}."

        record_event(
            session,
            actor_type="user", actor_id="nic", action="ebay.duplicate_listing.merge",
            resource_type="shop_listing", resource_id=duplicate.id, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={
                "canonical_shop_listing_id": keep.id,
                "canonical_inventory_item_id": keep_inventory.id,
                "canonical_ebay_item_id": merge.keep_ebay_id,
                "duplicate_ebay_item_id": merge.duplicate_ebay_id,
            },
        )
    session.commit()


async def _revise_canonical_listings(session) -> None:
    for merge in MERGES:
        response = await revise_listing_on_ebay(
            merge.keep_shop_id, confirmation="revise", user="nic", session=session,
        )
        location = unquote(response.headers.get("location", ""))
        if "error=" in location:
            raise RuntimeError(f"revision failed for {merge.keep_ebay_id}: {location}")
        print(f"revised canonical item {merge.keep_ebay_id}: {merge.title}")


async def _end_duplicates(session) -> None:
    settings = get_settings()
    cipher, oauth = ebay_services(settings)
    token = await valid_user_access_token(session, cipher, oauth)
    session.commit()
    adapter = EbayTradingListingAdapter(
        token,
        settings.ebay_client_id,
        environment=settings.ebay_env,
        marketplace_id=settings.ebay_marketplace_id,
    )
    targets = [
        (merge.duplicate_ebay_id, merge.keep_ebay_id, "same physical photographed card")
        for merge in MERGES
    ] + list(REMOTE_ONLY)
    try:
        for duplicate_id, keep_id, label in targets:
            await adapter.end_item(duplicate_id, reason="NotAvailable")
            remote = session.scalar(
                select(EbayListing).where(EbayListing.ebay_listing_id == duplicate_id)
            )
            remote.listing_status = "Ended"
            remote.observed_at = datetime.now(UTC)
            record_event(
                session,
                actor_type="user", actor_id="nic", action="ebay.duplicate_listing.end",
                resource_type="ebay_listing", resource_id=duplicate_id, outcome="success",
                correlation_id=str(uuid.uuid4()),
                details={"canonical_ebay_item_id": keep_id, "reason": label},
            )
            session.commit()
            print(f"ended duplicate item {duplicate_id}; kept {keep_id} ({label})")
    finally:
        await adapter.client.aclose()


async def _run(*, apply: bool) -> int:
    with SessionLocal() as session:
        _validate(session)
        print("Validated exact duplicate pairs:")
        for merge in MERGES:
            print(f"  end {merge.duplicate_ebay_id} -> keep {merge.keep_ebay_id} ({merge.title})")
        for duplicate_id, keep_id, label in REMOTE_ONLY:
            print(f"  end {duplicate_id} -> keep {keep_id} ({label})")
        if not apply:
            print("dry-run: nothing written; pass --apply to consolidate")
            return 0

        _prepare_local_merges(session)
        await _revise_canonical_listings(session)
        await _end_duplicates(session)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
