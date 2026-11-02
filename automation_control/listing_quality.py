"""Professional eBay copy, photo selection and pre-publish quality checks.

This module is deliberately deterministic.  It never invents a printing,
condition or seller promise, and it never downloads/rewrites a third-party
catalogue image.  Listing Studio can therefore show exactly where every word
and photo came from before a write reaches eBay.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image
from sqlalchemy.orm import Session

from .listing_finishes import finish_label, title_inventory
from .ebay_titles import build_single_card_ebay_title
from .models import CardSet, CatalogItem, CatalogItemType, InventoryItem, ShopListing, TcgCard
from scripts.convert_to_ebay_template import sanitize_title


MAX_EBAY_PHOTOS = 24
MIN_PHOTO_LONG_EDGE = 500
RECOMMENDED_TITLE_MIN = 65
MAX_TITLE_LENGTH = 80


@dataclass(frozen=True)
class ListingPhotoSource:
    value: str
    is_own_photo: bool
    label: str

    @property
    def is_remote(self) -> bool:
        return self.value.startswith(("http://", "https://"))


@dataclass(frozen=True)
class QualityCheck:
    label: str
    passed: bool
    blocking: bool
    detail: str


@dataclass(frozen=True)
class ListingQuality:
    score: int
    checks: tuple[QualityCheck, ...]

    @property
    def blockers(self) -> tuple[QualityCheck, ...]:
        return tuple(check for check in self.checks if check.blocking and not check.passed)


def generated_ebay_title(
    listing: ShopListing,
    catalog_item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None,
) -> str:
    if catalog_item.item_type == CatalogItemType.GRADED_CARD and inventory is not None:
        # Reserve the exact label wording first: Pristine 10 and Gem Mint 10
        # describe different grades even when eBay's numeric value is 10.
        grading = sanitize_title(" ".join(filter(None, (
            inventory.grading_company, inventory.grade,
        )))).strip()
        if grading:
            card_title = build_single_card_ebay_title(
                catalog_item, card, card_set, title_inventory(listing, inventory),
                max_length=max(1, MAX_TITLE_LENGTH - len(grading) - 1),
            )
            return f"{grading} {card_title}"
    return build_single_card_ebay_title(catalog_item, card, card_set, title_inventory(listing, inventory))


def outgoing_ebay_title(
    listing: ShopListing,
    catalog_item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None,
) -> str:
    override = (listing.ebay_title or "").strip()
    return override or generated_ebay_title(listing, catalog_item, card, card_set, inventory)


def generated_ebay_description(
    listing: ShopListing,
    catalog_item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None,
    *,
    has_own_photo: bool,
) -> str:
    """Plain, mobile-friendly copy made only from known catalogue/stock facts."""
    variant = ""
    if inventory is not None and inventory.variant:
        variant = inventory.variant.value.replace("_", " ").title()
    elif card.variant:
        variant = card.variant.replace("_", " ").title()
    variant = finish_label(listing) or variant
    condition = (
        inventory.condition
        if inventory is not None and inventory.condition
        else (listing.condition or "Ungraded")
    )
    lines = [
        catalog_item.name,
        "",
        *([f"Set: {card_set.name}"] if card_set else []),
        *([f"Card number: {card.number}"] if card.number else []),
        *([f"Rarity: {card.rarity}"] if card.rarity else []),
        *([f"Parallel: {card.parallel}"] if card.parallel else []),
        *([f"Finish: {variant}"] if variant and variant.casefold() not in {"normal", "standard"} else []),
        f"Language: {card.language or 'English'}",
        f"Condition: {condition}",
        "",
    ]
    if catalog_item.item_type == CatalogItemType.GRADED_CARD and inventory is not None:
        lines.extend([
            f"Grading company: {inventory.grading_company or 'Not recorded'}",
            f"Grade on label: {inventory.grade or 'Not recorded'}",
            f"Certification number: {inventory.certification_number or 'Not recorded'}",
            "",
        ])
    from .representative_backs import is_representative, NOTICE
    if has_own_photo and any(is_representative(p) for p in listing.ebay_photo_paths or []):
        lines.append(NOTICE)
    elif has_own_photo and catalog_item.item_type == CatalogItemType.GRADED_CARD:
        lines.append("You will receive the actual graded card and holder shown in the front and back photographs. Please review the slab, label and holder condition in all photos.")
    elif has_own_photo:
        lines.append("You will receive the actual card shown in the photographs. Please review all photos as they form part of the condition description.")
    else:
        lines.append("An actual-card photograph is still required before this listing is ready for eBay.")
    lines.extend([
        "",
        "The card will be packaged carefully for transit.",
    ])
    return "\n".join(lines)


def outgoing_ebay_description(
    listing: ShopListing,
    catalog_item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None,
    *,
    has_own_photo: bool,
) -> str:
    override = (listing.ebay_description or "").strip()
    from .representative_backs import with_notice
    description = override or generated_ebay_description(
        listing, catalog_item, card, card_set, inventory, has_own_photo=has_own_photo,
    )
    return with_notice(description, listing.ebay_photo_paths)


def listing_photo_sources(
    session: Session,
    listing: ShopListing,
    catalog_item: CatalogItem | None = None,
) -> list[ListingPhotoSource]:
    """Return publication order while explicitly identifying catalogue art."""
    # None means the seller has not started an editable eBay gallery yet, so
    # the owned-card scan may be offered as a sensible default. An empty list
    # means every photo was deliberately removed and must stay empty.
    if listing.ebay_photo_paths is not None:
        explicit = [
            str(value).strip()
            for value in listing.ebay_photo_paths
            if str(value).strip()
        ]
        return [ListingPhotoSource(value, True, f"Photo {index}") for index, value in enumerate(explicit, 1)]

    inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    if inventory and inventory.scan_image_path:
        return [ListingPhotoSource(inventory.scan_image_path, True, "Owned-card scan")]

    if listing.image_url:
        return [ListingPhotoSource(listing.image_url, False, "Reference art")]
    if catalog_item and catalog_item.image_url:
        return [ListingPhotoSource(catalog_item.image_url, False, "Catalogue reference")]
    return []


def _photo_dimensions(source: ListingPhotoSource) -> tuple[int, int] | None:
    if source.is_remote:
        return None
    try:
        with Image.open(Path(source.value)) as image:
            return image.size
    except (OSError, ValueError):
        return (0, 0)


def assess_listing_quality(
    title: str,
    description: str,
    photos: list[ListingPhotoSource],
    card: TcgCard | None,
    card_set: CardSet | None,
    condition: str | None,
) -> ListingQuality:
    checks: list[QualityCheck] = []

    title_len = len(title.strip())
    checks.append(QualityCheck(
        "Search title", 60 <= title_len <= MAX_TITLE_LENGTH, title_len == 0 or title_len > MAX_TITLE_LENGTH,
        f"{title_len}/80 characters" + ("; 65–80 is the recommended target" if title_len < RECOMMENDED_TITLE_MIN else ""),
    ))
    description_len = len(description.strip())
    checks.append(QualityCheck(
        "Detailed description", description_len >= 180, description_len == 0,
        f"{description_len} characters; include identity, condition and buyer-facing photo guidance",
    ))

    own_photos = [photo for photo in photos if photo.is_own_photo]
    checks.append(QualityCheck(
        "Actual-card photograph", bool(own_photos), True,
        f"{len(own_photos)} actual-card photo(s)" if own_photos else "Reference art cannot replace a photo of an ungraded/used card",
    ))
    if own_photos:
        undersized = []
        unreadable = []
        for photo in own_photos:
            size = _photo_dimensions(photo)
            if size == (0, 0):
                unreadable.append(photo.label)
            elif size is not None and max(size) < MIN_PHOTO_LONG_EDGE:
                undersized.append(f"{photo.label} ({size[0]}×{size[1]})")
        checks.append(QualityCheck(
            "Photo resolution", not undersized and not unreadable, bool(undersized or unreadable),
            "At least 500 px on the longest side" if not undersized and not unreadable
            else "; ".join([*(f"Unreadable: {x}" for x in unreadable), *(f"Too small: {x}" for x in undersized)]),
        ))
        checks.append(QualityCheck(
            "Front and back coverage", len(own_photos) >= 2, False,
            "Front and back supplied" if len(own_photos) >= 2 else "Add a back photo for stronger buyer confidence",
        ))

    identity_complete = bool(card and card_set and card.number and card.language)
    checks.append(QualityCheck(
        "Card identity", identity_complete, True,
        "Set, card number and language are complete" if identity_complete else "Set, card number or language is missing",
    ))
    checks.append(QualityCheck(
        "Condition", bool((condition or "").strip()), True,
        condition or "Condition is missing",
    ))

    deductions = 0
    for check in checks:
        if not check.passed:
            deductions += 25 if check.blocking else 8
    return ListingQuality(max(0, 100 - deductions), tuple(checks))


def australian_sold_listings_url(query: str) -> str:
    """Sold/completed results on eBay AU, restricted to items in Australia."""
    return "https://www.ebay.com.au/sch/i.html?" + urlencode({
        "_nkw": query, "LH_Sold": "1", "LH_Complete": "1", "LH_PrefLoc": "1",
    })


def australian_active_listings_url(query: str) -> str:
    """Current Buy It Now evidence on eBay AU for a reviewed card title."""
    return "https://www.ebay.com.au/sch/i.html?" + urlencode({
        "_nkw": query, "LH_BIN": "1", "LH_PrefLoc": "1",
    })
