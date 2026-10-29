"""Export active shop_listings and bulk_lots to a CSV for eBay's Bulk
Listing Tool.

This deliberately does NOT try to reproduce eBay's own upload template --
that template is category-specific (Seller Hub generates different "C:"
item-specific columns per category) and changes over time, so hardcoding
it here would be guessing at a schema this script has no way to verify.
Instead this writes a clean CSV with our own stable column names; you
line those up against your actual downloaded template (Seller Hub ->
Reports -> Uploads -> Get template -> "Create new listings" -> your
category -> CSV) with a copy-paste or a quick formula, once per category
rather than once per script change.

Shipping policy is by *how many cards actually ship in one parcel*, not
price -- a single-card listing is always one card regardless of what it's
worth, so it always gets --letter-policy. A "Bulk Master" lot
(bulk_lots.card_count) is the one place card count varies: a lot at or
under --lot-max-cards is still thin enough for a padded envelope, above
that it needs an actual tracked parcel.

A single at or under --lot-overlap-max-price, from a set that already has
an active Bulk Master lot, is excluded rather than exported: real
inventory check on 2026-09-13 found 545 of 990 singles were exactly this
-- build_bulk_lots.py sweeps cheap commons into a set-wide lot without
reserving specific inventory rows, so those singles were the same
physical cards the lot already promises. Exporting both risked selling
the same card twice.

Pass your *real* eBay Business Policy names (Seller Hub -> Account -> Business Policies -> Shipping) --
the defaults here are just placeholders and will not mean anything in
your eBay account.

Each row also carries a `weight_grams` estimate, since the tracked-parcel
policy uses eBay's "Calculated: cost varies by buyer location" shipping --
that needs a real per-listing weight to price correctly, and guessing it
by hand per lot is exactly the kind of manual step this script exists to
remove. See GRAMS_PER_CARD/LOT_PACKAGING_GRAMS below for the basis.

Image URL follows the exact same fallback chain the storefront itself
uses for display (storefront.common._art) -- listing's own image_url,
then the catalog item's, then the pokemontcg.io-hosted reference image
for a single card -- all of which are real, already-public URLs, checked
against _EBAY_UNSAFE_IMAGE_EXTENSIONS so a stock photo File Exchange will
actually reject (a .webp, confirmed 2026-09-25) is treated the same as
no photo at all rather than shipped and silently failing on eBay's side.

Only once every one of those comes up empty does this fall back to the
seller's own scanned photo (single_image_url -> _own_scan_photo_url),
pushed to Cloudflare R2 for a public URL when r2.is_configured -- until
2026-09-25 this app was Tailscale-only with nowhere public to put one, so
a scan's local path could never have been fetched by eBay's own servers
regardless of how it was used. A row with no image on any of those
sources -- including this last one -- is skipped, not exported with a
broken or unreachable link.

    # see what would be exported, write nothing
    .venv/bin/python -m scripts.export_ebay_bulk_listing --dry-run

    # export with your real shipping policy names
    .venv/bin/python -m scripts.export_ebay_bulk_listing \\
        --letter-policy "Letter Mail" --parcel-policy "Tracked Parcel" \\
        --out ebay_bulk_listing.csv

    # only the Bulk Master lots, not individual singles
    .venv/bin/python -m scripts.export_ebay_bulk_listing --lots-only
"""

import argparse
import csv
import sys
from decimal import ROUND_HALF_UP, Decimal

from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from sqlalchemy import select

from automation_control.adapters.r2 import R2UploadError, ensure_uploaded, is_configured as r2_is_configured
from automation_control.classification import load_pipeline_config
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_titles import build_single_card_ebay_title
from automation_control.models import (
    BulkLot, CardSet, CatalogCard, CatalogItem, InventoryItem, ShopListing, ShopListingStatus, TcgCard,
)
from storefront.common import _is_web_url, _reference_image_url

DEFAULT_LOT_MAX_CARDS = 20
DEFAULT_LETTER_POLICY = "REPLACE_WITH_YOUR_LETTER_SHIPPING_POLICY_NAME"
DEFAULT_PARCEL_POLICY = "REPLACE_WITH_YOUR_PARCEL_SHIPPING_POLICY_NAME"

# For the parcel policy's "Calculated" shipping -- eBay/Australia Post need
# a real package weight per listing to price each buyer's actual cost.
# ~3g per sleeved card (measured, not a guess from a spec sheet) plus a
# flat packaging allowance for the satchel/rigid mailer a lot ships in.
GRAMS_PER_CARD = 3
LOT_PACKAGING_GRAMS = 80
# Not price-critical (singles use the flat letter rate, not Calculated),
# but still worth a real number rather than a blank column: one sleeved,
# toploadered card plus its envelope and stiffener.
SINGLE_CARD_WEIGHT_GRAMS = 15

FIELDNAMES = ["sku", "title", "ebay_title", "card_name", "card_number", "description", "condition", "price", "currency", "quantity", "weight_grams", "image_url", "shipping_policy", "set_name", "rarity", "set_total", "variant", "set_logo_url", "game", "language"]

# Nic's updated request 2026-10-15: list Dragon Ball cards and rare-or-
# better Pokemon cards exactly 10% over the reviewed inventory price.
# This replaces the earlier 13% fee-covering uplift; it is a requested
# sale-price policy, not a claim that 10% covers every eBay fee. See
# _needs_ebay_fee_markup/_apply_ebay_fee_markup, applied only in
# build_set_grouped_rows -- build_single_rows itself keeps returning the
# listing's real, unmodified price (its own tests assert that), since
# other callers (the standalone CLI, personal_collection.py's price
# lookups) shouldn't have this baked in silently.
# eBay's own hard platform rule for a FixedPrice listing, confirmed by a
# real rejection (2026-09-26, ErrorCode 73): "The price in the listing is
# either invalid or below the minimum price of 1.0." Two real cards --
# both freshly hand-priced from genuine but very low market data (a $0.13
# USD alt-art, a $0.27 USD promo) -- cleared list_for_sale.py's own
# profit-floor/2x-market-cap tension (see that script's own MAX_MARKUP
# comment) at $0.26 and $0.54 AUD, both real, both under $1, both
# rejected. list_for_sale.py's floor is a website/profit concern; this is
# a separate, absolute eBay platform constraint that has to be enforced
# here regardless of what a card is otherwise worth, so it belongs in the
# export layer rather than in general pricing logic that also feeds the
# website (which has no such minimum).
EBAY_MIN_FIXED_PRICE = Decimal("1.00")

EBAY_FEE_MARKUP_PCT = Decimal("10")
DRAGON_BALL_GAMES = {
    "dragon_ball_fusion_world", "dragon_ball_super", "dragon_ball_z",
    "dragon_ball_super_divers", "super_dragon_ball_heroes",
}
# Whatever a card's own game/rarity vocabulary calls "unremarkable" --
# anything else (Rare, Double Rare, Secret Rare, Super Rare, blank/unknown
# included as "not confirmed common" for a non-Pokemon game since that
# game's TcgCard rows often have no rarity on file at all yet) is
# "rare enough" to mark up.
COMMON_TIER_RARITIES = {"common", "uncommon"}


def _needs_ebay_fee_markup(row: dict) -> bool:
    if row.get("game") in DRAGON_BALL_GAMES:
        return True
    return (row.get("rarity") or "").strip().lower() not in COMMON_TIER_RARITIES


def _apply_ebay_fee_markup(rows: list[dict]) -> None:
    """In place: every qualifying row's price goes up by EBAY_FEE_MARKUP_PCT."""
    multiplier = Decimal("1") + EBAY_FEE_MARKUP_PCT / Decimal("100")
    for row in rows:
        if _needs_ebay_fee_markup(row):
            row["price"] = f"{(Decimal(row['price']) * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _enforce_ebay_min_price(rows: list[dict]) -> int:
    """In place: any row still under EBAY_MIN_FIXED_PRICE (a real card,
    correctly priced by every other rule this codebase has, that's just
    genuinely worth very little) gets raised to it -- eBay will reject a
    FixedPrice listing under $1 outright, so this is the one place that
    absolute floor has to hold regardless of the row's actual source
    (single, lot, or variation child -- applied once here rather than
    duplicated in each builder). Returns how many rows were raised, for
    main()'s own reporting."""
    raised = 0
    for row in rows:
        if Decimal(row["price"]) < EBAY_MIN_FIXED_PRICE:
            row["price"] = f"{EBAY_MIN_FIXED_PRICE:.2f}"
            raised += 1
    return raised


def single_set_name(session, item: CatalogItem | None) -> str:
    if item is None:
        return ""
    tcg = session.get(TcgCard, item.id)
    if tcg is None or not tcg.set_id:
        return ""
    card_set = session.get(CardSet, tcg.set_id)
    return card_set.name if card_set else ""


def single_set_total(session, item: CatalogItem | None) -> str:
    """The set's own printed total (e.g. 162 for Temporal Forces) -- used
    to format a variation value the way real sellers do it, "001/162
    Pineco" rather than a bare number, confirmed against a real
    successful competitor listing (2026-09-14)."""
    if item is None:
        return ""
    tcg = session.get(TcgCard, item.id)
    if tcg is None or not tcg.set_id:
        return ""
    card_set = session.get(CardSet, tcg.set_id)
    if card_set is None:
        return ""
    total = card_set.printed_total or card_set.total
    return str(total) if total else ""


def single_rarity(session, item: CatalogItem | None) -> str:
    if item is None:
        return ""
    tcg = session.get(TcgCard, item.id)
    return (tcg.rarity or "") if tcg else ""


def single_language(session, item: CatalogItem | None) -> str:
    """TcgCard.language, defaulting to "English" -- the same default the
    column itself has, so a card with no TcgCard row at all still reads as
    the ordinary case rather than blank.

    This is the field that would have caught 2026-09-26's real incident
    before a customer paid for one: a physically Japanese-printed Dragon
    Ball Fusion World card, sold and pictured as the English release,
    because its whole reference catalogue is TCGplayer (a US marketplace)
    -- language defaults to English on import regardless of which print
    was actually scanned in, unless something explicitly corrects it.
    Surfaced two ways from here: build_set_grouped_rows below folds it
    into the grouping key so a set with cards in both languages becomes
    two honest listings instead of one that silently mixes them, and
    convert_to_ebay_template.py's convert_row sends it on as the eBay
    category's own real "Language" item specific (C:Language -- a
    recognised aspect on this category's actual downloaded template, with
    "Japanese" a valid value alongside "English") so it's something a
    buyer can actually see, not just something this app privately knows.
    """
    if item is None:
        return "English"
    tcg = session.get(TcgCard, item.id)
    return (tcg.language or "English") if tcg else "English"


def single_variant(session, listing: ShopListing) -> str:
    """Which printing this specific listing's copy actually is (Normal /
    Holo / Reverse Holo / ...), e.g. for a variation listing's buyer-facing
    label. Via `listing.inventory_item_id` -- the direct FK a listing
    already carries to the physical holding it represents -- rather than
    re-deriving it from the card's cached prices, which only says what
    printings *exist* for a card, not which one this particular copy is."""
    if not listing.inventory_item_id:
        return ""
    item = session.get(InventoryItem, listing.inventory_item_id)
    return item.variant.value.replace("_", " ").title() if item else ""


# eBay's own docs list WebP/AVIF/HEIC/TIFF as supported image formats, but
# that's for the newer Media/Inventory API -- File Exchange's bulk-upload
# validator checks a URL by its file extension, not the bytes behind it,
# and rejects those formats outright even when the URL serves a perfectly
# good, correctly sized image. Confirmed 2026-09-25: a Dragon Ball Super
# Divers listing failed with ErrorCode 37 ("PictureURL is invalid or
# missing") on all 4 of its .webp-named variation photos, each one
# downloaded and opened clean -- a real 600x874 PNG at that address.
#
# A denylist of the confirmed-bad extensions, not an allowlist of
# confirmed-good ones: an allowlist (tried first, 2026-09-25) rejected
# every images.scrydex.com URL too (Mega Evolution-era Pokemon cards'
# image host) -- those serve real JPGs at extensionless paths like
# ".../me5-62/small", which an allowlist has no way to recognise as safe.
# 661 real cached images use that host; a denylist lets all of them
# through (along with any other extensionless-but-real image host) while
# still catching the exact .webp files that started this.
_EBAY_UNSAFE_IMAGE_EXTENSIONS = {".webp", ".avif", ".heic", ".heif", ".tiff", ".tif", ".svg"}


def _ebay_safe_image_url(url: str | None) -> str | None:
    """`url` if File Exchange will actually accept it as a PicURL, else
    None -- checked by extension, matching how eBay's own validator
    behaves rather than what its format documentation claims. A URL with
    no extension at all is assumed safe (see _EBAY_UNSAFE_IMAGE_EXTENSIONS'
    own docstring for why an allowlist doesn't work here).

    Deliberately separate from storefront.common._is_web_url, which this
    still calls first: a .webp displays fine in a browser, so the
    storefront's own product photos are untouched by this -- it's an
    eBay File-Exchange-specific rejection, not a general "is this a real
    image" check.
    """
    if not _is_web_url(url):
        return None
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    return None if suffix in _EBAY_UNSAFE_IMAGE_EXTENSIONS else url


def single_set_logo_url(session, item: CatalogItem | None) -> str:
    """The set's own logo image, for a variation listing's parent picture
    -- a buyer picking a card from a whole set should see the set they're
    browsing, not whichever single card's photo happened to sort first."""
    if item is None:
        return ""
    tcg = session.get(TcgCard, item.id)
    if tcg is None or not tcg.set_id:
        return ""
    card_set = session.get(CardSet, tcg.set_id)
    return (_ebay_safe_image_url(card_set.logo_url) or "") if card_set else ""


def _own_scan_photo_url(session, listing: ShopListing) -> str | None:
    """The seller's own scanned photo, pushed to R2 for a public URL --
    last resort, only reached once every stock-photo source above has
    come up empty.

    This app is Tailscale-only (confirmed 2026-09-25), so a scan's local
    path was never something eBay's servers could fetch on their own --
    that's the reason this fallback didn't exist before, not a quality
    concern. R2 turns "own photo, unreachable" into "own photo, real
    URL", which only matters for the handful of cards (Japanese prints,
    Dragon Ball -- see _EBAY_UNSAFE_IMAGE_EXTENSIONS' own history) that
    have no usable stock photo anywhere else.

    Returns None -- same as every other source above -- rather than
    raising, whenever R2 isn't configured yet, upload fails, or there's
    no scan on file at all. A skip with a clear reason beats a build that
    dies partway through over one photo.
    """
    if not listing.inventory_item_id:
        return None
    settings = get_settings()
    if not r2_is_configured(settings):
        return None
    inventory_item = session.get(InventoryItem, listing.inventory_item_id)
    if inventory_item is None or not inventory_item.scan_image_path:
        return None
    local_path = Path(inventory_item.scan_image_path)

    try:
        if not local_path.exists():
            return None
        return ensure_uploaded(
            local_path, f"scan/{inventory_item.id}{local_path.suffix.lower()}",
            endpoint_url=settings.r2_endpoint_url, access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key, bucket_name=settings.r2_bucket_name,
            public_base_url=load_pipeline_config().images.base_url,
        )
    except (R2UploadError, OSError) as exc:
        # Printed, not raised: a build that's otherwise fine must not die
        # over one photo. Real incident (2026-09-26): a directory one of
        # these scans lived in had no group-read/execute permission for
        # the account this service runs as, and .exists() alone raised
        # PermissionError -- outside this function's old try block,
        # taking down the whole /ebay-export page for every listing, not
        # just this one. OSError (PermissionError's own base class) is
        # caught here for exactly that: a filesystem problem on one photo
        # must never be worse than "no photo for this one, everything
        # else still built". journalctl -u ezbay.service (web path) or
        # this script's own stdout (CLI path) is where to look afterward.
        print(f"R2 upload failed for {listing.title} ({listing.id}): {exc}", file=sys.stderr)
        return None


def single_image_url(session, listing: ShopListing, item: CatalogItem | None) -> str | None:
    # Each candidate that fails the eBay-safety check is treated exactly
    # like it was never a URL at all, falling through to the next source
    # -- so a listing whose own image_url happens to be a .webp still
    # picks up a safe one from the catalog item or the reference cache
    # when one exists there, rather than giving up immediately.
    url = _ebay_safe_image_url(listing.image_url)
    if url:
        return url
    if item is not None:
        url = _ebay_safe_image_url(item.image_url)
        if url:
            return url
    url = _ebay_safe_image_url(_reference_image_url(session, item))
    if url:
        return url
    return _own_scan_photo_url(session, listing)


def lot_shipping_policy(card_count: int, lot_max_cards: int, letter_policy: str, parcel_policy: str) -> str:
    return letter_policy if card_count <= lot_max_cards else parcel_policy


def _lot_covered_set_ids(session, status: ShopListingStatus) -> set[str]:
    return {
        set_id for (set_id,) in session.execute(
            select(BulkLot.set_id).where(BulkLot.status == status, BulkLot.set_id.isnot(None))
        ).all()
    }


def _is_pokemon_row(tcg: TcgCard | None) -> bool:
    """convert_to_ebay_template.py's convert_row hardcodes Franchise=Pokémon
    and Manufacturer=The Pokémon Company on every row it converts -- correct
    for the vast majority of this shop's stock, but a real mislabeling risk
    the day a different game's cards get catalogued too (TcgCard.game is
    exactly the field that makes CatalogItem/TcgCard generic across games,
    per that model's own docstring). A card with no TcgCard row at all
    (a Collectible/Accessory/etc.) isn't Pokemon either -- this export is
    singles-card-shaped, not a catalogue-wide one, so it's excluded the
    same way a Dragon Ball or Chinese-market card now is."""
    return tcg is not None and tcg.game == "pokemon"


def build_single_rows(session, *, status: ShopListingStatus, letter_policy: str,
                       lot_overlap_max_price: Decimal | None = Decimal("2")) -> tuple[list[dict], list[str]]:
    """Every single-card listing is one card per sale, so it's always
    letter-appropriate regardless of price -- no tiering needed here.

    Any TCG card qualifies, not just Pokemon (fixed 2026-09-22 -- see
    "game" in the row dict and convert_to_ebay_template.py's GAME_ITEM_SPECIFICS,
    which is what actually reads it): a non-card catalogue item (a
    Collectible/Accessory/etc, no TcgCard row at all) is still excluded,
    since this export is singles-card-shaped, not catalogue-wide.

    `lot_overlap_max_price` guards against double-selling: build_bulk_lots.py
    sweeps cheap commons (<=$2 by its own default) into a set-wide lot
    without reserving specific inventory rows, so a common at or under
    that price from a set that already has an active lot is the same
    physical card the lot also promises -- listing it again individually
    risks selling it twice. A single priced above the threshold in that
    same set wasn't lot-eligible in the first place and is safe. Pass
    None to disable the check entirely (e.g. if lots have been retired, or
    the caller is doing its own set-based grouping instead -- see
    build_set_grouped_rows, which always passes None since it has its own,
    unrelated notion of which cards belong together).
    """
    listings = session.scalars(
        select(ShopListing).where(ShopListing.status == status).order_by(ShopListing.title)
    ).all()
    lot_set_ids = _lot_covered_set_ids(session, status) if lot_overlap_max_price is not None else set()

    rows, skipped = [], []
    for listing in listings:
        tcg = session.get(TcgCard, listing.catalog_item_id)
        if tcg is None:
            skipped.append(f"{listing.title} ({listing.id}) -- not a TCG card (no TcgCard row; "
                           f"this export is card-shaped, not catalogue-wide)")
            continue
        item = session.get(CatalogItem, listing.catalog_item_id)
        if lot_set_ids and listing.price <= lot_overlap_max_price:
            if tcg and tcg.set_id in lot_set_ids:
                skipped.append(f"{listing.title} ({listing.id}) -- already covered by an active bulk lot for its set")
                continue
        url = single_image_url(session, listing, item)
        if not url:
            skipped.append(f"{listing.title} ({listing.id}) -- no publicly-hosted image on file")
            continue
        inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
        card_set = session.get(CardSet, tcg.set_id) if tcg.set_id else None
        outgoing_title = (
            build_single_card_ebay_title(item, tcg, card_set, inventory)
            if item is not None else listing.title
        )
        rows.append({
            "sku": listing.id,
            # Keep the short internal title for EzBay's review UI and set
            # grouping.  The final converter uses this exact-printing title
            # only for the buyer-facing eBay Title field.
            "title": listing.title,
            "ebay_title": outgoing_title,
            "card_name": item.name if item else listing.title,
            "card_number": tcg.number or "",
            "description": listing.description or listing.title,
            "condition": listing.condition or "",
            "price": f"{listing.price:.2f}",
            "currency": listing.currency,
            "quantity": listing.quantity,
            "weight_grams": SINGLE_CARD_WEIGHT_GRAMS,
            "image_url": url,
            "shipping_policy": letter_policy,
            "set_name": single_set_name(session, item),
            "rarity": single_rarity(session, item),
            "set_total": single_set_total(session, item),
            "variant": single_variant(session, listing),
            "set_logo_url": single_set_logo_url(session, item),
            "game": tcg.game,
            "language": single_language(session, item),
        })
    return rows, skipped


def _set_group_key(set_name: str, language: str) -> str:
    """The dict key build_set_grouped_rows groups under, and downstream
    (convert_to_ebay_template.py's _parent_sku/build_variation_group) the
    literal string both the parent SKU slug and the buyer-facing title
    ("<key> - Complete Your Master Set") are built from -- so folding
    language in here, rather than adding a separate field those functions
    would need to know about too, gets a set that exists in both
    languages split into two honestly-titled listings ("Cross Force" /
    "Cross Force (Japanese)") for free, with no other call site needing
    to change. English stays bare rather than "Cross Force (English)" --
    it's the overwhelming majority of this catalogue, and every existing
    title, sku, and test predates this field.
    """
    return set_name if language == "English" else f"{set_name} ({language})"


def build_set_grouped_rows(session, *, status: ShopListingStatus, letter_policy: str,
                            min_set_size: int = 2) -> tuple[list[dict], dict[str, list[dict]], list[str]]:
    """Every active listing, grouped by set (and, since 2026-09-27, by
    language within it -- see _set_group_key): a set with `min_set_size`
    or more active listings *in the same language* becomes one "pick your
    card" variation listing (see convert_to_ebay_template.py's
    build_variation_group), a smaller set's cards stay individual singles.
    No price ceiling and no Bulk Master lot dependency, unlike
    build_variation_rows -- Nic's own request 2026-09-22 was "every set I
    own becomes one listing", not just the cheap commons a lot already
    covers (that's still what build_variation_rows is for -- this is a
    second, independent grouping the /ebay-export page picks between, not
    a replacement for it).

    The language split closes a real incident (2026-09-26): a Dragon Ball
    Fusion World set mixing English and Japanese prints of the same card
    numbers was being folded into one listing under the English stock
    photo, silently misrepresenting whichever variation happened to be
    the Japanese copy. eBay's own variation model doesn't allow a second
    item specific to vary between children of one listing the way the
    picked variation trait (MPN) does -- Language has to be the same
    across every child of a single listing, so the honest fix is two
    listings, not one with an extra column.

    Returns (singles, variation_groups, skipped) -- variation_groups is
    {group_key: [row, ...]} (group_key is _set_group_key's output, not
    necessarily the bare set name), singles is every row whose
    (set, language) didn't reach min_set_size. Every price is the
    listing's own real price, EXCEPT a Dragon Ball card or a
    rare-or-better Pokemon card, which gets EBAY_FEE_MARKUP_PCT added on
    top of its reviewed inventory value (see _needs_ebay_fee_markup) --
    the current policy is the requested 10% eBay uplift.
    """
    all_rows, skipped = build_single_rows(session, status=status, letter_policy=letter_policy,
                                          lot_overlap_max_price=None)
    _apply_ebay_fee_markup(all_rows)

    by_set: dict[str, list[dict]] = {}
    for row in all_rows:
        key = _set_group_key(row["set_name"], row["language"]) if row["set_name"] else "(no set)"
        by_set.setdefault(key, []).append(row)

    singles: list[dict] = []
    variation_groups: dict[str, list[dict]] = {}
    for set_name, rows in by_set.items():
        if len(rows) >= min_set_size:
            variation_groups[set_name] = rows
        else:
            singles.extend(rows)
    return singles, variation_groups, skipped


def build_variation_rows(session, *, status: ShopListingStatus, letter_policy: str,
                          max_price: Decimal = Decimal("2"),
                          normal_discount_pct: Decimal = Decimal("40")) -> tuple[list[dict], list[str]]:
    """The exact inverse population of build_single_rows's lot-overlap
    exclusion: cheap commons whose set already has an active Bulk Master
    lot. Those are real, sellable cards -- today a buyer can only get one
    at random inside a lot of 30, with no way to pick a specific card.
    This is the candidate list for one eBay variation listing per set (see
    convert_to_ebay_template.py's build_variation_group), letting a buyer
    choose exactly which card they want at its own price, instead of
    either the anonymous lot or no individual listing at all.

    Row shape matches build_single_rows exactly (same downstream CSV,
    grouped by set_name once read back) -- each row is still one real
    card with its own sku/price/quantity/photo, just destined to become a
    variation of a shared parent listing rather than its own listing.

    `normal_discount_pct` knocks a percentage off the Normal (non-foil)
    printing only, per the user's request (2026-09-21) after seeing a real
    competitor listing price every card in a set flat at $1 regardless of
    market value: individual market pricing stays for everything, but
    Normals -- the least desirable printing, and the bulk of a set's
    filler -- undercut a bit rather than sitting at full market alongside
    the foils. Never applied to Reverse Holo/Holo/pattern printings.
    """
    listings = session.scalars(
        select(ShopListing).where(ShopListing.status == status, ShopListing.price <= max_price)
        .order_by(ShopListing.title)
    ).all()
    lot_set_ids = _lot_covered_set_ids(session, status)
    discount_multiplier = Decimal("1") - normal_discount_pct / Decimal("100")

    rows, skipped = [], []
    for listing in listings:
        tcg = session.get(TcgCard, listing.catalog_item_id)
        if not _is_pokemon_row(tcg):
            continue  # see _is_pokemon_row -- this export hardcodes Franchise=Pokémon
        if not (tcg and tcg.set_id in lot_set_ids):
            continue  # not the lot-covered population this export targets
        item = session.get(CatalogItem, listing.catalog_item_id)
        url = single_image_url(session, listing, item)
        if not url:
            skipped.append(f"{listing.title} ({listing.id}) -- no publicly-hosted image on file")
            continue
        variant = single_variant(session, listing)
        price = listing.price
        if variant == "Normal" and normal_discount_pct > 0:
            price = (price * discount_multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rows.append({
            "sku": listing.id,
            "title": listing.title,
            "card_name": item.name if item else listing.title,
            "card_number": tcg.number or "",
            "description": listing.description or listing.title,
            "condition": listing.condition or "",
            "price": f"{price:.2f}",
            "currency": listing.currency,
            "quantity": listing.quantity,
            "weight_grams": SINGLE_CARD_WEIGHT_GRAMS,
            "image_url": url,
            "shipping_policy": letter_policy,
            "set_name": single_set_name(session, item),
            "rarity": single_rarity(session, item),
            "set_total": single_set_total(session, item),
            "variant": variant,
            "set_logo_url": single_set_logo_url(session, item),
            "language": single_language(session, item),
        })
    return rows, skipped


def _lot_cover_photo_url(session, lot: BulkLot) -> str | None:
    """A lot with no image_url of its own (a hand-catalogued set with no
    real stock photo anywhere, unlike a tcgplayer/cardotaku-sourced set
    which usually has at least one member card's real image_url) still
    has something worth showing: one member card's own scanned photo,
    uploaded to R2 for a public URL -- the same last-resort a single
    already gets via _own_scan_photo_url, just picked from any card in
    the set rather than from the lot's own inventory_item_id, which
    BulkLot doesn't have (a lot isn't tied to one physical holding)."""
    settings = get_settings()
    if not r2_is_configured(settings) or not lot.set_id:
        return None
    card = session.scalar(
        select(CatalogCard).where(CatalogCard.set_id == lot.set_id, CatalogCard.local_image_path.isnot(None))
    )
    if card is None:
        return None
    local_path = Path(card.local_image_path)

    try:
        if not local_path.exists():
            return None
        return ensure_uploaded(
            local_path, f"lot-cover/{lot.id}{local_path.suffix.lower()}",
            endpoint_url=settings.r2_endpoint_url, access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key, bucket_name=settings.r2_bucket_name,
            public_base_url=load_pipeline_config().images.base_url,
        )
    except (R2UploadError, OSError) as exc:
        # See _own_scan_photo_url's identical guard for the real incident
        # (2026-09-26) this closes: a directory permission problem on one
        # card's scan crashed the whole /ebay-export page via an
        # unguarded .exists() call, not just skipped this one lot's cover.
        print(f"R2 upload failed for lot cover {lot.title} ({lot.id}): {exc}", file=sys.stderr)
        return None


def build_lot_rows(session, *, status: ShopListingStatus, lot_max_cards: int,
                    letter_policy: str, parcel_policy: str) -> tuple[list[dict], list[str]]:
    lots = session.scalars(
        select(BulkLot).where(BulkLot.status == status).order_by(BulkLot.card_count.desc())
    ).all()

    rows, skipped = [], []
    for lot in lots:
        url = _ebay_safe_image_url(lot.image_url) or _lot_cover_photo_url(session, lot)
        if not url:
            skipped.append(f"{lot.title} ({lot.id}) -- no publicly-hosted image on file")
            continue
        rows.append({
            "sku": lot.id,
            "title": lot.title,
            "description": lot.description or lot.title,
            "condition": "Used",
            "price": f"{lot.price:.2f}",
            "currency": lot.currency,
            "quantity": lot.quantity,
            "weight_grams": lot.card_count * GRAMS_PER_CARD + LOT_PACKAGING_GRAMS,
            "image_url": url,
            "shipping_policy": lot_shipping_policy(lot.card_count, lot_max_cards, letter_policy, parcel_policy),
            "set_name": lot.card_set.name if lot.card_set else "",
            "rarity": "",
            "set_total": "",
            "game": lot.game,
            # A lot is a random assortment across a whole set, not tied to
            # any one TcgCard row -- unlike a single or a variation child,
            # it was never the thing 2026-09-26's incident was about (a
            # specific, individually-priced card shown as the wrong
            # print). Left blank rather than guessed; convert_row's own
            # "English" default is exactly as accurate here as it was
            # before this field existed.
            "language": "",
        })
    return rows, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="active", choices=[s.value for s in ShopListingStatus],
                        help="only export rows in this status (default: active)")
    parser.add_argument("--lot-max-cards", type=int, default=DEFAULT_LOT_MAX_CARDS,
                        help=f"a Bulk Master lot with this many cards or fewer gets --letter-policy, "
                             f"more gets --parcel-policy (default: {DEFAULT_LOT_MAX_CARDS})")
    parser.add_argument("--letter-policy", default=DEFAULT_LETTER_POLICY,
                        help="your real eBay shipping Business Policy name for a single card or a thin lot")
    parser.add_argument("--parcel-policy", default=DEFAULT_PARCEL_POLICY,
                        help="your real eBay shipping Business Policy name for a lot over --lot-max-cards")
    parser.add_argument("--singles-only", action="store_true", help="skip Bulk Master lots")
    parser.add_argument("--lots-only", action="store_true", help="skip individual single-card listings")
    parser.add_argument("--variations", action="store_true",
                        help="export the lot-covered commons as variation candidates instead of singles/lots -- "
                             "see build_variation_rows and convert_to_ebay_template.py's --variations flag")
    parser.add_argument("--lot-overlap-max-price", type=Decimal, default=Decimal("2"),
                        help="a single at or under this price, from a set that already has an active bulk lot, is "
                             "assumed to be the same physical cards the lot promises and is excluded to avoid "
                             "double-selling (default 2, matching build_bulk_lots.py's own default). Pass a "
                             "negative number to disable this check entirely.")
    parser.add_argument("--normal-discount-pct", type=Decimal, default=Decimal("40"),
                        help="with --variations, knock this percentage off the Normal (non-foil) printing's own "
                             "market price only -- Reverse Holo/Holo/pattern printings are never discounted "
                             "(default 40, i.e. Normals list at 60%% of market)")
    parser.add_argument("--out", default="ebay_bulk_listing.csv", help="output CSV path")
    parser.add_argument("--dry-run", action="store_true", help="print what would be exported, write nothing")
    args = parser.parse_args(argv)

    if sum([args.singles_only, args.lots_only, args.variations]) > 1:
        parser.error("--singles-only, --lots-only, and --variations are mutually exclusive")

    if DEFAULT_LETTER_POLICY == args.letter_policy or DEFAULT_PARCEL_POLICY == args.parcel_policy:
        print("NOTE: using a placeholder shipping policy name -- pass --letter-policy/--parcel-policy "
              "with the real names from Seller Hub -> Account -> Business Policies -> Shipping.\n", file=sys.stderr)

    status = ShopListingStatus(args.status)
    with SessionLocal() as session:
        rows: list[dict] = []
        skipped: list[str] = []
        if args.variations:
            rows, skipped = build_variation_rows(session, status=status, letter_policy=args.letter_policy,
                                                 max_price=args.lot_overlap_max_price,
                                                 normal_discount_pct=args.normal_discount_pct)
        else:
            if not args.lots_only:
                overlap_price = args.lot_overlap_max_price if args.lot_overlap_max_price >= 0 else None
                r, s = build_single_rows(session, status=status, letter_policy=args.letter_policy,
                                         lot_overlap_max_price=overlap_price)
                rows += r
                skipped += s
            if not args.singles_only:
                r, s = build_lot_rows(session, status=status, lot_max_cards=args.lot_max_cards,
                                      letter_policy=args.letter_policy, parcel_policy=args.parcel_policy)
                rows += r
                skipped += s

    raised_to_min = _enforce_ebay_min_price(rows)

    if args.dry_run:
        print(f"would export {len(rows)} row(s):")
        for row in rows:
            print(f"  {row['sku']}  {row['title']!r}  ${row['price']}  -> {row['shipping_policy']}")
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} row(s) to {args.out}")

    if raised_to_min:
        print(f"\n{raised_to_min} row(s) were priced under eBay's ${EBAY_MIN_FIXED_PRICE} minimum "
              f"and raised to it -- a real, correctly-priced card that's just genuinely worth very little.")

    if skipped:
        print(f"\nskipped {len(skipped)} row(s) -- see reason per row:")
        for reason in skipped:
            print(f"  {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
