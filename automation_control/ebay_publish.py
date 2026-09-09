"""Publish a ShopListing to eBay -- Phase 3 of the storefront <-> eBay
two-way stock sync (see .claude/plans/idempotent-churning-bumblebee.md
for the full picture). The one truly cost-bearing action in that plan
(creating a live, potentially-billable eBay listing), so it's gated
behind an Approval record exactly like listings_review.py's existing
"a human clicking the button here *is* the approval decision" pattern --
reusing that mechanism rather than inventing a second one.

Deliberately separate from shop_listings_admin.py: that file is the
storefront-only Activate/Withdraw review queue and doesn't need to know
eBay exists. This module owns the one new button that appears alongside
those existing actions.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError
from .adapters.ebay_write import EbayWriteAdapter
from .approvals import decide_approval
from .audit import record_event
from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .ebay_oauth import configured_for_writes, ebay_services, valid_user_access_token
from .models import (
    Approval, CatalogItem, EbayCategoryMapping, EbayListingLink, EbayListingLinkStatus,
    InventoryItem, ShopListing, ShopListingStatus, TcgCard,
)

router = APIRouter(tags=["ebay-publish"])


def _resolve_category(session: Session, catalog_item: CatalogItem, tcg_card: TcgCard | None, marketplace_id: str) -> str | None:
    """(marketplace, item_type, game) -> (marketplace, item_type, NULL), so
    one seeded fallback row covers a brand-new game with zero config."""
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


def _build_inventory_item_payload(listing: ShopListing, catalog_item: CatalogItem, inventory_item: InventoryItem | None) -> dict:
    # Fixed default for now -- real per-condition eBay condition-id mapping
    # (raw vs. graded, NM vs. LP, ...) is a real future need, out of scope
    # for proving the write path works end to end.
    condition = "LIKE_NEW" if inventory_item and inventory_item.grading_company else "USED_EXCELLENT"
    return {
        "condition": condition,
        "product": {
            "title": listing.title[:80],
            "description": listing.description or listing.title,
            "imageUrls": [listing.image_url] if listing.image_url else [],
        },
        "availability": {"shipToLocationAvailability": {"quantity": listing.quantity}},
    }


def _build_offer_payload(listing: ShopListing, sku: str, category_id: str, settings) -> dict:
    return {
        "sku": sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
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


@router.post("/shop-listings/{listing_id}/publish-to-ebay")
async def publish_to_ebay(
    listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    settings = get_settings()
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if listing.status not in (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT):
        raise HTTPException(status_code=400, detail="only an active or draft listing can be published to eBay")

    existing_link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing_id))
    if existing_link and existing_link.status in (EbayListingLinkStatus.ACTIVE, EbayListingLinkStatus.PUBLISHING):
        raise HTTPException(status_code=400, detail=f"already {existing_link.status.value} on eBay")

    if not configured_for_writes(settings):
        raise HTTPException(status_code=503, detail="eBay listing policies are not fully configured -- see config.py's ebay_*_policy_id / ebay_merchant_location_key settings")

    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    category_id = _resolve_category(session, catalog_item, tcg_card, settings.ebay_marketplace_id)
    if category_id is None:
        raise HTTPException(status_code=503, detail=f"no eBay category mapping for item_type={catalog_item.item_type.value!r} game={tcg_card.game if tcg_card else None!r} -- see scripts/seed_ebay_categories.py")

    # A human clicking Publish here *is* the approval decision -- same
    # pattern as listings_review.py's Approve button. Unlike that route,
    # this one actually calls eBay afterward, since the write path it
    # deferred to is exactly what this module implements.
    approval = Approval(requested_at=datetime.now(UTC), requested_by=user, action="ebay.publish_listing",
                        arguments={"shop_listing_id": listing_id})
    session.add(approval)
    session.commit()
    decide_approval(session, approval, approved=True, actor=user)

    link = existing_link or EbayListingLink(shop_listing_id=listing_id, sku=listing_id, marketplace_id=settings.ebay_marketplace_id)
    link.status = EbayListingLinkStatus.PUBLISHING
    link.approval_id = approval.id
    link.last_error = None
    session.add(link)
    session.commit()

    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
        await adapter.upsert_inventory_item(link.sku, _build_inventory_item_payload(listing, catalog_item, inventory_item))
        offer_id = await adapter.create_offer(_build_offer_payload(listing, link.sku, category_id, settings))
        link.ebay_offer_id = offer_id
        session.commit()
        ebay_listing_id = await adapter.publish_offer(offer_id)
    except EbayApiError as exc:
        link.status = EbayListingLinkStatus.FAILED
        link.last_error = str(exc)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="ebay.publish_listing", resource_type="shop_listing",
                    resource_id=listing_id, outcome="failed", correlation_id=str(uuid.uuid4()), details={"error": str(exc)})
        raise HTTPException(status_code=502, detail=f"eBay publish failed: {exc}") from exc

    link.ebay_listing_id = ebay_listing_id
    link.status = EbayListingLinkStatus.ACTIVE
    link.last_pushed_quantity = listing.quantity
    link.last_synced_at = datetime.now(UTC)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="ebay.publish_listing", resource_type="shop_listing",
                resource_id=listing_id, outcome="success", correlation_id=str(uuid.uuid4()),
                details={"ebay_listing_id": ebay_listing_id, "offer_id": offer_id})
    return RedirectResponse("/shop-listings", status_code=303)
