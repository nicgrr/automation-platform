import csv
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .config import EbayFieldsConfig, ImagesConfig, PipelineConfig
from .models import Listing
from .render import RenderedListing

ACTION_VALUE = "Add"

CENTS = Decimal("0.01")

# Fields eBay expects to see literally "Mixed" whenever a listing bundles
# multiple cards, per the source requirement.
FORCED_MIXED_FIELDS = ("set_name", "card_number", "rarity", "character")

# Pokemon TCG card-type suffixes stripped from C:Character only -- the
# *Title keeps the full card name (e.g. "Alcremie VMAX"), but the Character
# item specific is expected to be the base name (e.g. "Alcremie").
CARD_TYPE_SUFFIXES = ("VMAX", "VSTAR", "GX", "EX", "ex", "V", "BREAK", "Prime")
CARD_TYPE_SUFFIX_PATTERN = re.compile(r"\b(" + "|".join(re.escape(s) for s in CARD_TYPE_SUFFIXES) + r")$")

COLUMNS = [
    "CustomLabel",
    "*Category",
    "StoreCategory",
    "*Title",
    "Subtitle",
    "*ConditionID",
    "PicURL",
    "*Description",
    "*Format",
    "*Duration",
    "*StartPrice",
    "BuyItNowPrice",
    "*Quantity",
    "*Location",
    "ShippingProfileName",
    "ReturnProfileName",
    "PaymentProfileName",
    "BestOfferEnabled",
    "BestOfferAutoAcceptPrice",
    "MinimumBestOfferPrice",
    "C:Game",
    "C:Language",
    "C:Card Condition",
    "C:Graded",
    "C:Set",
    "C:Card Number",
    "C:Rarity",
    "C:Character",
]


def _action_header(ebay: EbayFieldsConfig) -> str:
    return f"*Action(SiteID={ebay.site_id}|Country={ebay.country}|Currency={ebay.currency}|Version={ebay.bulk_upload_version})"


def _money(value: Decimal) -> str:
    return str(value.quantize(CENTS, rounding=ROUND_HALF_UP))


def _strip_card_type_suffix(name: str) -> str:
    match = CARD_TYPE_SUFFIX_PATTERN.search(name)
    return name[: match.start()].rstrip() if match else name


def _photos_per_card(listing: Listing, images: ImagesConfig) -> int:
    return images.photos_per_card_bundle if listing.is_bundle else images.photos_per_card_single


def derive_image_filenames(listing: Listing, images: ImagesConfig) -> list[str]:
    total = len(listing.cards) * _photos_per_card(listing, images)
    return [
        images.filename_pattern.format(custom_label=listing.custom_label, number=listing.sale_plan_number, index=i)
        for i in range(1, total + 1)
    ]


def derive_image_urls(listing: Listing, images: ImagesConfig) -> list[str]:
    base = images.base_url.rstrip("/")
    return [f"{base}/{name}" for name in derive_image_filenames(listing, images)]


def _shot_label(card, photo_index_within_card: int, photos_per_card: int) -> str:
    if photos_per_card == 2:
        return f"{card.character} FRONT" if photo_index_within_card == 1 else f"{card.character} BACK"
    if photos_per_card == 1:
        return card.character
    return f"{card.character} (photo {photo_index_within_card})"


def _photo_plan(listing: Listing, images: ImagesConfig) -> list[tuple]:
    photos_per_card = _photos_per_card(listing, images)
    filenames = derive_image_filenames(listing, images)
    plan = []
    idx = 0
    for card in listing.cards:
        for within in range(1, photos_per_card + 1):
            plan.append((card, _shot_label(card, within, photos_per_card), filenames[idx]))
            idx += 1
    return plan


def _bundle_field(listing: Listing, attr: str, warnings: list[str]) -> str:
    values = {getattr(card, attr) for card in listing.cards}
    if len(values) == 1:
        return values.pop()
    warnings.append(f"listing '{listing.custom_label}': cards disagree on '{attr}' ({', '.join(sorted(values))}); using 'Mixed'")
    return "Mixed"


def _card_fields(listing: Listing, warnings: list[str]) -> dict[str, str]:
    if not listing.is_bundle:
        card = listing.cards[0]
        return {
            "language": card.language,
            "graded": card.graded,
            "set_name": card.set_name,
            "card_number": card.card_number,
            "rarity": card.rarity,
            "character": _strip_card_type_suffix(card.character),
        }
    fields = {attr: _bundle_field(listing, attr, warnings) for attr in ("language", "graded")}
    fields.update({attr: "Mixed" for attr in FORCED_MIXED_FIELDS})
    return fields


def _auto_accept_price(listing: Listing, config: PipelineConfig) -> Decimal:
    return listing.list_price * config.pricing.auto_accept_pct


def write_bulk_upload_csv(rendered: list[RenderedListing], config: PipelineConfig, out_path: str | Path, warnings: list[str]) -> None:
    ordered = sorted(rendered, key=lambda r: r.listing.custom_label)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([_action_header(config.ebay)] + COLUMNS)
        for item in ordered:
            listing = item.listing
            fields = _card_fields(listing, warnings)
            image_urls = derive_image_urls(listing, config.images)
            if listing.list_price <= listing.floor_price:
                warnings.append(f"listing '{listing.custom_label}': list_price ({listing.list_price}) is at or below floor_price ({listing.floor_price})")
            row = [
                ACTION_VALUE,
                listing.custom_label,
                config.ebay.category_id,
                "",
                item.title,
                "",
                config.ebay.condition_id,
                "|".join(image_urls),
                item.description_html,
                "FixedPrice",
                "GTC",
                _money(listing.list_price),
                "",
                config.ebay.default_quantity,
                config.ebay.item_location,
                config.ebay.shipping_profile_name,
                config.ebay.return_profile_name,
                config.ebay.payment_profile_name,
                "1",
                _money(_auto_accept_price(listing, config)),
                _money(listing.floor_price),
                config.ebay.game,
                fields["language"],
                config.ebay.default_condition_text,
                fields["graded"],
                fields["set_name"],
                fields["card_number"],
                fields["rarity"],
                fields["character"],
            ]
            writer.writerow(row)


def write_image_checklist_csv(rendered: list[RenderedListing], config: PipelineConfig, out_path: str | Path) -> None:
    ordered = sorted(rendered, key=lambda r: r.listing.custom_label)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Listing ID", "Listing", "Image filename", "What to shoot"])
        for item in ordered:
            listing = item.listing
            listing_id = f"{listing.sale_plan_number:02d}"
            for card, shot_label, filename in _photo_plan(listing, config.images):
                writer.writerow([listing_id, item.title, filename, shot_label])


def write_validation_report(warnings: list[str], out_path: str | Path) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Warning"])
        for warning in sorted(warnings):
            writer.writerow([warning])
