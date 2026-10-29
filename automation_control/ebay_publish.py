"""Safe, resumable publication of one canonical ShopListing to eBay.

The Inventory API call creates a real, potentially billable production
listing. A read-only preview therefore runs every local preflight first and
the write endpoint requires an explicit confirmation value. The link row is
persisted before the first eBay write and retries discover/reuse an offer by
SKU, so a process crash between createOffer and publishOffer cannot create a
second offer on the next click.
"""

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy import select
from sqlalchemy.orm import Session

from scripts.convert_to_ebay_template import (
    CARD_CONDITION_DESCRIPTOR_NEAR_MINT,
    EBAY_ITEM_SPECIFIC_MAX_LEN,
    GAME_DISPLAY_NAMES,
    GAME_ITEM_SPECIFICS,
    sanitize_title,
)
from .adapters.ebay import EbayApiError, EbayNotConfigured
from .adapters.ebay_trading_write import EbayTradingListingAdapter
from .adapters.ebay_write import EbayWriteAdapter
from .adapters.r2 import R2UploadError, ensure_uploaded, is_configured as r2_is_configured
from .approvals import decide_approval
from .listing_finishes import FINISHES, finish_field, finish_label, save_finish
from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import RecognitionError, suggest_ebay_listing_copy
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
from .images import serve_image
from .listing_quality import (
    MAX_EBAY_PHOTOS,
    ListingPhotoSource,
    ListingQuality,
    assess_listing_quality,
    australian_active_listings_url,
    australian_sold_listings_url,
    generated_ebay_description,
    generated_ebay_title,
    listing_photo_sources,
    outgoing_ebay_description,
    outgoing_ebay_title,
)
from .models import (
    Approval,
    CardSet,
    CatalogItem,
    CatalogItemType,
    EbayCategoryMapping,
    EbayCredential,
    EbayExportItem,
    EbayExportItemStatus,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)

from .pricing_safety import assess_listing_price
from .ui import brand_header, page, pill

register_heif_opener()

router = APIRouter(tags=["ebay-publish"])

EBAY_MIN_FIXED_PRICE = Decimal("1.00")
LIVE_REVISION_MIN_PRICE_RATIO = Decimal("0.85")
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}

# eBay category 183454 keeps every raw card under ConditionID 4000 and
# carries the collector-facing grade in condition descriptor 40001.  These
# are eBay's published AU-compatible values for that category.
UNGRADED_CARD_CONDITION_DESCRIPTORS = {
    "near mint": CARD_CONDITION_DESCRIPTOR_NEAR_MINT,
    "lightly played": "400015",
    "moderately played": "400016",
    "heavily played": "400017",
}


def _ungraded_condition_descriptor(condition: str | None) -> str | None:
    normalized = (condition or "").casefold().replace("-", " ")
    for label, descriptor in UNGRADED_CARD_CONDITION_DESCRIPTORS.items():
        if label in normalized:
            return descriptor
    return None


@dataclass(frozen=True)
class PublishPreflight:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    category_id: str | None
    photos: tuple[ListingPhotoSource, ...]
    image_will_upload: bool
    quality: ListingQuality | None = None

    @property
    def ready(self) -> bool:
        return not self.errors

    @property
    def image_url(self) -> str | None:
        """Compatibility convenience for older callers and tests."""
        return self.photos[0].value if self.photos else None


def _studio_photo_url(listing_id: str, index: int, photo: ListingPhotoSource) -> str:
    """A position URL whose version follows the underlying immutable file.

    Photo order is editable, so ``/ebay-photo/0`` can point at a different
    file after a delete or move. Phones otherwise keep the prior response for
    a week and show the removed image over the next photo's Remove button.
    """
    if photo.is_remote:
        return photo.value
    version = uuid.uuid5(uuid.NAMESPACE_URL, photo.value).hex[:12]
    return f"/shop-listings/{listing_id}/ebay-photo/{index}?v={version}"


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


def _current_live_ebay_price(session: Session, listing_id: str) -> Decimal | None:
    """Return the mirrored AU price for the exact live item behind a listing."""
    link = session.scalar(
        select(EbayListingLink).where(
            EbayListingLink.shop_listing_id == listing_id,
            EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
            EbayListingLink.ebay_listing_id.is_not(None),
        )
    )
    ebay_listing_id = link.ebay_listing_id if link else None
    if not ebay_listing_id:
        export = session.scalar(
            select(EbayExportItem)
            .where(
                EbayExportItem.sku == listing_id,
                EbayExportItem.kind == "single",
                EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
                EbayExportItem.ebay_listing_id.is_not(None),
            )
            .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
            .limit(1)
        )
        ebay_listing_id = export.ebay_listing_id if export else None
    if not ebay_listing_id:
        return None
    remote = session.scalar(
        select(EbayListing).where(
            EbayListing.ebay_listing_id == ebay_listing_id,
            EbayListing.listing_status == "Active",
        )
    )
    if remote is None or remote.currency != "AUD":
        return None
    return Decimal(remote.current_price_minor) / 100


def publication_preflight(
    session: Session, listing: ShopListing, settings, *, price_override: Decimal | None = None,
    allow_active_revision: bool = False, allow_pricing_safety_override: bool = False,
) -> PublishPreflight:
    errors: list[str] = []
    warnings: list[str] = []

    if listing.status not in (ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT):
        errors.append("Only an active or draft listing can be published.")
    if listing.quantity < 1:
        errors.append("Quantity must be at least one.")
    if listing.currency != "AUD":
        errors.append("The EBAY_AU flow currently supports AUD listings only.")
    effective_price = price_override if price_override is not None else listing.price
    if effective_price < EBAY_MIN_FIXED_PRICE:
        errors.append("eBay fixed-price listings must be at least AUD $1.00.")
    pricing_safety = assess_listing_price(
        session, listing, proposed_price=effective_price,
    )
    if pricing_safety.blocked:
        current_live_price = (
            _current_live_ebay_price(session, listing.id)
            if allow_active_revision else None
        )
        modest_live_revision = (
            current_live_price is not None
            and effective_price >= current_live_price * LIVE_REVISION_MIN_PRICE_RATIO
        )
        if allow_pricing_safety_override:
            warnings.append(
                f"Pricing safety override selected: AU ${effective_price:.2f} is below the "
                f"AU ${pricing_safety.review_floor:.2f} review floor. The override will be "
                "recorded in the listing audit."
            )
        elif modest_live_revision:
            price_change = current_live_price - effective_price
            change_note = (
                "does not reduce"
                if price_change <= 0 else
                f"reduces by only AU ${price_change:.2f}"
            )
            warnings.append(
                f"Pricing review recommended: the proposed AU ${effective_price:.2f} is below the "
                f"market safety floor of AU ${pricing_safety.review_floor:.2f}, but it {change_note} "
                f"the current live eBay price of AU ${current_live_price:.2f}. "
                "The explicitly reviewed revision is allowed."
            )
        else:
            errors.append(pricing_safety.message())
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
    photos: list[ListingPhotoSource] = []
    image_will_upload = False
    quality = None
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
            card_set = session.get(CardSet, tcg_card.set_id) if tcg_card.set_id else None
            if not tcg_card.set_id or card_set is None:
                errors.append("The card set is missing.")
            if not tcg_card.number:
                errors.append("The card number is missing.")
            authenticity_text = " ".join(filter(None, (
                tcg_card.rarity, tcg_card.variant, card_set.name if card_set else None,
            ))).casefold()
            if "unofficial" in authenticity_text:
                errors.append(
                    "This catalogue record is classified as unofficial. eBay prohibits "
                    "unauthorised trading-card reprints and branded inspired-by items."
                )
        category_id = _resolve_category(session, catalog_item, tcg_card, settings.ebay_marketplace_id)
        if category_id is None:
            game = tcg_card.game if tcg_card else None
            errors.append(
                f"No eBay category mapping exists for item type {catalog_item.item_type.value} / game {game}."
            )

        photos = listing_photo_sources(session, listing, catalog_item)
        own_photos = [photo for photo in photos if photo.is_own_photo]
        if not own_photos:
            errors.append("Add a photograph of the actual card; reference/catalogue art cannot be published for this ungraded item.")
        else:
            local_photos = [photo for photo in own_photos if not photo.is_remote]
            insecure = [photo.label for photo in own_photos if photo.is_remote and not photo.value.startswith("https://")]
            if insecure:
                errors.append("eBay photo URLs must use HTTPS: " + ", ".join(insecure))
            missing = [photo.label for photo in local_photos if not Path(photo.value).is_file()]
            if missing:
                errors.append("Photo file missing: " + ", ".join(missing))
            if local_photos:
                if not r2_is_configured(settings):
                    errors.append("R2 image hosting must be configured before local card photos can be published.")
                else:
                    image_will_upload = True
                    warnings.append(
                        f"{len(local_photos)} local card photo(s) will upload to the public image bucket only after confirmation."
                    )

        if tcg_card is not None:
            card_set = session.get(CardSet, tcg_card.set_id) if tcg_card.set_id else None
            title = outgoing_ebay_title(listing, catalog_item, tcg_card, card_set, inventory_item)
            description = outgoing_ebay_description(
                listing, catalog_item, tcg_card, card_set, inventory_item,
                has_own_photo=bool(own_photos),
            )
            quality = assess_listing_quality(
                title, description, photos, tcg_card, card_set,
                listing.condition or (inventory_item.condition if inventory_item else None),
            )
            if not sanitize_title(title).strip():
                errors.append("The eBay title becomes empty after safe-character cleanup.")
            for failed in quality.blockers:
                if failed.label in {"Actual-card photograph", "Card identity", "Condition"}:
                    continue  # already covered above by a more specific preflight message
                errors.append(f"{failed.label}: {failed.detail}")

    if inventory_item and inventory_item.grading_company:
        errors.append("Graded-card condition descriptors are not implemented; use CSV fallback.")
    condition = listing.condition or (inventory_item.condition if inventory_item else "")
    if tcg_card is not None and _ungraded_condition_descriptor(condition) is None:
        errors.append(
            "Direct publishing supports Near Mint, Lightly Played, Moderately Played, "
            "or Heavily Played ungraded card conditions."
        )

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    if link:
        if link.status == EbayListingLinkStatus.ACTIVE:
            if not allow_active_revision:
                errors.append("This listing is already active on eBay.")
        elif link.status == EbayListingLinkStatus.ENDING:
            errors.append("This listing is currently being ended on eBay.")
        elif link.status == EbayListingLinkStatus.PUBLISHING:
            warnings.append("A prior attempt stopped while publishing; this run will discover and resume its offer.")
        elif link.status in (EbayListingLinkStatus.FAILED, EbayListingLinkStatus.ENDED):
            if not allow_active_revision:
                warnings.append("This run will safely reuse or recreate the prior eBay offer.")
    else:
        latest_export = session.scalar(
            select(EbayExportItem)
            .where(EbayExportItem.sku == listing.id)
            .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
            .limit(1)
        )
        if latest_export and latest_export.status == EbayExportItemStatus.CONFIRMED and not allow_active_revision:
            errors.append(
                "This item is already live through Bulk Upload. Direct publish is blocked to prevent a duplicate listing."
            )
        elif latest_export and latest_export.status == EbayExportItemStatus.PENDING:
            errors.append(
                "This item is awaiting a Bulk Upload result. Import or cancel that batch before direct publishing."
            )

    return PublishPreflight(tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)), category_id,
                            tuple(photos), image_will_upload, quality)


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
    image_urls: list[str],
    title: str,
    description: str,
) -> dict:
    condition = listing.condition or (inventory_item.condition if inventory_item else None)
    condition_descriptor = _ungraded_condition_descriptor(condition)
    if condition_descriptor is None:
        raise ValueError(f"unsupported ungraded card condition: {condition or 'missing'}")
    return {
        # Category 183454 uses numeric condition 4000 for ungraded cards;
        # Inventory API represents it as USED_VERY_GOOD and requires the
        # structured 40001 card-condition descriptor as well.
        "condition": "USED_VERY_GOOD",
        "conditionDescriptors": [
            {"name": "40001", "values": [condition_descriptor]},
        ],
        "product": {
            "title": title,
            "description": description,
            "aspects": {**_card_aspects(catalog_item, tcg_card, card_set, inventory_item),
                        **({"Finish": [finish_label(listing)]} if finish_label(listing) else {})},
            "imageUrls": image_urls,
        },
        "availability": {"shipToLocationAvailability": {"quantity": listing.quantity}},
    }


def _build_offer_payload(
    listing: ShopListing, sku: str, category_id: str, settings, *, description: str,
    price_override: Decimal | None = None,
) -> dict:
    effective_price = price_override if price_override is not None else listing.price
    return {
        "sku": sku,
        "marketplaceId": settings.ebay_marketplace_id,
        "format": "FIXED_PRICE",
        # These are seller-described, photographed singles.  Leaving this at
        # eBay's true default makes publishOffer try to resolve an eBay
        # catalogue product even when no ePID/GTIN was supplied, which fails
        # for legitimate vintage cards with the opaque 25604 "Product not
        # found" response.
        "includeCatalogProductDetails": False,
        "listingDuration": "GTC",
        "availableQuantity": listing.quantity,
        "categoryId": category_id,
        "listingDescription": description,
        "listingPolicies": {
            "fulfillmentPolicyId": settings.ebay_fulfillment_policy_id,
            "paymentPolicyId": settings.ebay_payment_policy_id,
            "returnPolicyId": settings.ebay_return_policy_id,
        },
        "pricingSummary": {"price": {"value": str(effective_price), "currency": listing.currency}},
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
    # A successful eBay publication is no longer an unpublished local draft.
    # Later copy/price edits remain proposals without reverting this status.
    if listing.status == ShopListingStatus.DRAFT:
        listing.status = ShopListingStatus.ACTIVE
    session.commit()


def _prepare_images(
    session: Session, listing: ShopListing, catalog_item: CatalogItem, settings,
) -> list[str]:
    sources = [photo for photo in listing_photo_sources(session, listing, catalog_item) if photo.is_own_photo]
    if not sources:
        raise HTTPException(status_code=400, detail="no actual-card photograph is available")
    urls: list[str] = []
    for index, source in enumerate(sources[:MAX_EBAY_PHOTOS], 1):
        if source.is_remote:
            if not source.value.startswith("https://"):
                raise HTTPException(status_code=400, detail="eBay photo URLs must use HTTPS")
            urls.append(source.value)
            continue
        path = Path(source.value)
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"photo {index} is missing or not a supported JPG/PNG")
        try:
            urls.append(ensure_uploaded(
                path,
                f"listing-studio/{listing.id}/{index}{path.suffix.lower()}",
                endpoint_url=settings.r2_endpoint_url,
                access_key_id=settings.r2_access_key_id,
                secret_access_key=settings.r2_secret_access_key,
                bucket_name=settings.r2_bucket_name,
                public_base_url=load_pipeline_config().images.base_url,
            ))
        except (R2UploadError, OSError) as exc:
            raise HTTPException(status_code=502, detail=f"photo {index} upload failed: {exc}") from exc
    return urls


_LISTING_STUDIO_SCRIPT = """<script>
(() => {
  const title = document.getElementById('ebay-title');
  const count = document.getElementById('title-count');
  if (!title || !count) return;
  const update = () => {
    const length = [...title.value].length;
    count.textContent = `${length}/80`;
    count.className = length >= 65 && length <= 80 ? 'count-good' : 'count-warn';
  };
  title.addEventListener('input', update);
  update();
})();
</script>"""


_LISTING_STUDIO_STYLE = """<style>
.studio-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:16px;margin:16px 0}
.studio-editor h2,.studio-quality h2,.photo-heading h2{margin-top:0}.studio-editor form{display:grid;gap:14px}
.studio-field{display:grid;gap:6px;font-size:13px;color:var(--text-dim)}
.studio-field input,.studio-field select,.studio-field textarea{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;
  border:1px solid var(--panel-border);background:var(--surface-sunken);color:var(--text);font:inherit}
.studio-field textarea{resize:vertical;line-height:1.45}.studio-field span{float:right}.count-good{color:var(--success)}
.count-warn{color:var(--warning)}.studio-suggestion{margin:-7px 0 0;color:var(--text-dim);font-size:12px}
.studio-short-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px}.studio-short-fields .wide{grid-column:1/-1}
.studio-actions,.photo-controls,.photo-controls form{display:flex;gap:7px;flex-wrap:wrap}.studio-actions{margin-top:3px}
.ai-suggest{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:16px 0}.ai-suggest h2{margin:0 0 4px}
.studio-editor details{font-size:12px;color:var(--text-dim)}.studio-editor pre{white-space:pre-wrap;font:inherit;line-height:1.45}
.quality-score{display:flex;gap:10px;align-items:baseline;margin-bottom:12px}.quality-score strong{font-size:28px}
.quality-list{list-style:none;padding:0;margin:0;display:grid;gap:8px}.quality-list li{padding:9px 10px;border-radius:8px;
  background:var(--surface-sunken);display:grid;gap:2px}.quality-list small{color:var(--text-dim)}
.quality-pass span{color:var(--success)}.quality-block span{color:var(--danger)}.quality-warn span{color:var(--warning)}
.photo-heading{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
.photo-upload-actions,.photo-upload{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.camera-choice{cursor:pointer}
.camera-choice input{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.studio-gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:12px}
.studio-photo{border:1px solid var(--panel-border);border-radius:10px;padding:8px;background:var(--surface-sunken)}
.studio-photo img{width:100%;aspect-ratio:1/1;object-fit:contain;background:#fff;border-radius:7px;display:block}
.studio-photo.empty{min-height:180px;display:grid;place-items:center;color:var(--text-dim)}
.photo-position{display:block;margin-top:8px;font-size:12px}.photo-controls{margin-top:6px}
.photo-controls form:first-of-type{flex:1}.photo-controls .btn{min-height:36px}
.photo-badge{display:inline-block;margin:7px 0 5px;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700}
.photo-badge.own{background:var(--success-bg);color:var(--success)}.photo-badge.ref{background:var(--warning-bg);color:var(--warning)}
.buyer-preview{display:grid;grid-template-columns:minmax(240px,42%) 1fr;gap:24px;margin-top:16px;background:#fff;color:#111}
.buyer-photo{background:#f7f7f7;min-height:360px;display:grid;place-items:center;border-radius:9px;overflow:hidden}
.buyer-photo img{width:100%;height:100%;max-height:520px;object-fit:contain}.buyer-copy h1{font-size:22px;line-height:1.25}
.preview-label{text-transform:uppercase;letter-spacing:.08em;font-size:10px;color:#666}.buyer-price{font-size:28px;font-weight:700;margin:18px 0}
.buyer-description{margin-top:22px;line-height:1.5;font-size:14px}.publish-confirm{display:flex;flex-direction:column;gap:12px;margin:18px 0}
.callout ul{margin-bottom:0}
@media(max-width:760px){.studio-grid,.buyer-preview{grid-template-columns:1fr}.studio-short-fields{grid-template-columns:1fr}
  .buyer-photo{min-height:280px}.ai-suggest{align-items:stretch;flex-direction:column}.photo-upload-actions{width:100%}
  .photo-upload,.photo-upload .btn{flex:1}.photo-upload .btn{justify-content:center}}
</style>"""


@router.get("/ebay-listing-cleanup", response_class=HTMLResponse)
def ebay_listing_cleanup(
    show: str = "",
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Read-only quality inventory for what eBay currently reports as active.

    A Complete-Your-Master-Set parent represents a whole variation group,
    rather than one physical card. Its shared photos are deliberately ignored
    here. Individual card listings at AU$50+ receive enhanced photo review.
    """
    enhanced_photo_min = Decimal("50.00")
    live = list(session.scalars(
        select(EbayListing).where(EbayListing.listing_status == "Active").order_by(EbayListing.title)
    ))
    live_ids = [row.ebay_listing_id for row in live]
    direct_links = {
        link.ebay_listing_id: link.shop_listing_id
        for link in session.scalars(
            select(EbayListingLink).where(EbayListingLink.ebay_listing_id.in_(live_ids))
        )
        if link.ebay_listing_id
    } if live_ids else {}

    # Only accept export SKUs that resolve to a real canonical ShopListing.
    # A variation parent SKU (set-variation-...) is a group identifier, not a
    # single listing, and was previously mistaken for a broken link here.
    export_links: dict[str, str] = {}
    if live_ids:
        exported = session.execute(
            select(EbayExportItem.ebay_listing_id, EbayExportItem.sku)
            .join(ShopListing, ShopListing.id == EbayExportItem.sku)
            .where(
                EbayExportItem.ebay_listing_id.in_(live_ids),
                EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            )
            .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
        )
        for ebay_listing_id, sku in exported:
            if ebay_listing_id:
                export_links.setdefault(ebay_listing_id, sku)

    show_all = show == "all"
    single_rows: list[tuple[int, str]] = []
    bulk_rows: list[tuple[int, str]] = []
    linked = unverified_descriptions = bulk_count = high_value_singles = photo_work = 0
    cleared_count = 0
    for remote in live:
        is_complete_set = "complete your master set" in remote.title.casefold()
        if is_complete_set:
            bulk_count += 1
        listing_id = direct_links.get(remote.ebay_listing_id) or export_links.get(remote.ebay_listing_id)
        listing = session.get(ShopListing, listing_id) if listing_id else None
        issues: list[str] = []
        if len(remote.title.strip()) < 60:
            issues.append(f"Short title ({len(remote.title.strip())}/80)")

        snapshot = remote.raw_snapshot or {}
        description = str(snapshot.get("description") or "").strip()
        image_urls = snapshot.get("image_urls") or []
        if not is_complete_set and not description:
            unverified_descriptions += 1
            issues.append("Description not cached/verified")

        proposal = ""
        local_photo_note = ""
        card = None
        has_actual_photo = False
        if listing:
            linked += 1
            item = session.get(CatalogItem, listing.catalog_item_id)
            card = session.get(TcgCard, listing.catalog_item_id)
            card_set = session.get(CardSet, card.set_id) if card and card.set_id else None
            inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
            sources = listing_photo_sources(session, listing, item)
            has_actual_photo = any(photo.is_own_photo for photo in sources)
            if item and card:
                proposed_title = outgoing_ebay_title(listing, item, card, card_set, inventory)
                if proposed_title != remote.title:
                    proposal = f"<div class='cleanup-proposal'><b>Proposed:</b> {escape(proposed_title)}</div>"
            if sources:
                local_photo_note = f" · {sum(1 for photo in sources if photo.is_own_photo)} local actual photo(s)"
        elif not is_complete_set:
            issues.append("No canonical ShopListing link")

        price = Decimal(remote.current_price_minor) / 100
        title_lower = f" {remote.title.casefold()} "
        likely_card = bool(card) or any(marker in title_lower for marker in (
            " pokemon ", " pokémon ", " one piece ", " dragon ball ", " tcg ",
            " fusion world ", " db heroes ", " super divers ",
        ))
        needs_enhanced_photos = not is_complete_set and likely_card and price >= enhanced_photo_min
        if needs_enhanced_photos:
            high_value_singles += 1
            needs_photo_work = False
            if not image_urls:
                issues.append("eBay photos not cached/verified")
                needs_photo_work = True
            if not has_actual_photo:
                issues.append("No local actual-card photo")
                needs_photo_work = True
            if needs_photo_work:
                issues.append(f"High-value single (AU ${price:,.2f})")
                photo_work += 1
        elif is_complete_set:
            issues.append("Title-only bulk review")

        review_fingerprint = _cleanup_review_fingerprint(
            remote, listing_id=listing.id if listing else None,
            proposed_title=proposed_title if listing and item and card else None,
            has_actual_photo=has_actual_photo,
        )
        review = snapshot.get("cleanup_review") or {}
        is_reviewed = (
            review.get("status") == "dismissed"
            and review.get("fingerprint") == review_fingerprint
        )
        if is_reviewed or not issues:
            cleared_count += 1
        if not show_all and (is_reviewed or not issues):
            continue

        severity = len(issues)
        issue_html = "".join(f"<span>{escape(issue)}</span>" for issue in issues) or "<span class='ok'>No obvious issue</span>"
        primary_action = (
            f"<a class='btn small' href='/shop-listings/{escape(listing.id)}/ebay-preview'>Open Listing Studio</a>"
            if listing and not is_complete_set else
            f"<a class='btn small ghost' href='/ebay-listings/{escape(remote.ebay_listing_id)}'>Inspect cached listing</a>"
        )
        reviewed_label = "Reviewed" if is_reviewed else "Mark reviewed"
        action = (
            "<div class='cleanup-actions'>" + primary_action
            + f"<a class='btn small ghost' href='{escape(australian_sold_listings_url(remote.title), quote=True)}' target='_blank' rel='noopener noreferrer'>Sold listings · Australia</a>"
            + f"<form method='post' action='/ebay-listing-cleanup/{escape(remote.ebay_listing_id)}/dismiss'>"
            + f"<input type='hidden' name='fingerprint' value='{review_fingerprint}'>"
            + f"<button class='btn small ghost' type='submit'>{reviewed_label}</button></form></div>"
        )
        review_kind = "bulk" if is_complete_set else "single"
        photo_summary = (
            "photos intentionally ignored for this bulk group"
            if is_complete_set else
            f"{len(image_urls)} cached eBay photo(s){local_photo_note}"
        )
        html = (
            f"<article class='cleanup-card' data-review-kind='{review_kind}'>"
            f"<div><div class='cleanup-title'>{escape(remote.title)}</div>"
            f"<div class='muted'>eBay {escape(remote.ebay_listing_id)} · AU ${price:,.2f} · {photo_summary}</div>"
            f"{proposal}<div class='cleanup-issues'>{issue_html}</div></div>{action}</article>"
        )
        (bulk_rows if is_complete_set else single_rows).append((severity, html))

    single_rows.sort(key=lambda row: row[0], reverse=True)
    bulk_rows.sort(key=lambda row: row[0], reverse=True)
    singles = "".join(html for _, html in single_rows) or "<div class='panel'>No individual listings are cached.</div>"
    bulk = "".join(html for _, html in bulk_rows) or "<div class='panel'>No Complete Your Master Set listings are cached.</div>"
    body = (
        brand_header("eBay listing cleanup")
        + "<p class='subtitle'>Singles first: clean up every title and description, with actual-card photo work prioritised from AU$50. Complete-your-set bulk listings are title-only. Reviewed and issue-free listings stay hidden unless their details change; nothing on this page writes to eBay.</p>"
        + "<p><a class='btn' href='/ebay-listing-bulk-photos'>Photograph &amp; store cards</a> <a class='btn ghost' href='/shop-listings'>Back to Listing Studio</a></p>"
        + "<div class='cleanup-stats'>"
        + f"<div><strong>{len(live)}</strong><span>active on eBay</span></div>"
        + f"<div><strong>{len(single_rows)}</strong><span>individual listings</span></div>"
        + f"<div><strong>{bulk_count}</strong><span>bulk groups · title only</span></div>"
        + f"<div><strong>{high_value_singles}</strong><span>card singles AU$50+</span></div>"
        + f"<div><strong>{photo_work}</strong><span>priority photo work</span></div>"
        + f"<div><strong>{unverified_descriptions}</strong><span>single descriptions unverified</span></div>"
        + f"<div><strong>{cleared_count}</strong><span>cleared from review</span></div></div>"
        + f"<p class='subtitle'>{linked} listings are linked to Listing Studio. “Unverified” means the CRM cache lacks that field; it does not automatically mean the live listing is empty.</p>"
        + ("<p><a class='btn small ghost' href='/ebay-listing-cleanup'>Hide cleared listings</a></p>" if show_all else "<p><a class='btn small ghost' href='/ebay-listing-cleanup?show=all'>Show cleared listings</a></p>")
        + f"<section class='cleanup-section'><h2>Individual listings</h2><p class='subtitle'>Title and description review for every listing. Actual-card photos become priority work at AU$50.</p><div class='cleanup-list'>{singles}</div></section>"
        + f"<details class='cleanup-bulk'><summary>Complete Your Master Set · title-only ({bulk_count})</summary><p class='subtitle'>Shared bulk photos and descriptions are deliberately ignored. Review only the parent title.</p><div class='cleanup-list'>{bulk}</div></details>"
        + "<p><a class='btn small ghost' href='/shop-listings?status=active&amp;ebay=live'>Back to live listings</a></p>"
        + """<style>
.cleanup-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:16px 0}
.cleanup-stats div{display:grid;gap:2px;padding:14px;border:1px solid var(--panel-border);border-radius:11px;background:var(--panel)}
.cleanup-stats strong{font-size:24px}.cleanup-stats span{font-size:11px;color:var(--text-dim)}
.cleanup-section{margin-top:24px}.cleanup-section h2{margin-bottom:4px}.cleanup-bulk{margin-top:28px}
.cleanup-bulk>summary{font-size:18px;font-weight:750;cursor:pointer;padding:14px 0}
.cleanup-list{display:grid;gap:10px}.cleanup-card{display:flex;justify-content:space-between;gap:15px;align-items:center;
  padding:15px;border:1px solid var(--panel-border);border-radius:12px;background:var(--panel)}
.cleanup-actions{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.cleanup-actions form{margin:0}
.cleanup-title{font-weight:700}.cleanup-proposal{font-size:12px;margin-top:5px}.cleanup-issues{display:flex;gap:5px;flex-wrap:wrap;margin-top:8px}
.cleanup-issues span{font-size:10px;padding:3px 7px;border-radius:999px;background:var(--warning-bg);color:var(--warning)}
.cleanup-issues span.ok{background:var(--success-bg);color:var(--success)}
@media(max-width:680px){.cleanup-card{align-items:stretch;flex-direction:column}.cleanup-card .btn{width:100%;text-align:center}}
</style>"""
    )
    return HTMLResponse(page("EzBay — eBay listing cleanup", body))


def _cleanup_review_fingerprint(
    remote: EbayListing, *, listing_id: str | None, proposed_title: str | None,
    has_actual_photo: bool,
) -> str:
    """Version a review decision so a materially changed item reappears."""
    snapshot = remote.raw_snapshot or {}
    payload = {
        "item_id": remote.ebay_listing_id,
        "title": remote.title,
        "price": remote.current_price_minor,
        "quantity": remote.quantity,
        "description": snapshot.get("description") or "",
        "images": snapshot.get("image_urls") or [],
        "listing_id": listing_id,
        "proposed_title": proposed_title,
        "has_actual_photo": has_actual_photo,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@router.post("/ebay-listing-cleanup/{ebay_listing_id}/dismiss")
def dismiss_ebay_listing_cleanup(
    ebay_listing_id: str,
    fingerprint: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    remote = session.scalar(
        select(EbayListing).where(
            EbayListing.ebay_listing_id == ebay_listing_id,
            EbayListing.listing_status == "Active",
        )
    )
    if remote is None:
        raise HTTPException(status_code=404, detail="active eBay listing not found")
    if len(fingerprint) != 64:
        raise HTTPException(status_code=400, detail="invalid review fingerprint")
    remote.raw_snapshot = {
        **(remote.raw_snapshot or {}),
        "cleanup_review": {
            "status": "dismissed",
            "fingerprint": fingerprint,
            "dismissed_by": user,
            "dismissed_at": datetime.now(UTC).isoformat(),
        },
    }
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="ebay.listing_review.dismiss",
        resource_type="ebay_listing", resource_id=ebay_listing_id, outcome="success",
        correlation_id=str(uuid.uuid4()), details={},
    )
    return RedirectResponse("/ebay-listing-cleanup", status_code=303)


def _confirmed_export_target(session: Session, listing_id: str) -> EbayExportItem | None:
    """The active legacy/File Exchange single behind a canonical listing."""
    item = session.scalar(
        select(EbayExportItem)
        .where(
            EbayExportItem.sku == listing_id,
            EbayExportItem.kind == "single",
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            EbayExportItem.ebay_listing_id.is_not(None),
        )
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
        .limit(1)
    )
    if item is None or not item.ebay_listing_id:
        return None
    remote = session.scalar(
        select(EbayListing).where(EbayListing.ebay_listing_id == item.ebay_listing_id)
    )
    return item if remote and remote.listing_status == "Active" else None


@router.get("/shop-listings/{listing_id}/ebay-preview", response_class=HTMLResponse)
def ebay_publish_preview(
    listing_id: str,
    notice: str = Query(""),
    error: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    settings = get_settings()
    if settings.ebay_bulk_upload_only:
        return RedirectResponse("/ebay-export?notice=Direct+eBay+publishing+is+disabled.+Use+Bulk+Upload.", status_code=303)
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    ebay_link = session.scalar(
        select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id)
    )
    legacy_target = _confirmed_export_target(session, listing.id)
    linked_live = bool(
        ebay_link
        and ebay_link.status == EbayListingLinkStatus.ACTIVE
        and ebay_link.ebay_listing_id
    )
    direct_revision = bool(linked_live and ebay_link and ebay_link.ebay_offer_id)
    is_live_revision = linked_live or legacy_target is not None
    live_ebay_id = (
        ebay_link.ebay_listing_id if linked_live and ebay_link else
        legacy_target.ebay_listing_id if legacy_target else None
    )
    check = publication_preflight(
        session, listing, settings, allow_active_revision=is_live_revision,
    )
    pricing_safety = assess_listing_price(session, listing)
    override_check = publication_preflight(
        session, listing, settings, allow_active_revision=is_live_revision,
        allow_pricing_safety_override=True,
    )
    can_override_pricing = pricing_safety.blocked and override_check.ready and not check.ready
    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    card_set = session.get(CardSet, tcg_card.set_id) if tcg_card and tcg_card.set_id else None
    inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    can_generate = bool(catalog_item and tcg_card)
    recommendation = (
        generated_ebay_title(listing, catalog_item, tcg_card, card_set, inventory_item)
        if can_generate else listing.title
    )
    outgoing_title = (
        outgoing_ebay_title(listing, catalog_item, tcg_card, card_set, inventory_item)
        if can_generate else (listing.ebay_title or listing.title)
    )
    has_own_photo = any(photo.is_own_photo for photo in check.photos)
    suggested_description = (
        generated_ebay_description(
            listing, catalog_item, tcg_card, card_set, inventory_item,
            has_own_photo=has_own_photo,
        ) if can_generate else (listing.description or listing.title)
    )
    outgoing_description = (
        outgoing_ebay_description(
            listing, catalog_item, tcg_card, card_set, inventory_item,
            has_own_photo=has_own_photo,
        ) if can_generate else (listing.ebay_description or listing.description or listing.title)
    )
    status = pill(
        "READY" if check.ready else ("OVERRIDE AVAILABLE" if can_override_pricing else "BLOCKED"),
        "ok" if check.ready else ("warn" if can_override_pricing else "bad"),
    )
    environment = settings.ebay_env.upper()
    gallery = ""
    explicit_photos = listing.ebay_photo_paths is not None
    for index, photo in enumerate(check.photos):
        source = _studio_photo_url(listing.id, index, photo)
        controls = ""
        if explicit_photos:
            position = "Main photo" if index == 0 else f"Photo {index + 1}"
            controls = (
                f"<strong class='photo-position'>{position}</strong>"
                "<div class='photo-controls'>"
                f"<form method='post' action='/shop-listings/{listing.id}/ebay-photos/{index}/move'>"
                + (
                    "<button name='direction' value='first' class='btn small ghost'>Make main</button>"
                    "<button name='direction' value='left' class='btn small ghost'>Move earlier</button>"
                    if index > 0 else ""
                )
                + (
                    "<button name='direction' value='right' class='btn small ghost'>Move later</button>"
                    if index < len(check.photos) - 1 else ""
                )
                + "</form>"
                f"<form method='post' action='/shop-listings/{listing.id}/ebay-photos/{index}/delete' "
                "onsubmit=\"return confirm('Remove this photo from the listing? The preserved original will remain on disk.');\">"
                "<button class='btn small ghost'>Remove</button></form></div>"
            )
        badge_class = "own" if photo.is_own_photo else "ref"
        gallery += (
            "<div class='studio-photo'>"
            f"<img src='{escape(source, quote=True)}' alt='Listing photo {index + 1}'>"
            f"<span class='photo-badge {badge_class}'>{escape(photo.label)}</span>{controls}</div>"
        )
    if not gallery:
        gallery = "<div class='studio-photo empty'>No photograph available</div>"

    quality_html = ""
    if check.quality:
        quality_rows = "".join(
            "<li class='quality-{}'><span>{}</span><small>{}</small></li>".format(
                "pass" if item.passed else ("block" if item.blocking else "warn"),
                ("✓ " if item.passed else "! ") + escape(item.label),
                escape(item.detail),
            ) for item in check.quality.checks
        )
        quality_html = (
            f"<div class='quality-score'><strong>{check.quality.score}/100</strong><span>Listing quality</span></div>"
            f"<ul class='quality-list'>{quality_rows}</ul>"
        )
    visible_errors = tuple(
        message for message in check.errors
        if not (can_override_pricing and message.startswith("Pricing safety hold:"))
    )
    errors = "".join(f"<li>{escape(message)}</li>" for message in visible_errors)
    warnings = "".join(f"<li>{escape(message)}</li>" for message in check.warnings)
    checks_html = ""
    if errors:
        checks_html += f"<div class='callout bad'><strong>Must fix first</strong><ul>{errors}</ul></div>"
    if warnings:
        checks_html += f"<div class='callout warn'><strong>Please note</strong><ul>{warnings}</ul></div>"
    if can_override_pricing:
        evidence_rows = "".join(
            "<li><strong>{}</strong>: AU ${:.2f} <small>observed {} UTC</small></li>".format(
                escape(evidence.label), evidence.value_aud,
                evidence.observed_at.astimezone(UTC).strftime("%-d %b %Y, %H:%M"),
            )
            for evidence in pricing_safety.evidence
        )
        checks_html += (
            "<div class='callout warn'><strong>Pricing hold — manual override available</strong>"
            f"<p>{escape(pricing_safety.message())}</p>"
            "<div class='pricing-proof'><strong>Proof used for this hold</strong>"
            f"<ul>{evidence_rows}</ul>"
            "<p>The review floor is 75% of the strongest recent credible reference. "
            "Market evidence is a comparison signal, not proof that every result is the same printing.</p>"
            f"<p><a class='btn small ghost' href='{escape(australian_active_listings_url(outgoing_title), quote=True)}' "
            "target='_blank' rel='noopener noreferrer'>View active eBay evidence</a> "
            f"<a class='btn small ghost' href='{escape(australian_sold_listings_url(outgoing_title), quote=True)}' "
            "target='_blank' rel='noopener noreferrer'>View sold eBay evidence</a></p></div>"
            "<p>Only continue after checking the exact card, printing and your intended price.</p></div>"
        )
    flash = ""
    if notice:
        flash += f"<div class='callout ok'>{escape(notice)}</div>"
    if error:
        flash += f"<div class='callout bad'>{escape(error)}</div>"
    action = ""
    if check.ready or can_override_pricing:
        override_confirmation = (
            f"<label class='pricing-override-confirm'><input type='checkbox' name='pricing_override' "
            f"value='{escape(listing.id)}' required> I verified this exact card and choose to override "
            f"the AU ${pricing_safety.review_floor:.2f} pricing safety floor.</label>"
            if can_override_pricing and pricing_safety.review_floor is not None else ""
        )
        if is_live_revision:
            action = (
                f"<form method='post' action='/shop-listings/{escape(listing.id)}/revise-on-ebay' class='publish-confirm'>"
                + override_confirmation
                + f"<label><input type='checkbox' name='confirmation' value='revise' required> "
                f"I reviewed this proposal and understand it will revise listing {escape(live_ebay_id or '')} "
                f"on eBay {escape(environment)}.</label>"
                "<button class='btn'>Update title, description, price &amp; photos on eBay</button></form>"
            )
        else:
            action = (
                f"<form method='post' action='/shop-listings/{escape(listing.id)}/publish-to-ebay' class='publish-confirm'>"
                + override_confirmation
                + f"<label><input type='checkbox' name='confirmation' value='publish' required> "
                f"I understand this will create or resume a live listing on eBay {escape(environment)}.</label>"
                "<button class='btn'>Publish live to eBay</button></form>"
            )
    workflow_label = "Existing eBay listing revision" if is_live_revision else "Direct Inventory API"
    body = (
        brand_header("eBay Listing Studio")
        + f"<p class='subtitle'>{status} Quality-controlled draft · {workflow_label} · {escape(environment)}. "
        + "Saving here never changes a live eBay listing.</p>"
        + f"<p><a class='btn ghost' href='{escape(australian_active_listings_url(outgoing_title), quote=True)}' target='_blank' rel='noopener noreferrer'>Active eBay evidence</a> "
        + f"<a class='btn ghost' href='{escape(australian_sold_listings_url(outgoing_title), quote=True)}' target='_blank' rel='noopener noreferrer'>Sold listings · Australia</a> "
        + "<a class='btn ghost' href='/shop-listings'>Back to Listing Studio</a></p>"
        + flash
        + checks_html
        + (
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/ebay-ai-suggest' class='panel ai-suggest'>"
            "<div><h2>AI listing suggestion</h2><p class='subtitle'>Uses your card photos plus the verified CRM identity. It saves a draft for review and never updates eBay by itself.</p></div>"
            "<button class='btn' type='submit'>Suggest title &amp; description</button></form>"
            if settings.anthropic_api_key else
            "<div class='callout warn'>AI suggestions are unavailable until the Anthropic photo-identification key is configured.</div>"
        )
        + "<div class='studio-grid'><section class='panel studio-editor'><h2>Listing content</h2>"
        + f"<form method='post' action='/shop-listings/{escape(listing.id)}/ebay-studio'>"
        + "<label class='studio-field'>eBay title <span id='title-count'></span>"
        + f"<input id='ebay-title' name='ebay_title' value='{escape(outgoing_title, quote=True)}' maxlength='80' required></label>"
        + f"<p class='studio-suggestion'>Recommended: {escape(recommendation)}</p>"
        + "<label class='studio-field'>eBay description"
        + f"<textarea name='ebay_description' rows='12' maxlength='5000' required>{escape(outgoing_description)}</textarea></label>"
        + f"<details><summary>View generated description</summary><pre>{escape(suggested_description)}</pre></details>"
        + "<div class='studio-short-fields'>"
        + f"<label class='studio-field'>Price (AUD)<input name='price' type='number' min='1' step='0.01' value='{listing.price}' required></label>"
        + f"<label class='studio-field'>Quantity<input name='quantity' type='number' min='1' step='1' value='{listing.quantity}' required></label>"
        + f"<label class='studio-field wide'>Condition<input name='condition' value='{escape(listing.condition or '', quote=True)}' maxlength='120' required></label>"
        + finish_field(listing)
        + "</div><div class='studio-actions'><button class='btn'>Save listing draft</button>"
        + "<button class='btn ghost' name='action' value='reset'>Use generated copy</button></div></form></section>"
        + f"<aside class='panel studio-quality'><h2>Quality checks</h2>{quality_html}</aside></div>"
        + "<section class='panel'><div class='photo-heading'><div><h2>Actual-card photos</h2>"
        + f"<p class='subtitle'>{len(check.photos)}/{MAX_EBAY_PHOTOS} images · first photo is the search image</p></div>"
        + "<div class='photo-upload-actions'>"
        + f"<form method='post' action='/shop-listings/{escape(listing.id)}/ebay-photos' enctype='multipart/form-data' class='photo-upload'>"
        + "<label class='btn small camera-choice'>Take new photo<input type='file' name='photos' accept='image/*' capture='environment' required onchange='this.form.requestSubmit()'></label></form>"
        + f"<form method='post' action='/shop-listings/{escape(listing.id)}/ebay-photos' enctype='multipart/form-data' class='photo-upload'>"
        + "<label class='btn small ghost camera-choice'>Choose photos<input type='file' name='photos' accept='image/jpeg,image/png,image/webp' multiple required onchange='this.form.requestSubmit()'></label></form></div></div>"
        + f"<div class='studio-gallery'>{gallery}</div></section>"
        + "<section class='panel buyer-preview'><div class='buyer-photo'>"
        + (f"<img src='{escape(_studio_photo_url(listing.id, 0, check.photos[0]), quote=True)}' alt='Main listing photo'>" if check.photos else "<div>No photo</div>")
        + "</div><div class='buyer-copy'><span class='preview-label'>Buyer-facing preview</span>"
        + f"<h1>{escape(outgoing_title)}</h1><div class='buyer-price'>AU ${listing.price}</div>"
        + f"<p>{listing.quantity} available · category {escape(check.category_id or 'unresolved')}</p>"
        + f"<div class='buyer-description'>{escape(outgoing_description).replace(chr(10), '<br>')}</div></div></section>"
        + action
        + "<p><a class='btn small ghost' href='/ebay-export'>Use CSV fallback</a> "
        + f"<a class='btn small ghost' href='/shop-listings?status={escape(listing.status.value)}'>Back to listings</a></p>"
        + _LISTING_STUDIO_STYLE + _LISTING_STUDIO_SCRIPT
    )
    return HTMLResponse(page("EzBay — eBay Listing Studio", body))


@router.post("/shop-listings/{listing_id}/ebay-studio")
def save_listing_studio(
    listing_id: str,
    ebay_title: str = Form(""),
    ebay_description: str = Form(""),
    price: str = Form(""),
    quantity: str = Form(""),
    condition: str = Form(""),
    finish: str | None = Form(None),
    action: str = Form("save"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if action == "reset":
        before = {"ebay_title": listing.ebay_title, "ebay_description": listing.ebay_description}
        listing.ebay_title = None
        listing.ebay_description = None
        session.commit()
        record_event(
            session, actor_type="user", actor_id=user, action="listing_studio.reset_copy",
            resource_type="shop_listing", resource_id=listing_id, outcome="success",
            correlation_id=str(uuid.uuid4()), details={"before": before},
        )
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?notice={quote('Generated title and description restored.')}",
            status_code=303,
        )

    clean_title = sanitize_title(ebay_title).strip()
    clean_description = ebay_description.strip()
    error = "Choose a valid card finish." if finish and finish not in FINISHES else None
    if not clean_title:
        error = "eBay title cannot be empty."
    elif len(clean_title) > 80:
        error = "eBay titles cannot exceed 80 characters."
    elif not clean_description:
        error = "eBay description cannot be empty."
    try:
        price_value = Decimal(price)
    except Exception:
        price_value = Decimal("0")
        error = error or "Price must be a valid number."
    try:
        quantity_value = int(quantity)
    except (TypeError, ValueError):
        quantity_value = 0
        error = error or "Quantity must be a whole number."
    if price_value < EBAY_MIN_FIXED_PRICE:
        error = error or "eBay price must be at least AUD $1.00."
    if quantity_value < 1:
        error = error or "Quantity must be at least one."
    if not condition.strip():
        error = error or "Condition cannot be empty."
    if error:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote(error)}", status_code=303,
        )

    before = {
        "ebay_title": listing.ebay_title, "ebay_description": listing.ebay_description,
        "finish": listing.finish, "price": str(listing.price), "quantity": listing.quantity, "condition": listing.condition,
    }
    listing.ebay_title = clean_title
    listing.ebay_description = clean_description[:5000]
    listing.price = price_value
    listing.quantity = quantity_value
    listing.condition = condition.strip()
    save_finish(listing, finish)
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_studio.save",
        resource_type="shop_listing", resource_id=listing_id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"before": before, "after": {
            "ebay_title": listing.ebay_title, "ebay_description": listing.ebay_description,
            "finish": listing.finish, "price": str(listing.price), "quantity": listing.quantity, "condition": listing.condition,
        }},
    )
    return RedirectResponse(
        f"/shop-listings/{listing_id}/ebay-preview?notice={quote('Listing draft saved. Nothing was sent to eBay.')}",
        status_code=303,
    )


@router.post("/shop-listings/{listing_id}/ebay-ai-suggest")
def suggest_listing_studio_copy(
    listing_id: str,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Create and save a reviewable AI draft; never write directly to eBay."""
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    settings = get_settings()
    if not settings.anthropic_api_key:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote('AI suggestions are not configured.')}",
            status_code=303,
        )
    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    card = session.get(TcgCard, listing.catalog_item_id)
    card_set = session.get(CardSet, card.set_id) if card and card.set_id else None
    inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if catalog_item is None or card is None:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote('Verified card identity is required before asking AI for listing copy.')}",
            status_code=303,
        )
    local_paths = [
        Path(photo.value)
        for photo in listing_photo_sources(session, listing, catalog_item)
        if photo.is_own_photo and not photo.is_remote and Path(photo.value).is_file()
    ]
    if not local_paths:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote('Take or upload an actual-card photo before asking AI for a suggestion.')}",
            status_code=303,
        )
    variant = ""
    if inventory and inventory.variant:
        variant = inventory.variant.value.replace("_", " ").title()
    elif card.variant:
        variant = card.variant.replace("_", " ").title()
    known_facts = {
        "card name": catalog_item.name,
        "game": card.game,
        "set": card_set.name if card_set else "",
        "card number": card.number or "",
        "rarity": card.rarity or "",
        "parallel": card.parallel or "",
        "finish": variant,
        "language": card.language or "English",
        "condition": (
            inventory.condition
            if inventory and inventory.condition
            else (listing.condition or "Ungraded")
        ),
        "current eBay title": listing.ebay_title or listing.title,
    }
    try:
        suggestion = suggest_ebay_listing_copy(
            local_paths, api_key=settings.anthropic_api_key, known_facts=known_facts,
        )
    except RecognitionError as exc:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote(str(exc))}", status_code=303,
        )
    clean_title = sanitize_title(suggestion.title).strip()
    clean_description = suggestion.description.strip()
    if not clean_title or len(clean_title) > 80 or not clean_description:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote('AI returned invalid listing copy; no draft was changed.')}",
            status_code=303,
        )
    before = {"ebay_title": listing.ebay_title, "ebay_description": listing.ebay_description}
    listing.ebay_title = clean_title
    listing.ebay_description = clean_description[:5000]
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_studio.ai_suggest",
        resource_type="shop_listing", resource_id=listing_id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={
            "photo_count": min(len(local_paths), 4), "before": before,
            "after": {"ebay_title": listing.ebay_title, "ebay_description": listing.ebay_description},
        },
    )
    return RedirectResponse(
        f"/shop-listings/{listing_id}/ebay-preview?notice={quote('AI suggestion saved as a draft. Review it, then use Update this listing on eBay.')}",
        status_code=303,
    )


MAX_PHOTO_UPLOAD_BYTES = 7 * 1024 * 1024


def _store_listing_photo(listing_id: str, data: bytes, settings) -> str:
    if not data or len(data) > MAX_PHOTO_UPLOAD_BYTES:
        raise ValueError("Each photo must be between 1 byte and 7 MB.")
    try:
        with Image.open(BytesIO(data)) as opened:
            original_suffix = {
                "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
            }.get((opened.format or "").upper(), ".bin")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file is not a readable JPEG, PNG or WebP image.") from exc
    if max(image.size) < 500:
        raise ValueError("Each eBay photo must be at least 500 pixels on its longest side.")
    if max(image.size) > 2400:
        image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    directory = Path(settings.scan_media_dir) / "listing_photos" / listing_id
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    original = directory / f"{token}-original{original_suffix}"
    original.write_bytes(data)
    processed = directory / f"{token}.jpg"
    image.save(processed, "JPEG", quality=92, optimize=True)
    return str(processed)


@router.post("/shop-listings/{listing_id}/ebay-photos")
async def add_listing_photos(
    listing_id: str,
    photos: list[UploadFile] = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    current = list(listing.ebay_photo_paths or [])
    if listing.ebay_photo_paths is None:
        current = [
            photo.value for photo in listing_photo_sources(session, listing, session.get(CatalogItem, listing.catalog_item_id))
            if photo.is_own_photo
        ]
    if len(current) + len(photos) > MAX_EBAY_PHOTOS:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote(f'eBay allows up to {MAX_EBAY_PHOTOS} photos.')}",
            status_code=303,
        )
    added: list[str] = []
    try:
        for photo in photos:
            added.append(_store_listing_photo(listing_id, await photo.read(), get_settings()))
    except ValueError as exc:
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?error={quote(str(exc))}", status_code=303,
        )
    listing.ebay_photo_paths = current + added
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_studio.add_photos",
        resource_type="shop_listing", resource_id=listing_id, outcome="success",
        correlation_id=str(uuid.uuid4()), details={"added": len(added), "total": len(listing.ebay_photo_paths)},
    )
    return RedirectResponse(
        f"/shop-listings/{listing_id}/ebay-preview?notice={quote(f'Added {len(added)} photo(s).')}", status_code=303,
    )


@router.get("/shop-listings/{listing_id}/ebay-photo/{photo_index}")
def listing_studio_photo(
    listing_id: str, photo_index: int,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    sources = listing_photo_sources(session, listing, session.get(CatalogItem, listing.catalog_item_id))
    if photo_index < 0 or photo_index >= len(sources):
        raise HTTPException(status_code=404, detail="photo not found")
    source = sources[photo_index]
    if source.is_remote:
        return RedirectResponse(source.value)
    # The route is indexed by editable position, rather than immutable file
    # identity. The versioned HTML URL normally prevents stale displays, and
    # max_age=0 is a second guard for older already-open phone pages.
    return serve_image(source.value, missing="listing photo file missing", max_age=0)


@router.post("/shop-listings/{listing_id}/ebay-photos/{photo_index}/delete")
def delete_listing_photo(
    listing_id: str, photo_index: int,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    values = list(listing.ebay_photo_paths or [])
    if photo_index < 0 or photo_index >= len(values):
        raise HTTPException(status_code=404, detail="editable listing photo not found")
    removed = values.pop(photo_index)
    listing.ebay_photo_paths = values
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="listing_studio.remove_photo",
        resource_type="shop_listing", resource_id=listing_id, outcome="success",
        correlation_id=str(uuid.uuid4()), details={"removed": removed, "remaining": len(values)},
    )
    return RedirectResponse(
        f"/shop-listings/{listing_id}/ebay-preview?notice={quote('Photo removed from the draft; the original remains recoverable on disk.')}",
        status_code=303,
    )


@router.post("/shop-listings/{listing_id}/ebay-photos/{photo_index}/move")
def move_listing_photo(
    listing_id: str, photo_index: int, direction: str = Form("left"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    values = list(listing.ebay_photo_paths or [])
    if direction == "first":
        destination = 0
    elif direction == "left":
        destination = photo_index - 1
    else:
        destination = photo_index + 1
    if 0 <= photo_index < len(values) and 0 <= destination < len(values) and photo_index != destination:
        value = values.pop(photo_index)
        values.insert(destination, value)
        listing.ebay_photo_paths = values
        session.commit()
        record_event(
            session, actor_type="user", actor_id=user, action="listing_studio.reorder_photos",
            resource_type="shop_listing", resource_id=listing_id, outcome="success",
            correlation_id=str(uuid.uuid4()), details={"from": photo_index, "to": destination},
        )
    return RedirectResponse(f"/shop-listings/{listing_id}/ebay-preview", status_code=303)


@router.post("/shop-listings/{listing_id}/publish-to-ebay")
async def publish_to_ebay(
    listing_id: str,
    confirmation: str = Form(""),
    pricing_override: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    if get_settings().ebay_bulk_upload_only:
        return RedirectResponse("/ebay-export?notice=Direct+eBay+publishing+is+disabled.+Use+Bulk+Upload.", status_code=303)
    return await publish_listing_to_ebay(
        listing_id, confirmation=confirmation, user=user, session=session,
        allow_pricing_safety_override=pricing_override == listing_id,
    )


@router.post("/shop-listings/{listing_id}/revise-on-ebay")
async def revise_listing_on_ebay(
    listing_id: str,
    confirmation: str = Form(""),
    pricing_override: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Apply one explicitly reviewed proposal to its exact live eBay item.

    Inventory-API listings stay on the Inventory API. Confirmed legacy/CSV
    singles use Trading API ReviseFixedPriceItem; variation parents never enter
    Listing Studio, so this cannot rewrite a Complete-Your-Set group by mistake.
    """
    if confirmation != "revise":
        raise HTTPException(status_code=400, detail="review and confirm the live eBay revision first")
    settings = get_settings()
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing_id))
    linked_live = bool(
        link
        and link.status == EbayListingLinkStatus.ACTIVE
        and link.ebay_listing_id
    )
    direct_revision = bool(linked_live and link and link.ebay_offer_id)
    legacy_target = None if linked_live else _confirmed_export_target(session, listing_id)
    ebay_listing_id = link.ebay_listing_id if linked_live and link else (
        legacy_target.ebay_listing_id if legacy_target else None
    )
    if not ebay_listing_id:
        raise HTTPException(status_code=400, detail="no active eBay single is safely linked to this CRM listing")
    pricing_safety_overridden = pricing_override == listing_id
    check = publication_preflight(
        session, listing, settings, allow_active_revision=True,
        allow_pricing_safety_override=pricing_safety_overridden,
    )
    if not check.ready:
        raise HTTPException(status_code=400, detail="; ".join(check.errors))

    lock = _PUBLISH_LOCKS.setdefault(listing_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="this listing is already being published or revised")
    async with lock:
        catalog_item = session.get(CatalogItem, listing.catalog_item_id)
        tcg_card = session.get(TcgCard, listing.catalog_item_id)
        card_set = session.get(CardSet, tcg_card.set_id)
        inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
        image_urls = _prepare_images(session, listing, catalog_item, settings)
        title = outgoing_ebay_title(listing, catalog_item, tcg_card, card_set, inventory_item)
        description = outgoing_ebay_description(
            listing, catalog_item, tcg_card, card_set, inventory_item, has_own_photo=True,
        )
        inventory_payload = offer_payload = None
        if direct_revision and link:
            inventory_payload = _build_inventory_item_payload(
                listing, catalog_item, tcg_card, card_set, inventory_item,
                image_urls, title, description,
            )
            offer_payload = _build_offer_payload(
                listing, link.sku, check.category_id, settings, description=description,
            )

        approval = Approval(
            requested_at=datetime.now(UTC), requested_by=user, action="ebay.revise_listing",
            arguments={
                "shop_listing_id": listing_id, "ebay_listing_id": ebay_listing_id,
                "environment": settings.ebay_env, "title": title,
                "price": str(listing.price), "quantity": listing.quantity,
                "photo_count": len(image_urls),
                "revision_api": "inventory" if direct_revision else "trading",
                "pricing_safety_override": pricing_safety_overridden,
            },
        )
        session.add(approval)
        session.commit()
        decide_approval(session, approval, approved=True, actor=user)

        adapter = trading_adapter = None
        previous: dict = {}
        try:
            cipher, oauth = ebay_services(settings)
            token = await valid_user_access_token(session, cipher, oauth)
            if direct_revision and link:
                adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
                previous = await adapter.get_inventory_item(link.sku)
                await adapter.upsert_inventory_item(link.sku, inventory_payload)
                try:
                    await adapter.update_offer(link.ebay_offer_id, offer_payload)
                except EbayApiError as exc:
                    # eBay can accept createOrReplaceInventoryItem and then,
                    # for a short propagation window, make updateOffer report
                    # error 25604 "Availability not found" for that same SKU.
                    # Confirm the inventory row now contains availability and
                    # retry the idempotent offer update once.
                    availability_missing = (
                        "25604" in str(exc) or "availability not found" in str(exc).casefold()
                    )
                    if not availability_missing:
                        raise
                    refreshed = await adapter.get_inventory_item(link.sku)
                    available = (
                        (refreshed.get("availability") or {})
                        .get("shipToLocationAvailability", {})
                        .get("quantity")
                    )
                    if available is None:
                        raise
                    await adapter.update_offer(link.ebay_offer_id, offer_payload)
            else:
                remote = session.scalar(
                    select(EbayListing).where(EbayListing.ebay_listing_id == ebay_listing_id)
                )
                if remote:
                    previous = {
                        "product": {
                            "title": remote.title,
                            "imageUrls": (remote.raw_snapshot or {}).get("image_urls") or [],
                        },
                    }
                trading_adapter = EbayTradingListingAdapter(
                    token, settings.ebay_client_id, environment=settings.ebay_env,
                    marketplace_id=settings.ebay_marketplace_id,
                )
                await trading_adapter.revise_copy_and_photos(
                    ebay_listing_id, title=title, description=description,
                    picture_urls=image_urls, price=listing.price,
                    condition_descriptor=_ungraded_condition_descriptor(
                        inventory_item.condition if inventory_item else listing.condition
                    ),
                )
        except (EbayApiError, EbayNotConfigured, OAuthError, ValueError) as exc:
            if link:
                link.last_error = str(exc)
                link.last_synced_at = datetime.now(UTC)
            session.commit()
            record_event(
                session, actor_type="user", actor_id=user, action="ebay.revise_listing",
                resource_type="shop_listing", resource_id=listing_id, outcome="failed",
                correlation_id=str(uuid.uuid4()), details={"error": str(exc)},
            )
            message = f"eBay did not complete the update: {exc}"
            return RedirectResponse(
                f"/shop-listings/{listing_id}/ebay-preview?error={quote(message[:700])}",
                status_code=303,
            )
        finally:
            if adapter is not None:
                await adapter.aclose()
            if trading_adapter is not None:
                await trading_adapter.client.aclose()

        old_product = previous.get("product") or {}
        if link:
            link.last_error = None
            link.last_synced_at = datetime.now(UTC)
            link.last_pushed_quantity = listing.quantity
            link.approval_id = approval.id
        remote = session.scalar(
            select(EbayListing).where(EbayListing.ebay_listing_id == ebay_listing_id)
        )
        if remote:
            remote.title = title
            remote.current_price_minor = int((listing.price * 100).quantize(Decimal("1")))
            remote.raw_snapshot = {
                **(remote.raw_snapshot or {}),
                "description": description,
                "image_urls": image_urls,
                "details_source": "listing_studio_revision",
                "details_imported_at": datetime.now(UTC).isoformat(),
            }
            remote.observed_at = datetime.now(UTC)
        session.commit()
        record_event(
            session, actor_type="user", actor_id=user, action="ebay.revise_listing",
            resource_type="shop_listing", resource_id=listing_id, outcome="success",
            correlation_id=str(uuid.uuid4()), details={
                "ebay_listing_id": ebay_listing_id,
                "old_title": old_product.get("title"), "new_title": title,
                "old_photo_count": len(old_product.get("imageUrls") or []),
                "new_photo_count": len(image_urls),
                "revision_api": "inventory" if direct_revision else "trading",
                "pricing_safety_override": pricing_safety_overridden,
            },
        )
    return RedirectResponse(
        f"/shop-listings/{listing_id}/ebay-preview?notice={quote('Approved revision sent to eBay successfully.')}",
        status_code=303,
    )


async def publish_listing_to_ebay(
    listing_id: str,
    *,
    confirmation: str,
    user: str,
    session: Session,
    price_override: Decimal | None = None,
    allow_pricing_safety_override: bool = False,
):
    """Shared direct-publication service.

    The web route deliberately exposes no price override; its reviewed
    ShopListing price is authoritative. Controlled operator workflows can
    supply an eBay-only price (for example a fee markup) without changing
    the NL Vault/base price stored on the listing.
    """
    # One uvicorn worker serves this app. A per-SKU lock closes the remaining
    # double-click race where two requests could both see no remote offer
    # before either createOffer response arrives. Crash recovery is handled
    # separately by the persisted PUBLISHING link and getOffers-by-SKU path.
    lock = _PUBLISH_LOCKS.setdefault(listing_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="this listing is already being published")
    async with lock:
        return await _publish_to_ebay_locked(
            listing_id, confirmation, user, session, price_override=price_override,
            allow_pricing_safety_override=allow_pricing_safety_override,
        )


async def _publish_to_ebay_locked(
    listing_id: str, confirmation: str, user: str, session: Session,
    *, price_override: Decimal | None = None, allow_pricing_safety_override: bool = False,
):
    settings = get_settings()
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")

    check = publication_preflight(
        session, listing, settings, price_override=price_override,
        allow_pricing_safety_override=allow_pricing_safety_override,
    )
    if not check.ready:
        raise HTTPException(status_code=400, detail="; ".join(check.errors))
    if confirmation != "publish":
        raise HTTPException(status_code=400, detail="review and confirm the live eBay publication first")

    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    card_set = session.get(CardSet, tcg_card.set_id)
    inventory_item = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    image_urls = _prepare_images(session, listing, catalog_item, settings)
    outgoing_title = outgoing_ebay_title(listing, catalog_item, tcg_card, card_set, inventory_item)
    outgoing_description = outgoing_ebay_description(
        listing, catalog_item, tcg_card, card_set, inventory_item, has_own_photo=True,
    )

    approval = Approval(
        requested_at=datetime.now(UTC),
        requested_by=user,
        action="ebay.publish_listing",
        arguments={
            "shop_listing_id": listing_id,
            "environment": settings.ebay_env,
            "ebay_price": str(price_override if price_override is not None else listing.price),
            "title": outgoing_title,
            "photo_count": len(image_urls),
            "pricing_safety_override": allow_pricing_safety_override,
        },
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
        listing, catalog_item, tcg_card, card_set, inventory_item,
        image_urls, outgoing_title, outgoing_description,
    )
    offer_payload = _build_offer_payload(
        listing, link.sku, check.category_id, settings,
        description=outgoing_description, price_override=price_override,
    )
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
        details={
            "ebay_listing_id": ebay_listing_id,
            "offer_id": link.ebay_offer_id,
            "ebay_price": str(price_override if price_override is not None else listing.price),
            "pricing_safety_override": allow_pricing_safety_override,
        },
    )
    return RedirectResponse("/shop-listings", status_code=303)
