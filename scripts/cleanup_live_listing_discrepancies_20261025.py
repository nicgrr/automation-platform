"""Remove the exact duplicate/incorrect live sale paths audited on 2026-10-25.

Dry-run is the default.  Applying requires an exact confirmation token so
neither item IDs nor variation SKUs can be supplied through an accidental
wildcard or broad query.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from automation_control.adapters.ebay_trading_write import (
    EbayTradingListingAdapter,
    EbayTradingQuantityAdapter,
    InventoryRevision,
)
from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import ebay_services, valid_user_access_token
from automation_control.models import (
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


CONFIRMATION = "RUN-20261025-LIVE-CLEANUP"


@dataclass(frozen=True)
class VariationRemoval:
    parent_item_id: str
    shop_listing_id: str
    label: str


VARIATION_REMOVALS = (
    VariationRemoval("267797338618", "2af006f2-6fd3-4632-8bbd-567bc725f41b", "Bulbasaur #1"),
    VariationRemoval("267797338618", "238a54aa-90db-4509-a683-d81b73400850", "Ivysaur #2"),
    VariationRemoval("267797338618", "5c7044d1-37cb-4e4f-91d5-15df74da8fc4", "Pikachu #25"),
    VariationRemoval("267797338618", "e466165a-6aa8-4974-a3e9-eecbe1d5851d", "Cloyster #91"),
    VariationRemoval("267797339786", "6fa487cb-235c-44fd-a0d1-5d609b580f0b", "Tapu Lele #92"),
    VariationRemoval("267797340077", "35141344-377a-4a4d-bb9a-af124053b4d0", "Professor's Research #88"),
)


@dataclass(frozen=True)
class EndTarget:
    item_id: str
    label: str
    reason: str
    disposition: str
    bundle_item_id: str | None = None


END_TARGETS = (
    EndTarget("267805139688", "Wugtrio 224/091", "NotAvailable", "bundle", "267785473767"),
    EndTarget("267805139894", "Glimmora ex 218/091", "NotAvailable", "bundle", "267785473767"),
    EndTarget("267805139499", "Kadabra 149/091", "NotAvailable", "bundle", "267785473767"),
    EndTarget("267805139901", "Mega Heracross ex 004/094", "NotAvailable", "bundle", "267785473768"),
    EndTarget("267805515872", "Mega Sharpedo ex 061/094", "NotAvailable", "bundle", "267785473768"),
    EndTarget("267805139735", "Wishiwashi-GX 063/236", "NotAvailable", "bundle", "267785473771"),
    EndTarget("267805139913", "Zamazenta V 018/025", "NotAvailable", "bundle", "267785473771"),
    EndTarget("267805603860", "Piccolo (PR) #7 photo is Trunks", "OtherListingError", "identity_review"),
    EndTarget("267796015350", "Krillin LZ03-038 unofficial Little Dinosaur card", "OtherListingError", "unofficial_hold"),
)


def _active_remote(session, item_id: str) -> EbayListing:
    row = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == item_id))
    if row is None or row.listing_status != "Active":
        raise RuntimeError(f"expected eBay item {item_id} to be Active")
    return row


def _shop_for_item(session, item_id: str) -> tuple[ShopListing, EbayListingLink | None]:
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.ebay_listing_id == item_id))
    if link is not None:
        shop = session.get(ShopListing, link.shop_listing_id)
        if shop is None:
            raise RuntimeError(f"missing ShopListing for link to {item_id}")
        return shop, link
    exports = list(session.scalars(
        select(EbayExportItem).where(
            EbayExportItem.ebay_listing_id == item_id,
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
        )
    ))
    shops = [session.get(ShopListing, row.sku) for row in exports]
    shops = [shop for shop in shops if shop is not None]
    if len({shop.id for shop in shops}) != 1:
        raise RuntimeError(f"could not resolve exactly one ShopListing for {item_id}")
    return shops[0], None


def _validate(session) -> None:
    for removal in VARIATION_REMOVALS:
        _active_remote(session, removal.parent_item_id)
        shop = session.get(ShopListing, removal.shop_listing_id)
        if shop is None:
            raise RuntimeError(f"missing variation ShopListing {removal.shop_listing_id}")
        export = session.scalar(select(EbayExportItem).where(
            EbayExportItem.ebay_listing_id == removal.parent_item_id,
            EbayExportItem.sku == removal.shop_listing_id,
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
        ))
        if export is None or export.kind != "variation_child":
            raise RuntimeError(f"variation ledger mismatch for {removal.label}")

    for target in END_TARGETS:
        _active_remote(session, target.item_id)
        shop, link = _shop_for_item(session, target.item_id)
        if shop.status != ShopListingStatus.ACTIVE:
            raise RuntimeError(f"expected active ShopListing for {target.item_id}")
        if link is not None and link.status != EbayListingLinkStatus.ACTIVE:
            raise RuntimeError(f"expected active eBay link for {target.item_id}")


def _append_note(inventory: InventoryItem, note: str) -> None:
    existing = (inventory.notes or "").strip()
    if note not in existing:
        inventory.notes = "\n".join(value for value in (existing, note) if value)


def _sync_variation_removal(session, removal: VariationRemoval) -> None:
    shop = session.get(ShopListing, removal.shop_listing_id)
    shop.quantity = 0
    if shop.status != ShopListingStatus.SOLD_OUT:
        shop.status = ShopListingStatus.WITHDRAWN
    for export in session.scalars(select(EbayExportItem).where(
        EbayExportItem.ebay_listing_id == removal.parent_item_id,
        EbayExportItem.sku == removal.shop_listing_id,
        EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
    )):
        export.status = EbayExportItemStatus.CANCELLED
        export.error = "Removed from live variation parent during 2026-10-25 reconciliation."
    record_event(
        session,
        actor_type="user", actor_id="nic", action="ebay.variation.delete",
        resource_type="shop_listing", resource_id=shop.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"ebay_item_id": removal.parent_item_id, "label": removal.label},
    )


def _sync_ended_item(session, target: EndTarget) -> None:
    now = datetime.now(UTC)
    remote = _active_remote(session, target.item_id)
    shop, link = _shop_for_item(session, target.item_id)
    inventory = session.get(InventoryItem, shop.inventory_item_id) if shop.inventory_item_id else None
    if inventory is None:
        raise RuntimeError(f"missing inventory for ended item {target.item_id}")

    remote.listing_status = "Ended"
    remote.observed_at = now
    shop.status = ShopListingStatus.WITHDRAWN
    shop.quantity = 0
    if link is not None:
        link.status = EbayListingLinkStatus.ENDED
        link.last_synced_at = now
        link.last_error = None
    for export in session.scalars(select(EbayExportItem).where(
        EbayExportItem.ebay_listing_id == target.item_id,
        EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
    )):
        export.status = EbayExportItemStatus.CANCELLED
        export.error = f"Ended during 2026-10-25 reconciliation: {target.label}."

    if target.disposition == "bundle":
        inventory.status = InventoryStatus.LISTED
        _append_note(
            inventory,
            f"Allocated to active bundle eBay {target.bundle_item_id}; individual eBay {target.item_id} ended 2026-10-25.",
        )
    elif target.disposition == "identity_review":
        inventory.status = InventoryStatus.AVAILABLE
        _append_note(
            inventory,
            f"Hold for re-identification: eBay {target.item_id} ended 2026-10-25 because the photo shows Trunks, not Piccolo.",
        )
    elif target.disposition == "unofficial_hold":
        inventory.status = InventoryStatus.AVAILABLE
        _append_note(
            inventory,
            f"Do not relist as an official card: eBay {target.item_id} ended 2026-10-25; Little Dinosaur LZ03 unofficial/third-party card.",
        )

    record_event(
        session,
        actor_type="user", actor_id="nic", action="ebay.listing.end_reconciliation",
        resource_type="ebay_listing", resource_id=target.item_id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={
            "label": target.label,
            "reason": target.reason,
            "disposition": target.disposition,
            "bundle_ebay_item_id": target.bundle_item_id,
        },
    )


async def _run(*, apply: bool, confirmation: str | None) -> int:
    if apply and confirmation != CONFIRMATION:
        raise SystemExit(f"--confirm must exactly equal {CONFIRMATION}")

    settings = get_settings()
    with SessionLocal() as session:
        _validate(session)
        print("Validated exact variation removals:")
        for removal in VARIATION_REMOVALS:
            print(f"  {removal.parent_item_id}: {removal.label} [{removal.shop_listing_id}]")
        print("Validated exact listing endings:")
        for target in END_TARGETS:
            print(f"  {target.item_id}: {target.label}")
        if not apply:
            print(f"dry-run: nothing written; use --apply --confirm {CONFIRMATION}")
            return 0

        cipher, oauth = ebay_services(settings)
        token = await valid_user_access_token(session, cipher, oauth)
        session.commit()
        adapter = EbayTradingListingAdapter(
            token,
            settings.ebay_client_id,
            environment=settings.ebay_env,
            marketplace_id=settings.ebay_marketplace_id,
        )
        quantity_adapter = EbayTradingQuantityAdapter(
            token,
            settings.ebay_client_id,
            environment=settings.ebay_env,
            marketplace_id=settings.ebay_marketplace_id,
        )
        try:
            parents: dict[str, list[VariationRemoval]] = {}
            for removal in VARIATION_REMOVALS:
                parents.setdefault(removal.parent_item_id, []).append(removal)
            for parent_id, removals in parents.items():
                await quantity_adapter.revise_inventory_status([
                    InventoryRevision(parent_id, removal.shop_listing_id, 0)
                    for removal in removals
                ])
                for removal in removals:
                    _sync_variation_removal(session, removal)
                session.commit()
                print(f"disabled {len(removals)} variation(s) on {parent_id}")

            for target in END_TARGETS:
                await adapter.end_item(target.item_id, reason=target.reason)
                _sync_ended_item(session, target)
                session.commit()
                print(f"ended {target.item_id}: {target.label}")
        finally:
            await adapter.client.aclose()
            await quantity_adapter.client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    return asyncio.run(_run(apply=args.apply, confirmation=args.confirm))


if __name__ == "__main__":
    raise SystemExit(main())
