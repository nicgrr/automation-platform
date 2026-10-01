"""Safe handoff from an approved photo capture to Bulk Upload inventory."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .adapters.r2 import R2UploadError, is_configured as r2_is_configured, upload_card_image
from .classification import load_pipeline_config
from .models import (
    CapturePromotion, CapturePromotionStatus, CapturedCard, CardSet, CardVariant,
    CatalogCard, CatalogItem, EbayExportItem, EbayExportItemStatus, InventoryItem,
    InventoryStatus, ShopListing, ShopListingStatus, TcgCard,
)


@dataclass(frozen=True)
class PromotionResult:
    promoted: bool
    issue: str | None = None
    shop_listing_id: str | None = None


def _normalise(value: str | None) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())


def _number(value: str | None) -> str:
    return _normalise((value or "").split("/", 1)[0]).lstrip("0") or "0"


def _set_key(value: str | None) -> str:
    return _normalise(re.sub(r"\([^)]*\)", "", value or ""))


def resolve_catalogue_card(session: Session, card: CapturedCard) -> tuple[CatalogItem, TcgCard] | str:
    """Return one exact name+number match, or a human-readable issue."""
    if not card.character or not card.card_number:
        return "Card name and number are required before it can be listed."

    rows = session.execute(
        select(CatalogItem, TcgCard)
        .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
        .where(func.lower(CatalogItem.name) == card.character.strip().lower())
    ).all()
    candidates = [(item, tcg) for item, tcg in rows if _number(tcg.number) == _number(card.card_number)]
    if len(candidates) > 1 and card.set_name:
        wanted = _set_key(card.set_name)
        set_ids = {tcg.set_id for _item, tcg in candidates if tcg.set_id}
        set_names = dict(session.execute(select(CardSet.id, CardSet.name).where(CardSet.id.in_(set_ids))).all())
        narrowed = [
            pair for pair in candidates
            if wanted and _set_key(set_names.get(pair[1].set_id, pair[1].set_id)) == wanted
        ]
        if narrowed:
            candidates = narrowed
    if not candidates:
        return f"No catalogue match for {card.character} #{card.card_number}; correct the identity first."
    if len(candidates) != 1:
        return f"{len(candidates)} catalogue matches for {card.character} #{card.card_number}; choose the set first."
    return candidates[0]


def infer_unambiguous_variant(session: Session, catalog_item_id: str) -> CardVariant | None:
    """Use catalogue price variants only when exactly one printing exists."""
    reference = session.get(CatalogCard, catalog_item_id)
    prices = ((reference.raw_prices or {}).get("tcgplayer") or {}).get("prices") if reference else None
    if not isinstance(prices, dict):
        return None
    mapping = {
        "normal": CardVariant.NORMAL,
        "holofoil": CardVariant.HOLO,
        "reverseHolofoil": CardVariant.REVERSE_HOLO,
    }
    possibilities = {printing for key, printing in mapping.items() if prices.get(key)}
    return next(iter(possibilities)) if len(possibilities) == 1 else None


def _set_issue(session: Session, card: CapturedCard, issue: str,
               catalog_item_id: str | None = None) -> PromotionResult:
    row = session.scalar(select(CapturePromotion).where(CapturePromotion.captured_card_id == card.id))
    if row is None:
        row = CapturePromotion(captured_card_id=card.id, status=CapturePromotionStatus.NEEDS_REVIEW)
        session.add(row)
    row.status = CapturePromotionStatus.NEEDS_REVIEW
    row.catalog_item_id = catalog_item_id
    row.inventory_item_id = None
    row.shop_listing_id = None
    row.issue = issue
    return PromotionResult(False, issue=issue)


def _latest_export(session: Session, sku: str) -> EbayExportItem | None:
    return session.scalar(
        select(EbayExportItem).where(EbayExportItem.sku == sku)
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc()).limit(1)
    )


def promote_approved_card(session: Session, card: CapturedCard, settings) -> PromotionResult:
    """Promote one approved capture, without committing the transaction.

    Every possible issue is decided before inventory is touched. This is
    important: a human can correct and retry without adding the same copy a
    second time.
    """
    existing_promotion = session.scalar(
        select(CapturePromotion).where(CapturePromotion.captured_card_id == card.id)
    )
    if existing_promotion and existing_promotion.status == CapturePromotionStatus.PROMOTED:
        return PromotionResult(True, shop_listing_id=existing_promotion.shop_listing_id)

    resolved = resolve_catalogue_card(session, card)
    if isinstance(resolved, str):
        return _set_issue(session, card, resolved)
    catalog_item, tcg = resolved

    if card.variant is None:
        card.variant = infer_unambiguous_variant(session, catalog_item.id)
    if card.variant is None:
        return _set_issue(
            session, card,
            "Choose the physical printing (Normal, Holo, Reverse Holo, Poké Ball or Master Ball) first.",
            catalog_item.id,
        )

    shared = session.scalar(
        select(func.count()).select_from(CapturedCard).where(
            CapturedCard.front_image_path == card.front_image_path,
            CapturedCard.id != card.id,
        )
    ) or 0
    if shared and not re.match(r"card\d+_", Path(card.front_image_path).name):
        return _set_issue(
            session, card,
            "This photo is shared by multiple cards and needs a card-specific crop before listing.",
            catalog_item.id,
        )

    variant = card.variant
    active = session.scalar(
        select(ShopListing)
        .outerjoin(InventoryItem, InventoryItem.id == ShopListing.inventory_item_id)
        .where(
            ShopListing.catalog_item_id == catalog_item.id,
            ShopListing.status == ShopListingStatus.ACTIVE,
            (InventoryItem.variant == variant) | (ShopListing.inventory_item_id.is_(None)),
        ).order_by(ShopListing.created_at.desc()).limit(1)
    )
    if active:
        export = _latest_export(session, active.id)
        if export and export.status in (EbayExportItemStatus.PENDING, EbayExportItemStatus.CONFIRMED):
            return _set_issue(
                session, card,
                "Another copy is already exported to eBay; it needs a stock-quantity revision, not a duplicate listing.",
                catalog_item.id,
            )

    if not r2_is_configured(settings):
        return _set_issue(session, card, "R2 image hosting is not configured.", catalog_item.id)
    try:
        image_url = upload_card_image(
            card.front_image_path, f"captured-approved/{card.id}.jpg",
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket_name=settings.r2_bucket_name,
            public_base_url=load_pipeline_config().images.base_url,
        )
    except (R2UploadError, OSError) as exc:
        return _set_issue(session, card, f"Could not host the card photo: {exc}", catalog_item.id)

    condition = "Near Mint"
    inventory = session.scalar(select(InventoryItem).where(
        InventoryItem.catalog_item_id == catalog_item.id,
        InventoryItem.variant == variant,
        InventoryItem.condition == condition,
    ))
    if inventory is None:
        catalog_card = session.get(CatalogCard, catalog_item.id)
        inventory = InventoryItem(
            card_id=catalog_card.id if catalog_card else None,
            catalog_item_id=catalog_item.id, variant=variant, condition=condition,
            quantity=1, scan_image_path=card.front_image_path,
            status=InventoryStatus.AVAILABLE,
        )
        session.add(inventory)
        session.flush()
    else:
        inventory.quantity += 1
        inventory.status = InventoryStatus.AVAILABLE
        if not inventory.scan_image_path:
            inventory.scan_image_path = card.front_image_path

    price = Decimal(str(card.approved_price))
    title = catalog_item.name + (f" #{tcg.number}" if tcg.number else "")
    if active is None:
        printing_label = {
            CardVariant.NORMAL: "Normal",
            CardVariant.HOLO: "Holo",
            CardVariant.REVERSE_HOLO: "Reverse Holo",
            CardVariant.POKEBALL_PATTERN: "Poké Ball pattern",
            CardVariant.MASTERBALL_PATTERN: "Master Ball pattern",
        }[variant]
        active = ShopListing(
            catalog_item_id=catalog_item.id, inventory_item_id=inventory.id,
            title=title, condition=f"Near Mint · {printing_label}", price=price,
            currency="AUD", quantity=inventory.quantity,
            status=ShopListingStatus.ACTIVE, image_url=image_url,
        )
        session.add(active)
        session.flush()
    else:
        active.inventory_item_id = inventory.id
        active.quantity = inventory.quantity
        active.price = price
        active.image_url = image_url

    promotion = existing_promotion or CapturePromotion(captured_card_id=card.id)
    if existing_promotion is None:
        session.add(promotion)
    promotion.status = CapturePromotionStatus.PROMOTED
    promotion.catalog_item_id = catalog_item.id
    promotion.inventory_item_id = inventory.id
    promotion.shop_listing_id = active.id
    promotion.issue = None
    return PromotionResult(True, shop_listing_id=active.id)
