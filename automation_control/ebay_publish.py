"""Safe, resumable publication of one canonical ShopListing to eBay.

The Inventory API call creates a real, potentially billable production
listing. A read-only preview therefore runs every local preflight first and
the write endpoint requires an explicit confirmation value. The link row is
persisted before the first eBay write and retries discover/reuse an offer by
SKU, so a process crash between createOffer and publishOffer cannot create a
second offer on the next click.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.convert_to_ebay_template import (
    CARD_CONDITION_DESCRIPTOR_NEAR_MINT,
    EBAY_ITEM_SPECIFIC_MAX_LEN,
    GAME_DISPLAY_NAMES,
    GAME_ITEM_SPECIFICS,
    sanitize_title,
)
from storefront.common import _reference_image_url

from .adapters.ebay import EbayApiError, EbayNotConfigured
from .adapters.ebay_write import EbayWriteAdapter
from .adapters.r2 import R2UploadError, ensure_uploaded, is_configured as r2_is_configured
from .approvals import decide_approval
from .audit import record_event
from .auth import require_dashboard_user
from .classification import load_pipeline_config
from .config import get_settings
from .database import get_session
from .ebay_oauth import (
    INVENTORY_WRITE_SCOPE,
    OAuthError,
    configured_for_writes,
    ebay_services,
    valid_user_access_token,
)
from .models import (
    Approval,
    CardSet,
    CatalogItem,
    CatalogItemType,
    EbayCategoryMapping,
    EbayCredential,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from .ui import brand_header, page, pill

router = APIRouter(tags=["ebay-publish"])

EBAY_MIN_FIXED_PRICE = Decimal("1.00")
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class PublishPreflight:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    category_id: str | None
    image_url: str | None
    image_will_upload: bool

    @property
    def ready(self) -> bool:
        return not self.errors


def _resolve_category(
    session: Session, catalog_item: CatalogItem, tcg_card: TcgCard | None, marketplace_id: str,
) -> str | None:
    """Resolve a game-specific mapping first, then the explicit type fallback."""
    game = tcg_card.game if tcg_card else None
    for candidate_game in (game, None):
        row = session.scalar(
            select(EbayCategoryMapping).where(
                EbayCategoryMapping.marketplace_id == marketplace_id,
                EbayCategoryMapping.catalog_item_type == catalog_item.item_type.value,
                EbayCategoryMapping.game == candidate_game,
            )
        )
        if row:
            return row.ebay_category_id
    return None


def _public_image_url(session: Session, listing: ShopListing, catalog_item: CatalogItem) -> str | None:
    """Return the first already-public HTTPS image in the CSV flow's order."""
    for candidate in (
        listing.image_url,
        catalog_item.image_url,
        _reference_image_url(session, catalog_item),
    ):
        if candidate and candidate.startswith("https://"):
            return candidate
    return None


def _scan_upload_source(session: Session, listing: ShopListing, settings) -> tuple[InventoryItem, Path] | None:
    if not listing.inventory_item_id or not r2_is_configured(settings):
        return None
    item = session.get(InventoryItem, listing.inventory_item_id)
    if item is None or not item.scan_image_path:
        return None
    path = Path(item.scan_image_path)
    try:
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            return None
    except OSError:
        return None
    return item, path


def publication_preflight(session: Session, listing: ShopListing, settings) -> PublishPreflight:
    errors: list[str] = []
    warnings: list[str] = []

    if listing.status not in (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT):
        errors.append("Only an active or draft listing can be published.")
    if listing.quantity < 1:
        errors.append("Quantity must be at least one.")
    if listing.currency != "AUD":
        errors.append("The EBAY_AU flow currently supports AUD listings only.")
    if listing.price < EBAY_MIN_FIXED_PRICE:
        errors.append("eBay fixed-price listings must be at least AUD $1.00.")
    if not sanitize_title(listing.title).strip():
        errors.append("The title becomes empty after eBay-safe character cleanup.")

    if not configured_for_writes(settings):
        errors.append("eBay OAuth, business-policy IDs, or merchant location are not fully configured.")

    credential = session.get(EbayCredential, 1)
    if credential is None:
        errors.append("The eBay seller account is not connected.")
    else:
        if credential.environment != settings.ebay_env:
            errors.append(
                f"The stored eBay connection is for {credential.environment}, not {settings.ebay_env}."
            )
        if INVENTORY_WRITE_SCOPE not in set((credential.scopes or "").split()):
            errors.append("The eBay connection lacks the sell.inventory write scope; reconnect it first.")

    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    category_id = None
    image_url = None
    image_will_upload = False
    inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None

    if listing.inventory_item_id:
        duplicate = session.scalar(
            select(ShopListing.id).where(
                ShopListing.id != listing.id,
                ShopListing.inventory_item_id == listing.inventory_item_id,
                ShopListing.status.in_((ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT)),
            ).limit(1)
        )
        if duplicate:
            errors.append(
                "Another active or draft listing already uses this inventory; withdraw the duplicate first."
            )

    if catalog_item is None:
        errors.append("The catalogue item is missing.")
    else:
        if catalog_item.item_type != CatalogItemType.TCG_CARD:
            errors.append("Direct publishing currently supports ungraded single TCG cards only; use CSV fallback.")
        if tcg_card is None:
            errors.append("TCG card details are missing.")
        else:
            if not tcg_card.set_id or session.get(CardSet, tcg_card.set_id) is None:
                errors.append("The card set is missing.")
            if not tcg_card.number:
                errors.append("The card number is missing.")
        category_id = _resolve_category(session, catalog_item, tcg_card, settings.ebay_marketplace_id)
        if category_id is None:
            game = tcg_card.game if tcg_card else None
            errors.append(
                f"No eBay category mapping exists for item type {catalog_item.item_type.value} / game {game}."
            )

        image_url = _public_image_url(session, listing, catalog_item)
        if image_url is None:
            upload_source = _scan_upload_source(session, listing, settings)
            if upload_source is None:
                errors.append("No public HTTPS image or uploadable JPG/PNG scan is available.")
            else:
                image_will_upload = True
                warnings.append("The linked scan will be uploaded to the configured public image bucket on publish.")

    if inventory_item and inventory_item.grading_company:
        errors.append("Graded-card condition descriptors are not implemented; use CSV fallback.")
    condition = (listing.condition or (inventory_item.condition if inventory_item else "")).casefold()
    if tcg_card is not None and "near mint" not in condition:
        errors.append("Direct publishing currently supports the verified Near Mint ungraded condition only.")

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    if link:
        if link.status == EbayListingLinkStatus.ACTIVE:
            errors.append("This listing is already active on eBay.")
        elif link.status == EbayListingLinkStatus.ENDING:
            errors.append("This listing is currently being ended on eBay.")
        elif link.status == EbayListingLinkStatus.PUBLISHING:
            warnings.append("A prior attempt stopped while publishing; this run will discover and resume its offer.")
        elif link.status in (EbayListingLinkStatus.FAILED, EbayListingLinkStatus.ENDED):
            warnings.append("This run will safely reuse or recreate the prior eBay offer.")

    return PublishPreflight(tuple(errors), tuple(warnings), category_id, image_url, image_will_upload)


def _card_aspects(
    catalog_item: CatalogItem, tcg_card: TcgCard, card_set: CardSet, inventory_item: InventoryItem | None,
) -> dict[str, list[str]]:
    franchise, manufacturer = GAME_ITEM_SPECIFICS.get(tcg_card.game, (None, None))
    aspects: dict[str, list[str]] = {
        "Type": ["Trading Card"],
        "Set": [card_set.name],
        "Card Name": [sanitize_title(catalog_item.name)[:EBAY_ITEM_SPECIFIC_MAX_LEN]],
        "Card Number": [tcg_card.number],
        "Language": [tcg_card.language or "English"],
    }
    if franchise:
        aspects["Franchise"] = [franchise]
    if manufacturer:
        aspects["Manufacturer"] = [manufacturer]
    if game_name := GAME_DISPLAY_NAMES.get(tcg_card.game):
        aspects["Game"] = [game_name]
    if tcg_card.rarity:
        aspects["Rarity"] = [sanitize_title(tcg_card.rarity)[:EBAY_ITEM_SPECIFIC_MAX_LEN]]
    variant = inventory_item.variant.value.replace("_", " ").title() if inventory_item else tcg_card.variant
    if variant:
        aspects["Finish"] = [variant]
    return aspects


def _build_inventory_item_payload(
    listing: ShopListing,
    catalog_item: CatalogItem,
    tcg_card: TcgCard,
    card_set: CardSet,
    inventory_item: InventoryItem | None,
    image_url: str,
) -> dict:
    return {
        # Category 183454 uses numeric condition 4000 for ungraded cards;
        # Inventory API represents it as USED_VERY_GOOD and requires the
        # structured 40001/400010 Near Mint descriptor as well.
        "condition": "USED_VERY_GOOD",
        "conditionDescriptors": [
            {"name": "40001", "values": [CARD_CONDITION_DESCRIPTOR_NEAR_MINT]},
        ],
        "product": {
            "title": sanitize_title(listing.title)[:80],
            "description": listing.description or listing.title,
            "aspects": _card_aspects(catalog_item, tcg_card, card_set, inventory_item),
            "imageUrls": [image_url],
        },
        "availability": {"shipToLocationAvailability": {"quantity": listing.quantity}},
    }


def _build_offer_payload(listing: ShopListing, sku: str, category_id: str, settings) -> dict:
    return {
        "sku": sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        "listingDuration": "GTC",
        "availableQuantity": listing.quantity,
        "categoryId": category_id,
        "listingDescription": listing.description or listing.title,
        "listingPolicies": {
            "fulfillmentPolicyId": settings.ebay_fulfillment_policy_id,
            "paymentPolicyId": settings.ebay_payment_policy_id,
            "returnPolicyId": settings.ebay_return_policy_id,
        },
        "pricingSummary": {"price": {"value": str(listing.price), "currency": listing.currency}},
        "merchantLocationKey": settings.ebay_merchant_location_key,
    }


def _listing_id_if_published(offer: dict) -> str | None:
    listing = offer.get("listing") or {}
    if str(offer.get("status", "")).upper() != "PUBLISHED":
        return None
    return listing.get("listingId")


def _finalize_active_link(
    session: Session, link: EbayListingLink, listing: ShopListing, ebay_listing_id: str,
) -> None:
    link.ebay_listing_id = ebay_listing_id
    link.status = EbayListingLinkStatus.ACTIVE
    link.last_pushed_quantity = listing.quantity
    link.last_synced_at = datetime.now(UTC)
    link.last_error = None
    session.commit()


def _prepare_image(session: Session, listing: ShopListing, catalog_item: CatalogItem, settings) -> str:
    if url := _public_image_url(session, listing, catalog_item):
        return url
    source = _scan_upload_source(session, listing, settings)
    if source is None:
        raise HTTPException(status_code=400, detail="no publishable eBay image is available")
    inventory_item, path = source
    try:
        return ensure_uploaded(
            path,
            f"scan/{inventory_item.id}{path.suffix.lower()}",
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            public_base_url=load_pipeline_config().images.base_url,
        )
    except (R2UploadError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"image upload failed: {exc}") from exc


@router.get("/shop-listings/{listing_id}/ebay-preview", response_class=HTMLResponse)
def ebay_publish_preview(
    listing_id: str,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    check = publication_preflight(session, listing, settings)
    status = pill("READY" if check.ready else "BLOCKED", "ok" if check.ready else "bad")
    environment = settings.ebay_env.upper()
    image = (
        f"<img class='ebay-preview-image' src='{escape(check.image_url)}' alt=''>"
        if check.image_url
        else "<div class='ebay-preview-image empty'>Scan uploads when confirmed</div>"
    )
    errors = "".join(f"<li>{escape(message)}</li>" for message in check.errors)
    warnings = "".join(f"<li>{escape(message)}</li>" for message in check.warnings)
    checks_html = ""
    if errors:
        checks_html += f"<div class='callout bad'><strong>Must fix first</strong><ul>{errors}</ul></div>"
    if warnings:
        checks_html += f"<div class='callout warn'><strong>Please note</strong><ul>{warnings}</ul></div>"
    action = ""
    if check.ready:
        action = (
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/publish-to-ebay' class='publish-confirm'>"
            f"<label><input type='checkbox' name='confirmation' value='publish' required> "
            f"I understand this will create or resume a live listing on eBay {escape(environment)}.</label>"
            "<button class='btn'>Publish live to eBay</button></form>"
        )
    body = (
        brand_header("Review eBay publication")
        + f"<p class='subtitle'>{status} Direct Inventory API · {escape(environment)}. "
        + "CSV remains available for bulk and recovery work.</p>"
        + checks_html
        + "<div class='panel ebay-preview'>"
        + image
        + "<div>"
        + f"<h2>{escape(listing.title)}</h2>"
        + f"<p>{listing.quantity} × AUD ${listing.price} · category {escape(check.category_id or 'unresolved')}</p>"
        + f"<p>{escape(listing.description or listing.title)}</p>"
        + action
        + "<p><a class='btn small ghost' href='/ebay-export'>Use CSV fallback</a> "
        + f"<a class='btn small ghost' href='/shop-listings?status={escape(listing.status.value)}'>Back</a></p>"
        + "</div></div>"
        + "<style>.ebay-preview{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}.ebay-preview-image{width:180px;max-height:250px;object-fit:contain;border-radius:8px}.ebay-preview-image.empty{height:220px;background:var(--surface-sunken);display:flex;align-items:center;justify-content:center;text-align:center;padding:12px}.publish-confirm{display:flex;flex-direction:column;gap:12px;margin:18px 0}.callout ul{margin-bottom:0}</style>"
    )
    return HTMLResponse(page("EzBay — Review eBay publication", body))


@router.post("/shop-listings/{listing_id}/publish-to-ebay")
async def publish_to_ebay(
    listing_id: str,
    confirmation: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    # One uvicorn worker serves this app. A per-SKU lock closes the remaining
    # double-click race where two requests could both see no remote offer
    # before either createOffer response arrives. Crash recovery is handled
    # separately by the persisted PUBLISHING link and getOffers-by-SKU path.
    lock = _PUBLISH_LOCKS.setdefault(listing_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="this listing is already being published")
    async with lock:
        return await _publish_to_ebay_locked(listing_id, confirmation, user, session)


async def _publish_to_ebay_locked(
    listing_id: str, confirmation: str, user: str, session: Session,
):
    settings = get_settings()
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")

    check = publication_preflight(session, listing, settings)
    if not check.ready:
        raise HTTPException(status_code=400, detail="; ".join(check.errors))
    if confirmation != "publish":
        raise HTTPException(status_code=400, detail="review and confirm the live eBay publication first")

    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    card_set = session.get(CardSet, tcg_card.set_id)
    inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    image_url = _prepare_image(session, listing, catalog_item, settings)

    approval = Approval(
        requested_at=datetime.now(UTC),
        requested_by=user,
        action="ebay.publish_listing",
        arguments={"shop_listing_id": listing_id, "environment": settings.ebay_env},
    )
    session.add(approval)
    session.commit()
    decide_approval(session, approval, approved=True, actor=user)

    existing_link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing_id))
    link = existing_link or EbayListingLink(
        shop_listing_id=listing_id,
        sku=listing_id,
        marketplace_id=settings.ebay_marketplace_id,
    )
    link.status = EbayListingLinkStatus.PUBLISHING
    link.approval_id = approval.id
    link.last_error = None
    session.add(link)
    session.commit()

    inventory_payload = _build_inventory_item_payload(
        listing, catalog_item, tcg_card, card_set, inventory_item, image_url,
    )
    offer_payload = _build_offer_payload(listing, link.sku, check.category_id, settings)
    adapter = None
    try:
        cipher, oauth = ebay_services(settings)
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
        await adapter.upsert_inventory_item(link.sku, inventory_payload)

        remote_offer = None
        if link.ebay_offer_id:
            remote_offer = await adapter.get_offer(link.ebay_offer_id)
        else:
            offers = [
                offer
                for offer in await adapter.get_offers(link.sku)
                if offer.get("sku") in (None, link.sku)
                and offer.get("marketplaceId") in (None, settings.ebay_marketplace_id)
            ]
            if len(offers) > 1:
                raise EbayApiError(
                    f"found {len(offers)} eBay offers for SKU {link.sku}; "
                    "resolve the duplicate offers before retrying"
                )
            if offers:
                remote_offer = offers[0]
                link.ebay_offer_id = remote_offer.get("offerId")
                if not link.ebay_offer_id:
                    raise EbayApiError("eBay returned an existing offer without offerId")
                session.commit()

        if remote_offer and (published_id := _listing_id_if_published(remote_offer)):
            ebay_listing_id = published_id
        else:
            if link.ebay_offer_id:
                await adapter.update_offer(link.ebay_offer_id, offer_payload)
            else:
                link.ebay_offer_id = await adapter.create_offer(offer_payload)
                session.commit()
            ebay_listing_id = await adapter.publish_offer(link.ebay_offer_id)
    except (EbayApiError, EbayNotConfigured, OAuthError) as exc:
        link.status = EbayListingLinkStatus.FAILED
        link.last_error = str(exc)
        session.commit()
        record_event(
            session,
            actor_type="user",
            actor_id=user,
            action="ebay.publish_listing",
            resource_type="shop_listing",
            resource_id=listing_id,
            outcome="failed",
            correlation_id=str(uuid.uuid4()),
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=f"eBay publish failed: {exc}") from exc
    finally:
        if adapter is not None:
            await adapter.aclose()

    _finalize_active_link(session, link, listing, ebay_listing_id)
    record_event(
        session,
        actor_type="user",
        actor_id=user,
        action="ebay.publish_listing",
        resource_type="shop_listing",
        resource_id=listing_id,
        outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"ebay_listing_id": ebay_listing_id, "offer_id": link.ebay_offer_id},
    )
    return RedirectResponse("/shop-listings", status_code=303)
