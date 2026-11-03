"""Move a figurine to eBay when it has sat unsold on Vinted for 14 days.

The owner chose this behaviour: it publishes without a per-item review and the
Vinted listing stays up. One physical unit then backs both listings, so a sale
on either side has to close the other:

- Stock was already reserved when the Vinted listing was recorded. An eBay sale
  consumes that reservation instead of taking a second unit, closes the Vinted
  row, and alerts the owner to delete it in the app (Vinted has no API).
- Marking it sold on Vinted ends the eBay offer.

Nothing can see a Vinted sale until the owner records it, so that window is a
real risk the owner accepted. Everything else fails closed: a figurine with
missing parcel details, an unknown condition or no photo is reported, not listed.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .audit import record_event
from .config import get_settings
from .discord_notify import send_discord_message
from .models import (
    CatalogItem,
    CatalogItemType,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    InventoryStatus,
    PlatformConfig,
    ShopListing,
    ShopListingStatus,
    VintedListing,
    VintedListingStatus,
)

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "vinted-fallback"
FALLBACK_AFTER = timedelta(days=14)
# EBAY_AU "Collectable Figures & Bobbleheads"; Type is its one required aspect
# (getItemAspectsForCategory, 2026-11-03).
CATEGORY_ID = "149372"
ITEM_TYPE_ASPECT = "Figure"
MAX_TITLE_LENGTH = 80
# Inventory API values for the category's 1000 New and 3000 Used conditions.
_CONDITIONS = {"opened / used": "USED_EXCELLENT", "used": "USED_EXCELLENT", "new": "NEW", "brand new": "NEW"}

LISTING_PREFIX = "vinted_fallback:"
SOURCE_PREFIX = "vinted_fallback_source:"
STATE_PREFIX = "vinted_fallback_state:"
PARCEL_KEY = "figurine_parcel"


def _config(session: Session, key: str) -> dict:
    row = session.get(PlatformConfig, key)
    return dict(row.value or {}) if row else {}


def _save_config(session: Session, key: str, value: dict) -> None:
    row = session.get(PlatformConfig, key)
    if row is None:
        session.add(PlatformConfig(key=key, value=value))
    else:
        row.value = value


def notify(message: str) -> None:
    send_discord_message(message, get_settings().discord_webhook_url)


def save_source(session: Session, vinted_listing_id: str, draft: dict, paths: list[str]) -> None:
    """What was posted, captured while the draft can still be found.

    Reserving the last unit drops the figure out of the review rows, so this
    can't be looked up again 14 days later.
    """
    _save_config(session, SOURCE_PREFIX + vinted_listing_id, {
        "title": draft.get("title") or "", "description": draft.get("description") or "",
        "condition": draft.get("condition") or "", "brand": draft.get("brand") or "",
        "series": draft.get("series") or "", "name": draft.get("name") or "",
        "hold_reason": draft.get("hold_reason") or "", "paths": [p for p in paths if p],
    })


def source_for(session: Session, vinted_listing_id: str) -> dict:
    return _config(session, SOURCE_PREFIX + vinted_listing_id)


def state_for(session: Session, vinted_listing_id: str) -> dict:
    return _config(session, STATE_PREFIX + vinted_listing_id)


def _set_state(session: Session, vinted_listing_id: str, **changes) -> None:
    _save_config(session, STATE_PREFIX + vinted_listing_id, {**state_for(session, vinted_listing_id), **changes})


def fallback_vinted_id(session: Session, shop_listing_id: str) -> str | None:
    return _config(session, LISTING_PREFIX + shop_listing_id).get("vinted_listing_id")


def is_fallback_listing(session: Session, listing: ShopListing) -> bool:
    return session.get(PlatformConfig, LISTING_PREFIX + listing.id) is not None


def active_vinted_for(session: Session, shop_listing_id: str) -> VintedListing | None:
    vinted_id = fallback_vinted_id(session, shop_listing_id)
    vinted = session.get(VintedListing, vinted_id) if vinted_id else None
    return vinted if vinted is not None and vinted.status == VintedListingStatus.ACTIVE else None


def shares_reserved_stock(session: Session, listing: ShopListing) -> bool:
    """A live eBay listing backed by the unit its Vinted listing has reserved."""
    return active_vinted_for(session, listing.id) is not None


def save_parcel(session: Session, weight_g: str, length_cm: str, width_cm: str, height_cm: str) -> None:
    values = {"weight_g": weight_g, "length_cm": length_cm, "width_cm": width_cm, "height_cm": height_cm}
    for value in values.values():
        _positive(value)
    _save_config(session, PARCEL_KEY, {**values, "saved_at": datetime.now(UTC).isoformat()})


def saved_parcel(session: Session) -> dict:
    return _config(session, PARCEL_KEY)


def _positive(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Parcel sizes must be numbers.") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Parcel sizes must be above zero.")
    return number


def package_weight_and_size(session: Session) -> dict:
    parcel = saved_parcel(session)
    if not parcel:
        raise ValueError("Add the figurine parcel weight and size on the Live on Vinted page.")
    return {
        "weight": {"value": float(_positive(parcel["weight_g"]) / 1000), "unit": "KILOGRAM"},
        "dimensions": {
            "length": float(_positive(parcel["length_cm"])), "width": float(_positive(parcel["width_cm"])),
            "height": float(_positive(parcel["height_cm"])), "unit": "CENTIMETER",
        },
        "packageType": "PACKAGE_THICK_ENVELOPE",
    }


def ebay_condition(source: dict) -> str:
    condition = _CONDITIONS.get(" ".join((source.get("condition") or "").casefold().split()))
    if condition is None:
        raise ValueError(f"Condition {source.get('condition')!r} has no eBay equivalent; review this figure.")
    return condition


def fallback_title(source: dict) -> str:
    from scripts.convert_to_ebay_template import sanitize_title

    title = " ".join(sanitize_title(source.get("title") or "").split())
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH + 1].rsplit(" ", 1)[0]
    return title.strip()[:MAX_TITLE_LENGTH]


def fallback_description(source: dict) -> str:
    return (source.get("description") or "").strip()


def build_inventory_payload(session: Session, listing: ShopListing, image_urls: list[str]) -> dict:
    source = source_for(session, fallback_vinted_id(session, listing.id) or "")
    aspects = {"Type": [ITEM_TYPE_ASPECT]}
    for aspect, field in (("Brand", "brand"), ("Character", "name")):
        if value := (source.get(field) or "").strip():
            aspects[aspect] = [value[:65]]
    return {
        "condition": ebay_condition(source),
        "packageWeightAndSize": package_weight_and_size(session),
        "product": {
            "title": fallback_title(source), "description": fallback_description(source),
            "aspects": aspects, "imageUrls": image_urls,
        },
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }


def fallback_preflight(session: Session, listing: ShopListing, settings, effective_price: Decimal) -> list[str]:
    vinted_id = fallback_vinted_id(session, listing.id)
    vinted = session.get(VintedListing, vinted_id) if vinted_id else None
    if vinted is None or vinted.status != VintedListingStatus.ACTIVE:
        return ["This figurine is no longer live on Vinted, so it cannot move to eBay automatically."]
    errors = []
    stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if stock is None or stock.id != vinted.inventory_item_id or vinted.stock_released_at is not None:
        errors.append("The eBay listing must use the stock reserved by its Vinted listing.")
    if listing.quantity != 1:
        errors.append("A figurine moves to eBay one copy at a time.")
    if effective_price != vinted.price:
        errors.append("The eBay price must match the Vinted price.")
    if not settings.ebay_figure_fulfillment_policy_id:
        errors.append("No tracked parcel postage policy is configured for figurines.")
    source = source_for(session, vinted.id)
    if not source:
        errors.append("The posted Vinted details were not saved; review this figure.")
        return errors
    if source.get("hold_reason"):
        errors.append(f"This figure is on hold: {source['hold_reason']}")
    if not fallback_title(source):
        errors.append("The eBay title is empty.")
    if not fallback_description(source):
        errors.append("The eBay description is empty.")
    for check in (lambda: ebay_condition(source), lambda: package_weight_and_size(session)):
        try:
            check()
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def consume_reservation_for_ebay_sale(session: Session, listing: ShopListing) -> bool:
    """Close the Vinted side of an eBay sale. True if the reserved unit paid for it.

    Called from the shared stock decrement, so the caller still owns the commit.
    """
    vinted = active_vinted_for(session, listing.id)
    if vinted is None:
        return False
    claimed = session.execute(
        update(VintedListing).where(VintedListing.id == vinted.id, VintedListing.status == VintedListingStatus.ACTIVE)
        .values(status=VintedListingStatus.REMOVED).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        return False
    session.refresh(vinted)
    now = datetime.now(UTC)
    vinted.sold_at = now
    _set_state(session, vinted.id, sold_on_ebay_at=now.isoformat(), delete_from_vinted=True)
    stock = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if stock is not None and stock.quantity == 0:
        another_reserved = session.scalar(select(VintedListing.id).where(
            VintedListing.inventory_item_id == stock.id, VintedListing.id != vinted.id,
            VintedListing.status == VintedListingStatus.ACTIVE).limit(1))
        stock.status = InventoryStatus.RESERVED if another_reserved else InventoryStatus.SOLD
    notify(f"Sold on eBay: delete this from Vinted now, so it can't sell twice.\n{vinted.title}\n{vinted.url}")
    return True


def close_ebay_listing_for_vinted_sale(session: Session, vinted: VintedListing) -> str | None:
    """Take the local listing off sale in the Vinted sale's transaction; returns its id if eBay needs ending."""
    for row in session.scalars(select(PlatformConfig).where(PlatformConfig.key.like(LISTING_PREFIX + "%"))):
        if (row.value or {}).get("vinted_listing_id") != vinted.id:
            continue
        listing = session.get(ShopListing, row.key.removeprefix(LISTING_PREFIX))
        if listing is None:
            return None
        listing.quantity = 0
        listing.status = ShopListingStatus.SOLD_OUT
        return listing.id
    return None


async def end_ebay_listing(shop_listing_id: str) -> None:
    from .ebay_sync import sync_ebay_after_sale

    await sync_ebay_after_sale(shop_listing_id, 0)


def _prepare_listing(session: Session, vinted: VintedListing) -> ShopListing:
    """Reuse the figure's existing listing: a second one would fail the duplicate check."""
    source = source_for(session, vinted.id)
    listings = session.scalars(select(ShopListing).where(
        ShopListing.inventory_item_id == vinted.inventory_item_id)).all()
    usable = [l for l in listings if l.status in (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT)]
    if len(usable) > 1:
        raise ValueError("This figure has more than one listing; resolve the duplicate first.")
    listing = usable[0] if usable else next(iter(listings), None)
    if listing is None:
        stock = session.get(InventoryItem, vinted.inventory_item_id)
        if stock is None:
            raise ValueError("The stock behind this Vinted listing is gone.")
        listing = ShopListing(id=str(uuid.uuid4()), catalog_item_id=stock.catalog_item_id,
                              inventory_item_id=stock.id, title=vinted.title, currency="AUD",
                              status=ShopListingStatus.DRAFT, price=vinted.price, quantity=1)
        session.add(listing)
        session.flush()
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    if link is not None and link.status not in (EbayListingLinkStatus.ENDED, EbayListingLinkStatus.FAILED):
        raise ValueError("This figure already has an eBay listing.")
    previous = _config(session, LISTING_PREFIX + listing.id).get("previous") or {
        "status": listing.status.value, "price": str(listing.price), "quantity": listing.quantity,
    }
    if listing.status not in (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT):
        listing.status = ShopListingStatus.DRAFT
    listing.price, listing.quantity = vinted.price, 1
    listing.condition = source.get("condition") or listing.condition
    listing.ebay_title = fallback_title(source) or listing.ebay_title
    listing.ebay_description = fallback_description(source) or listing.ebay_description
    if source.get("paths"):
        listing.ebay_photo_paths = list(source["paths"])
    _save_config(session, LISTING_PREFIX + listing.id, {"vinted_listing_id": vinted.id, "previous": previous})
    return listing


def due_listings(session: Session, now: datetime) -> list[VintedListing]:
    rows = session.scalars(select(VintedListing).where(VintedListing.status == VintedListingStatus.ACTIVE)).all()
    def posted(row):
        value = row.posted_at
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return [row for row in rows if posted(row) + FALLBACK_AFTER <= now
            and not state_for(session, row.id).get("ebay_listing_id")]


def _report_blocker(session: Session, vinted: VintedListing, reason: str) -> None:
    """Alert when the reason changes, not on every hourly run."""
    if state_for(session, vinted.id).get("blocked") != reason:
        notify(f"Couldn't move a figurine to eBay: {vinted.title}\n{reason}")
    _set_state(session, vinted.id, blocked=reason, blocked_at=datetime.now(UTC).isoformat())
    session.commit()


async def run_fallback(session: Session, *, now: datetime | None = None, apply: bool = False) -> list[str]:
    from .ebay_publish import publication_preflight, publish_listing_to_ebay

    now = now or datetime.now(UTC)
    settings = get_settings()
    results = []
    for vinted in due_listings(session, now):
        title = vinted.title
        if not apply:
            results.append(f"DUE: {title}")
            continue
        try:
            listing = _prepare_listing(session, vinted)
            session.flush()
            check = publication_preflight(session, listing, settings)
            if not check.ready:
                raise ValueError("; ".join(check.errors))
            session.commit()
        except ValueError as exc:
            session.rollback()  # a blocked figure keeps its listing exactly as it was
            _report_blocker(session, vinted, str(exc))
            results.append(f"BLOCKED: {title}: {exc}")
            continue
        try:
            await publish_listing_to_ebay(listing.id, confirmation="publish", user=SYSTEM_ACTOR, session=session)
        except HTTPException as exc:
            session.rollback()
            _report_blocker(session, vinted, str(exc.detail))
            results.append(f"BLOCKED: {title}: {exc.detail}")
            continue
        link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
        _set_state(session, vinted.id, ebay_listing_id=link.ebay_listing_id, shop_listing_id=listing.id,
                   moved_at=now.isoformat(), blocked=None)
        session.commit()
        record_event(session, actor_type="system", actor_id=SYSTEM_ACTOR, action="vinted.moved_to_ebay",
                     resource_type="vinted_listing", resource_id=vinted.id, outcome="success",
                     correlation_id=str(uuid.uuid4()),
                     details={"shop_listing_id": listing.id, "ebay_listing_id": link.ebay_listing_id,
                              "price": str(listing.price)})
        notify(f"Moved to eBay after 14 days on Vinted: {title}\nAU ${listing.price}, eBay {link.ebay_listing_id}. "
               "If it has sold on Vinted, mark it sold in EzBay now to end the eBay listing.")
        results.append(f"MOVED: {title}: eBay {link.ebay_listing_id}")
    return results
