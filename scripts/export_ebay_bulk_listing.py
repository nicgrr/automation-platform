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
for a single card, or the set's own logo for a lot -- all of which are
real, already-public URLs. Deliberately never falls back to a scanned
"own photo": storefront.common._art's own docstring records a live bug
where a batch of those were saved upside down, so a correct stock photo
beats a wrong-looking real one here too. A row with no image on any of
those sources is skipped, not exported with a broken or unreachable
link -- this app is Tailscale-only, so a locally-hosted scan photo's URL
couldn't be fetched by eBay's own servers even if it were used.

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

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import BulkLot, CardSet, CatalogItem, InventoryItem, ShopListing, ShopListingStatus, TcgCard
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

FIELDNAMES = ["sku", "title", "description", "condition", "price", "currency", "quantity", "weight_grams", "image_url", "shipping_policy", "set_name", "rarity", "set_total", "variant", "set_logo_url"]

# Nic's request 2026-09-22: list Dragon Ball cards and rare-or-better
# Pokemon cards 13% over market to cover eBay's own cut (matches eBay
# AU's real final value fee for this category) rather than have a sale
# net less than the card's actual market price. See
# _needs_ebay_fee_markup/_apply_ebay_fee_markup, applied only in
# build_set_grouped_rows -- build_single_rows itself keeps returning the
# listing's real, unmodified price (its own tests assert that), since
# other callers (the standalone CLI, personal_collection.py's price
# lookups) shouldn't have this baked in silently.
EBAY_FEE_MARKUP_PCT = Decimal("13")
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
    return card_set.logo_url or "" if card_set else ""


def single_image_url(session, listing: ShopListing, item: CatalogItem | None) -> str | None:
    if _is_web_url(listing.image_url):
        return listing.image_url
    if item is not None and _is_web_url(item.image_url):
        return item.image_url
    return _reference_image_url(session, item)


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
        rows.append({
            "sku": listing.id,
            "title": listing.title,
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
        })
    return rows, skipped


def build_set_grouped_rows(session, *, status: ShopListingStatus, letter_policy: str,
                            min_set_size: int = 2) -> tuple[list[dict], dict[str, list[dict]], list[str]]:
    """Every active listing, grouped by set: a set with `min_set_size` or
    more active listings becomes one "pick your card" variation listing
    (see convert_to_ebay_template.py's build_variation_group), a smaller
    set's cards stay individual singles. No price ceiling and no Bulk
    Master lot dependency, unlike build_variation_rows -- Nic's own
    request 2026-09-22 was "every set I own becomes one listing", not
    just the cheap commons a lot already covers (that's still what
    build_variation_rows is for -- this is a second, independent grouping
    the /ebay-export page picks between, not a replacement for it).

    Returns (singles, variation_groups, skipped) -- variation_groups is
    {set_name: [row, ...]}, singles is every row whose set didn't reach
    min_set_size. Every price is the listing's own real price, EXCEPT a
    Dragon Ball card or a rare-or-better Pokemon card, which gets
    EBAY_FEE_MARKUP_PCT added on top of its full market value (see
    _needs_ebay_fee_markup) -- both are Nic's explicit choices (2026-09-22):
    full market value once expensive cards are included in this grouping
    at all, and a fee-covering markup on the ones actually worth
    protecting the margin on.
    """
    all_rows, skipped = build_single_rows(session, status=status, letter_policy=letter_policy,
                                          lot_overlap_max_price=None)
    _apply_ebay_fee_markup(all_rows)

    by_set: dict[str, list[dict]] = {}
    for row in all_rows:
        by_set.setdefault(row["set_name"] or "(no set)", []).append(row)

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
        })
    return rows, skipped


def build_lot_rows(session, *, status: ShopListingStatus, lot_max_cards: int,
                    letter_policy: str, parcel_policy: str) -> tuple[list[dict], list[str]]:
    lots = session.scalars(
        select(BulkLot).where(BulkLot.status == status).order_by(BulkLot.card_count.desc())
    ).all()

    rows, skipped = [], []
    for lot in lots:
        if not _is_web_url(lot.image_url):
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
            "image_url": lot.image_url,
            "shipping_policy": lot_shipping_policy(lot.card_count, lot_max_cards, letter_policy, parcel_policy),
            "set_name": lot.card_set.name if lot.card_set else "",
            "rarity": "",
            "set_total": "",
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

    if skipped:
        print(f"\nskipped {len(skipped)} row(s) -- see reason per row:")
        for reason in skipped:
            print(f"  {reason}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
