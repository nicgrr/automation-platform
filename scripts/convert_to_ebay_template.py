"""Convert our own ebay_bulk_listing.csv (see export_ebay_bulk_listing.py)
into eBay's real File Exchange bulk-upload format for the Trading Cards
category -- the one downloaded from Seller Hub -> Reports -> Uploads ->
Get template.

That file is a real oddity worth knowing before you touch it by hand:
it's \\r-delimited (old Mac-style line endings), not \\n, so `wc -l` and a
plain text editor both misread it as one giant line. Row 1 is an "Info"
marker, row 2 is the actual column header (starting with the
"*Action(...)" column), row 3 onward is data. Seller Hub's own download
was 133 columns; C:Game/C:Finish were appended by hand 2026-09-27 after
confirming this category's real item-specifics editor recognises both
(the download just doesn't pre-populate every valid aspect as its own
column) -- eBay's File Exchange matches columns by header name, not
position, so appending rather than inserting is safe.

Filled in confidently from our own catalogue: title, description, price,
quantity, photo, both shipping policies (by the same letter/parcel tier
export_ebay_bulk_listing.py already computed), and Trading Card item
specifics (set, card name/number, franchise, manufacturer) for anything
that's actually a Pokemon card. Category and condition are also filled in
automatically now -- SINGLE_CATEGORY_ID/LOT_CATEGORY_ID/
DEFAULT_CONDITION_ID below, verified against eBay's own category URLs
rather than guessed, not something you need to supply.

Still left as placeholders needing a real value from you, because
guessing wrong here risks a rejected listing, not just a cosmetic mistake:

  --location          Suburb or postcode you're shipping from (required
                       by eBay on every listing).
  --dispatch-days      Handling time in business days.
  --payment-policy     Your eBay Payment Business Policy name.
  --return-policy       Your eBay Return Business Policy name.

    # see what's still a placeholder, write nothing
    .venv/bin/python -m scripts.convert_to_ebay_template --dry-run

    # once you have every real value
    .venv/bin/python -m scripts.convert_to_ebay_template \\
        --location "Sydney NSW 2000" --dispatch-days 1 \\
        --payment-policy "eBay Managed Payments" --return-policy "30 Day Returns" \\
        --template ebay-category-listing-template.csv --in ebay_bulk_listing.csv \\
        --out ebay_bulk_listing_ready.csv
"""

import argparse
import csv
import re
import sys
import unicodedata

_LOT_CARD_COUNT_RE = re.compile(r"\((\d+) cards\)")

# eBay rejects "superscript, subscript, or similar characters (e.g., (tm),
# heart, 1/2, star)" in titles, subtitles, and item specifics -- confirmed
# by a real failed upload on card names that are *literally* printed with
# a gender symbol (Nidoran (female)/(male), and the whole Nidoran family).
# Spelled-out replacements first (readable, matches how sellers actually
# write these -- see import_ebay_prices.py's build_query, which strips the
# same two symbols for the same underlying reason), then a general net for
# any other symbol/fraction character eBay would reject that this
# specific card family isn't the only one to ever carry.
_TITLE_REPLACEMENTS = {"♀": "F", "♂": "M"}
_DISALLOWED_UNICODE_CATEGORIES = {"So", "Sk", "Sm", "No"}


def sanitize_title(text: str) -> str:
    for symbol, replacement in _TITLE_REPLACEMENTS.items():
        text = text.replace(symbol, replacement)
    return "".join(c for c in text if unicodedata.category(c) not in _DISALLOWED_UNICODE_CATEGORIES)

PLACEHOLDER = "REPLACE_ME"

# The two header rows are \r-delimited; everything after is real data
# once opened correctly, so a plain csv.reader over the \r-split text
# works exactly like any other CSV from there.
INFO_ROW_PREFIX = "﻿Info,Version="


def read_ebay_header(template_path: str) -> tuple[str, list[str]]:
    """Returns (info row, column header).

    Both matter on the way back out. The Info row is how File Exchange
    identifies which template and version a file was built from, and an
    upload without it fails every row while still reporting a row count --
    so it looks like 1,014 bad listings rather than one bad file.
    """
    with open(template_path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    rows = raw.split("\r")
    if len(rows) < 2 or not rows[1].startswith("*Action("):
        raise SystemExit(
            f"{template_path} doesn't look like an eBay File Exchange template "
            "(expected an Info row then a header row starting with '*Action(...)')"
        )
    if not rows[0].startswith("Info,"):
        raise SystemExit(
            f"{template_path}: row 1 should be the Info marker, got {rows[0][:60]!r}"
        )
    return rows[0], next(csv.reader([rows[1]]))


TYPE = "Trading Card"
FRANCHISE = "Pokémon"
MANUFACTURER = "The Pokémon Company"

# Franchise/Manufacturer by TcgCard.game -- (row.get("game") is missing for
# rows build_variation_rows produces, since that population is Pokemon-only
# today; FRANCHISE/MANUFACTURER above stay the fallback for that case).
# Only the Pokemon pair is confirmed against a real upload; the rest are
# this app's best real-world answer (Bandai publishes every Dragon Ball
# and One Piece card game) but haven't been upload-tested the way 4000/
# 40001 above were -- verify with one test listing per game before a big
# batch, same caution the module docstring already gives lot condition IDs.
GAME_ITEM_SPECIFICS: dict[str, tuple[str, str]] = {
    "pokemon": (FRANCHISE, MANUFACTURER),
    "pokemon_japan": (FRANCHISE, MANUFACTURER),
    "one_piece": ("One Piece", "Bandai"),
    "dragon_ball_fusion_world": ("Dragon Ball", "Bandai"),
    "dragon_ball_super": ("Dragon Ball", "Bandai"),
    # NOT Bandai -- confirmed 2026-09-28 auditing every Dragon Ball game key
    # after fixing the Artbox mislabeling below: dragon_ball_z is this
    # catalogue's key for the Score/Panini-published Dragon Ball Z CCG
    # (dbz-1712 "Panini: Set 1", dbz-1709 "Panini: Evolution", etc. -- 1806
    # real cards, every one of their own CardSet names already saying
    # Panini). Bandai never published this particular game; it's a
    # different company's licensed product entirely. 0 of these are
    # currently listed (all still catalogue-only), but the wrong value
    # was live in this dict regardless -- fixed before the first one ever
    # gets priced, not after.
    "dragon_ball_z": ("Dragon Ball", "Panini"),
    "dragon_ball_super_divers": ("Dragon Ball", "Bandai"),
    "super_dragon_ball_heroes": ("Dragon Ball", "Bandai"),
    "dragon_ball_carddass": ("Dragon Ball", "Bandai"),
    "dragon_ball_itajaga": ("Dragon Ball", "Bandai"),
    # NOT Bandai either -- this catalogue's only dragon_ball_art_card set
    # is dbha10-unofficial, whose own name says "(Unofficial)" (confirmed
    # 2026-09-28: a fan-made product, not a real Bandai release). Claiming
    # Bandai as manufacturer would misrepresent an unlicensed item as the
    # genuine article -- "Unbranded" is eBay's own standard convention for
    # exactly this (no real manufacturer to name), not a guess at one.
    # 1 real active listing carries this today.
    "dragon_ball_art_card": ("Dragon Ball", "Unbranded"),
    # A real, distinct brand, not Bandai: these 12 cards are 1996 Artbox
    # Entertainment trading cards (confirmed by each card's own name, e.g.
    # "Piccolo (Artbox '96)" -- see scripts/catalog_sheet_400694.py, the
    # hand-cataloguing script that first identified them). Split out of
    # the shared "dragon_ball_z" key 2026-09-27 after the set they lived
    # in was found mislabeled "Dragon Ball Z (Uncoded/Panini)" in the
    # /ebay-export UI -- Panini was itself just a placeholder guess, and
    # sharing "dragon_ball_z" with the real Panini-published Score/Panini
    # DBZ CCG sets (1806 other cards, see dbz-1712/dbz-1709/etc.) would
    # have carried Manufacturer=Bandai onto these regardless of which
    # placeholder name won -- neither Panini nor Bandai actually made them.
    "dragon_ball_artbox": ("Dragon Ball", "Artbox"),
}

# eBay's own "Game" item specific -- required by this category's own
# item-specifics editor (confirmed 2026-09-27 against a live listing:
# "Required -- Game" showing blank), but nothing in this pipeline ever
# populated it -- GAME_ITEM_SPECIFICS above only ever fed Franchise/
# Manufacturer, no call site read row["game"] for the Game picklist itself.
# Values here are this app's best real-world answer, not upload-verified
# the way Pokemon's Franchise/Manufacturer pair is -- same caution as
# GAME_ITEM_SPECIFICS above, verify with one test listing per game before
# a big batch. Dragon Ball Carddass/art cards are deliberately omitted:
# neither is really "a game" in the sense this picklist means, so a
# guessed value risks being wrong rather than merely unconfirmed.
GAME_DISPLAY_NAMES: dict[str, str] = {
    "pokemon": "Pokémon TCG",
    "pokemon_japan": "Pokémon TCG",
    "one_piece": "One Piece Card Game",
    "dragon_ball_fusion_world": "Dragon Ball Fusion World",
    "dragon_ball_super": "Dragon Ball Super Card Game",
    "dragon_ball_z": "Dragon Ball Z Card Game",
    "dragon_ball_super_divers": "Dragon Ball Super Divers",
    "super_dragon_ball_heroes": "Super Dragon Ball Heroes",
    "dragon_ball_itajaga": "Dragon Ball Itajaga",
}

# Verified against eBay's own category URLs (multiple independent listing
# pages under each ID), not guessed: Toys & Hobbies > Collectible Card
# Games > CCG Individual Cards is 183454, and its sibling > Mixed Card
# Lots is 183455 -- a bulk lot of many cards is a genuinely different
# eBay category from a single card, not the same one with a different
# quantity.
SINGLE_CATEGORY_ID = "183454"
LOT_CATEGORY_ID = "183455"
# Ungraded is the only condition that makes sense for a scanned personal
# collection (2750 is Graded, i.e. professionally slabbed, which none of
# this inventory is). Confirmed correct for singles by a real upload:
# 4000 was accepted for category 183454, and the only real failure there
# was the missing CD:40001 descriptor below -- but 4000 was explicitly
# *rejected* for 183455 (Mixed Card Lots), so lots need a different value
# -- see DEFAULT_LOT_CONDITION_ID below, no longer a placeholder.
DEFAULT_CONDITION_ID = "4000"

# Confirmed 2026-09-28 by asking eBay directly rather than guessing or
# listing a test item by hand: GetItem (via adapters/ebay_trading.py's
# get_item_condition, built for exactly this) against 3 real, active,
# category-183455 lot listings already on this account --
# 267785473775/267785473779/267785476854 -- all three came back
# ConditionID=3000 ("Used"), consistent across every one of them.
DEFAULT_LOT_CONDITION_ID = "3000"

# eBay's Condition Descriptor for ungraded CCG singles (ID 40001) is a
# required sub-field of ConditionID 4000, confirmed by a real failed
# upload ("Card Condition (40001) is a required field" on 990/1014 rows).
# Two independent community sources agree the numeric form is the
# reliable one to send, and both agree 400010 means "Near Mint or
# Better" -- which is what this whole export already assumes as the
# scanned condition, so one constant covers the actual data rather than
# a full text->ID mapping table for conditions nothing here currently has.
CARD_CONDITION_DESCRIPTOR_NEAR_MINT = "400010"

# Confirmed by two real, distinct upload failures (2026-09-26): eBay
# rejects "Card Name" (C:Card Name, error 21919308) and the variation
# trait MPN (error 21919318) alike past 65 characters -- "Majin Buu :
# Evil - FB04-091 (Dragon Ball Z Dokkan Battle Alternate Art)" (Card
# Name, 73 chars) and "EX25-39 - Kale & Caulifla, Becoming the Strongest
# in the Multiverse (N)" (MPN, 73 chars) both bounced with the exact same
# "no more than 65 characters" message. Neither field was ever truncated
# anywhere in this pipeline, unlike *Title (already capped at 80).
EBAY_ITEM_SPECIFIC_MAX_LEN = 65

def _truncate(value: str, max_len: int) -> str:
    return value[:max_len].rstrip() if len(value) > max_len else value


def _build_description(row: dict, *, is_lot: bool, name: str, number: str, game_name: str | None) -> str:
    """*Description's actual body text -- what a buyer reads, not an item
    specific they have to open a dropdown to see. Until 2026-09-27 this
    was just `listing.description or listing.title` (row["description"]),
    which for the overwhelming majority of listings meant a description
    that was the literal title again: no set, no condition statement, no
    language, nothing this app already knows and had computed for the
    item specifics sitting right next to it.

    Built from the same structured fields the item specifics already use
    (set_name, rarity, variant, language, weight_grams) rather than
    inventing new copy to maintain in two places -- and deliberately
    plain and factual, not sales copy: a real business description this
    codebase has no basis to write persuasively on its own behalf.
    """
    language = row.get("language") or "English"
    if is_lot:
        lines = [row["title"]]
        set_name = row.get("set_name")
        if set_name:
            lines.append(f"A random assortment of cards from {set_name}, pulled from our own scanned inventory.")
        if language != "English":
            lines.append(f"Language: {language}.")
        lines.append("Condition: Near Mint or Better throughout. Ships promptly, cards protected in sleeves.")
        return " ".join(lines)

    lines = [f"{name} #{number}".strip() if number else name]
    detail_bits = [b for b in (row.get("set_name"), game_name) if b]
    if detail_bits:
        lines.append(" -- ".join(detail_bits) + ".")
    spec_bits = [b for b in (row.get("rarity"), row.get("variant")) if b]
    if spec_bits:
        lines.append(" / ".join(spec_bits) + ".")
    if language != "English":
        lines.append(f"Language: {language}.")
    lines.append("Condition: Near Mint or Better. Carefully inspected and photographed from our own collection.")
    lines.append("Ships promptly with protective sleeve and toploader.")
    return " ".join(lines)


def convert_row(row: dict, args) -> dict:
    is_lot = "bulk lot" in row["title"].lower()
    internal_title = sanitize_title(row["title"])
    title = sanitize_title(row.get("ebay_title") or internal_title)
    parsed_name, _, parsed_number = internal_title.partition(" #")
    # New exports carry the canonical identity separately because the
    # buyer-facing title now contains set/rarity/game/language too.  Keep
    # the fallback for older saved intermediate CSVs.
    name = sanitize_title(row.get("card_name") or parsed_name)
    number = sanitize_title(row.get("card_number") or parsed_number)
    franchise, manufacturer = GAME_ITEM_SPECIFICS.get(row.get("game") or "pokemon", (FRANCHISE, MANUFACTURER))
    game_name = GAME_DISPLAY_NAMES.get(row.get("game") or "pokemon")

    out = {
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)": "Add",
        "CustomLabel": row["sku"],
        "*Category": LOT_CATEGORY_ID if is_lot else SINGLE_CATEGORY_ID,
        "*Title": title[:80],
        "*ConditionID": args.lot_condition_id if is_lot else args.condition_id,
        "*C:Type": TYPE,
        "*C:Franchise": franchise,
        "*C:Manufacturer": manufacturer,
        "PicURL": row["image_url"],
        "*Description": sanitize_title(_build_description(row, is_lot=is_lot, name=name, number=number, game_name=game_name)),
        "*Format": "FixedPrice",
        "*Duration": "GTC",
        "*StartPrice": row["price"],
        "*Quantity": row["quantity"],
        "*Location": args.location,
        "*DispatchTimeMax": args.dispatch_days,
        "*ReturnsAcceptedOption": "ReturnsAccepted",
        "ShippingProfileName": row["shipping_policy"],
        "ReturnProfileName": args.return_policy,
        "PaymentProfileName": args.payment_policy,
        "C:Item Weight": f"{row['weight_grams']} g",
        "*C:Set": row["set_name"],
        # Real, category-recognised aspect (C:Language, confirmed against
        # the downloaded template -- "Japanese" is one of its own listed
        # recommended values), not a bespoke field: this is what makes the
        # language a buyer can actually see on the listing, rather than
        # something only this app's own database knows. Added 2026-09-27
        # after a real sale of a Japanese-printed Dragon Ball card pictured
        # and catalogued as the English release -- see
        # export_ebay_bulk_listing.py's single_language for where the
        # value itself comes from.
        "C:Language": row.get("language") or "English",
    }
    if game_name:
        out["C:Game"] = game_name
    if is_lot:
        match = _LOT_CARD_COUNT_RE.search(row["title"])
        if match:
            out["C:Number of Cards"] = match.group(1)
        # Not populated: 183455's own condition-descriptor scheme (if it
        # has one at all) is unverified, unlike the singles' 4000/40001
        # pair below -- confirmed only once already, no need to guess twice.
        # C:Finish is also left blank here, deliberately: a lot mixes many
        # cards' printings under one sku, so a single Finish value would
        # misrepresent whichever ones aren't foil (or aren't all the same
        # foil), unlike a single-card row where the printing is the row.
    else:
        out["C:Card Name"] = _truncate(name.strip(), EBAY_ITEM_SPECIFIC_MAX_LEN)
        if number:
            out["C:Card Number"] = number.strip()
        if row.get("rarity"):
            out["C:Rarity"] = sanitize_title(row["rarity"])
        if row.get("variant"):
            # Real per-card data (export_ebay_bulk_listing.py's
            # single_variant/InventoryItem.variant), not a bespoke guess --
            # same reasoning as C:Language above: a buyer should be able to
            # see Normal/Holo/Reverse Holo as a real, searchable item
            # specific rather than something only buried in the title.
            # Unverified against a real upload (like every non-Pokemon
            # GAME_ITEM_SPECIFICS entry above) -- confirm the exact
            # picklist values with one test listing before a big batch.
            out["C:Finish"] = row["variant"]
        out["CD:Card Condition - (ID: 40001)"] = CARD_CONDITION_DESCRIPTOR_NEAR_MINT
    return out


def _parent_sku(set_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", set_name.lower()).strip("-")
    return f"set-variation-{slug}"


def build_variation_group(set_name: str, cards: list[dict], args) -> tuple[list[dict], list[str]]:
    """One eBay variation listing for a whole set: an empty-Relationship
    parent row plus one Relationship=Variation child row per card, each
    keeping its own price/quantity/photo/sku. Mechanics are from eBay's
    own File Exchange docs, verified against a real upload (2026-09-13,
    "Fusion Strike").

    Per those docs: Relationship is empty on the parent and "Variation" on
    every child; RelationshipDetails lists every possible value on the
    parent (Trait=Value1;Value2;...) and the one specific value that row
    is on each child (Trait=Value).

    The trait is "MPN" and the value is buyer-facing card detail, for
    example "001/162 - Pineco - Normal - Common": number/set total, card
    name, printing, then rarity.  A promo or other set with no meaningful
    printed total simply starts "8 - Golduck".  Earlier exports copied a
    competitor's terse "001 - Weedle (N)" convention, but the abbreviations
    and missing denominator/rarity made the only label eBay shows in its
    mobile variation picker needlessly cryptic.  Prefer the complete values
    EzBay already knows; only the card-name portion is shortened when the
    category's hard 65-character limit requires it.
    "Card #" as the trait name was rejected before either reference was
    found ("Card Number is not allowed as a variation specific" -- Card
    Number is a required item specific in this category and can't double
    as the variation trait); MPN sidesteps that conflict entirely.

    A second card sharing the same printed number *and the same printing*
    is skipped, not included as a second variation with a colliding trait
    value: a real upload rejected 3 of 24 sets with "Duplicate
    VariationSpecifics trait value" for exactly this (a reprint/alt-art
    sharing its base card's number). eBay's variation model has no room
    for two options with an identical trait value. A different printing of
    the *same* number (Normal vs. Reverse Holo of card #001) is not this
    case -- the printing is folded into the value itself ("001 - Weedle
    (RH)"), which is also how a buyer is told which is which. The skipped
    card stays real inventory, just not sold through this listing; the
    returned skip reasons are what a caller reports so it isn't silently
    dropped.

    That dedup key falls back to the card's own name, not "n/a", when
    C:Card Number is the literal placeholder "n/a" -- a real, distinct
    physical card can genuinely have no printed number at all (1996
    Artbox Dragon Ball Z cards, confirmed 2026-09-27), and every one of
    them partitions to the same "#n/a" title suffix. Keying on the bare
    number as usual would make this function think all of them are
    reprints of one single "n/a" card and silently drop every one but the
    first -- confirmed live: 11 of a real 12-card set were dropped this
    way before this fallback existed. Name is what actually distinguishes
    these specific cards when there's no real numbering scheme to key
    off; a genuine same-name-same-printing duplicate is still caught.

    Each child's PicURL is "<its own MPN value>=<its own photo url>", not
    a bare URL -- confirmed 2026-09-27 against eBay's own File Exchange
    Advanced Template Instructions (v3.5.2, PicURL/"Using relationship
    fields for variations", pp.24-25): a variation row's PicURL must be
    keyed by the exact trait value used in that same row's own
    RelationshipDetails ("Black=URL1|URL2|URL3" for a Colour=Black
    variation; pipe-separate multiple photos for one value), and "PicURL
    values for variations of a given item must use a single trait name"
    -- satisfied here since MPN is the only trait this listing ever uses,
    and every child's MPN value is already unique (see the dedup guard
    above), so no two children ever need to share one photo entry. This
    replaces an earlier, wrong 2026-09-27 fix that instead pooled every
    child's photo onto the *parent's* PicURL as a flat comma-joined list
    -- there is no such pool mechanism; each variation's picture lives on
    its own row, keyed to its own value. That wrong fix was never
    upload-tested before this correction, only reasoned from a community
    forum summary that turned out to describe a different (Trading API)
    mechanism.

    The parent (item) row's own PicURL is unrelated to any of this: it's
    the ordinary single "typical example" photo shown before a buyer
    picks a variation -- the set logo, or the first card's photo if the
    set has none, same as before this whole investigation.
    """
    VARIATION_TRAIT = "MPN"
    values: list[str] = []
    seen: set[tuple[str, str]] = set()
    child_rows = []
    skipped = []
    for card in cards:
        child = convert_row(card, args)
        number = child.get("C:Card Number", "").strip()
        variant = (card.get("variant") or "").strip()
        if not number:
            skipped.append(f"{card['title']} ({card['sku']}) -- no readable card number, can't be a variation")
            continue
        dedup_number = child.get("C:Card Name", "") if number.casefold() == "n/a" else number
        key = (dedup_number, variant)
        if key in seen:
            skipped.append(f"{card['title']} ({card['sku']}) -- another {variant or 'copy'} of card number {number} "
                            f"in {set_name} already uses this exact value (a reprint/alt-art sharing its base "
                            f"number); list this one separately")
            continue
        seen.add(key)
        set_total = (card.get("set_total") or "").strip()
        padded = number.zfill(len(set_total)) if set_total else number
        number_detail = f"{padded}/{set_total}" if set_total else padded
        # Spell out the details a buyer needs to distinguish the physical
        # card.  The set is already the listing title, language is already
        # split into a separate parent, and condition is common to every
        # variation, so repeating those here would only consume eBay's
        # 65-character allowance without adding useful choice information.
        detail_parts = [part for part in (variant, (card.get("rarity") or "").strip()) if part]
        suffix = "" if not detail_parts else " - " + " - ".join(detail_parts)
        base = f"{number_detail} - {child.get('C:Card Name', '')}".strip()
        base = base.replace(";", ",").replace("|", "-")
        suffix = suffix.replace(";", ",").replace("|", "-")
        # Keep at least the full number and one character of the card name
        # even if a future catalogue introduces an unexpectedly long
        # rarity. Printing comes first in the suffix, so this last-resort
        # shortening still preserves the variation's key distinction.
        minimum_base_len = len(f"{number_detail} - ") + 1
        suffix = _truncate(suffix, max(0, EBAY_ITEM_SPECIFIC_MAX_LEN - minimum_base_len))
        # eBay error 21920469 treats the JavaScript function token ``eval``
        # as active content even when it merely occurs inside an innocent
        # card name.  A real upload rejected both "Energy Retrieval" values
        # for that reason. Keep that common card name buyer-friendly, then
        # break the same token safely should it occur in another name.
        base = re.sub(r"\bretrieval\b", "Retr.", base, flags=re.IGNORECASE)
        base = re.sub(r"eval", "e-val", base, flags=re.IGNORECASE)
        # Truncate the name portion only, reserving room for the full
        # printing/rarity suffix -- truncating the combined value risked
        # cutting the distinguishing printing itself off, which would make
        # two printings of an overly-long card name collide (eBay's
        # "Duplicate VariationSpecifics trait value" rejection, a
        # different real failure this same function already guards
        # against for the ordinary case via `seen`).
        value = _truncate(base, EBAY_ITEM_SPECIFIC_MAX_LEN - len(suffix)) + suffix
        values.append(value)
        child["Relationship"] = "Variation"
        child["RelationshipDetails"] = f"{VARIATION_TRAIT}={value}"
        # Keyed by that exact same value, per eBay's own File Exchange
        # docs (see this function's docstring) -- a bare URL here is what
        # a buyer's variation picker has nothing to match, and never
        # displays that card's own photo once selected. Guarded rather
        # than assumed non-empty: single_image_url already filters out
        # rows with no photo before they ever reach this function, but a
        # defensive check costs nothing against a future call site that
        # doesn't.
        if child.get("PicURL"):
            child["PicURL"] = f"{value}={child['PicURL']}"
        child_rows.append(child)

    if not child_rows:
        return [], skipped

    parent = dict(child_rows[0])
    parent.update({
        "CustomLabel": _parent_sku(set_name),
        "*Title": sanitize_title(f"{set_name} - Complete Your Master Set")[:80],
        # set_name already carries a "(Japanese)" suffix when this whole
        # group is one language (export_ebay_bulk_listing.py's
        # _set_group_key), so it doesn't need a separate language clause
        # here the way a single/lot row does in _build_description --
        # it's already in the one line every buyer reads first.
        "*Description": sanitize_title(
            f"Choose your card from {set_name} -- {len(child_rows)} available, each at its own price. "
            "Select a variation below to see the specific card and photo. "
            "Condition: Near Mint or Better throughout. Ships promptly with protective sleeve and toploader."
        ),
        "*Quantity": "",
        "*StartPrice": "",
        # The parent's own picture is the set's logo, not a pool and not
        # any one card's keyed variation photo (child_rows[0]'s own
        # PicURL is now "<value>=<url>", which is meaningless as the
        # item-level picture) -- see this function's docstring. Falls
        # back to the first card's plain photo only if the set has none.
        "PicURL": cards[0].get("set_logo_url") or cards[0]["image_url"],
        "C:Card Name": "",
        "C:Card Number": "",
        "C:Rarity": "",
        # C:Finish too -- otherwise the parent silently inherits
        # child_rows[0]'s printing (e.g. "Holo") and misrepresents every
        # other Normal/Reverse Holo card in the set, the same class of bug
        # 2026-09-26's language incident was about.
        "C:Finish": "",
        "Relationship": "",
        "RelationshipDetails": f"{VARIATION_TRAIT}=" + ";".join(values),
    })
    return [parent] + child_rows, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", default="ebay-category-listing-template.csv",
                        help="the real template downloaded from Seller Hub")
    parser.add_argument("--in", dest="in_path", default="ebay_bulk_listing.csv",
                        help="our own export (see export_ebay_bulk_listing.py)")
    parser.add_argument("--out", default="ebay_bulk_listing_ready.csv")
    parser.add_argument("--condition-id", default=DEFAULT_CONDITION_ID,
                        help=f"eBay condition ID for SINGLE cards (default {DEFAULT_CONDITION_ID} = Ungraded -- "
                             "confirmed correct by a real upload; override only if some of this inventory is graded)")
    parser.add_argument("--lot-condition-id", default=DEFAULT_LOT_CONDITION_ID,
                        help=f"eBay condition ID for BULK LOTS (default {DEFAULT_LOT_CONDITION_ID} = Used -- "
                             "confirmed 2026-09-28 against 3 real active category-183455 listings via GetItem, "
                             "after 4000 -- the singles value -- was confirmed rejected here by a real upload)")
    parser.add_argument("--location", default=PLACEHOLDER)
    parser.add_argument("--dispatch-days", default=PLACEHOLDER)
    parser.add_argument("--payment-policy", default=PLACEHOLDER)
    parser.add_argument("--return-policy", default=PLACEHOLDER)
    parser.add_argument("--variations", action="store_true",
                        help="group --in rows by set_name into one variation listing per set (see "
                             "export_ebay_bulk_listing.py --variations for the matching export). Unverified "
                             "against a real upload -- test with one small set's output before uploading every set.")
    parser.add_argument("--only-set", default=None,
                        help="with --variations, convert only this one set_name -- for a small first test upload")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    placeholders = {
        k: v for k, v in vars(args).items()
        if k not in ("template", "in_path", "out", "dry_run") and v == PLACEHOLDER
    }
    if placeholders:
        print(f"NOTE: still using placeholders for: {', '.join(placeholders)} -- "
              "these need a real value before uploading.\n", file=sys.stderr)

    info_row, header = read_ebay_header(args.template)
    unmapped_required = [h for h in header if h.startswith("*") and h not in (
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)",
        "*Category", "*Title", "*ConditionID", "*C:Type", "*C:Franchise", "*C:Manufacturer",
        "*Description", "*Format", "*Duration", "*StartPrice", "*Quantity", "*Location",
        "*DispatchTimeMax", "*ReturnsAcceptedOption", "*C:Set",
    )]
    if unmapped_required:
        print(f"NOTE: this template also requires {unmapped_required}, which this script doesn't "
              "fill in -- add them by hand in the CSV or in Seller Hub's review step before uploading.\n",
              file=sys.stderr)

    with open(args.in_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.variations:
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row["set_name"], []).append(row)
        if args.only_set:
            groups = {k: v for k, v in groups.items() if k == args.only_set}
        out_rows = []
        variation_skipped: list[str] = []
        for set_name, cards in groups.items():
            group_rows, group_skipped = build_variation_group(set_name, cards, args)
            out_rows.extend(group_rows)
            variation_skipped.extend(group_skipped)
        print(f"grouped {len(rows)} card(s) into {len(groups)} set variation listing(s)", file=sys.stderr)
        if variation_skipped:
            # Not all of these are skipped cards -- a photo-pool-over-limit
            # entry (see build_variation_group) is a listing-level warning,
            # every card in it is still exported. Same list, same printout,
            # since both are just "read this before you upload" notes.
            print(f"{len(variation_skipped)} note(s) -- see reason per row:", file=sys.stderr)
            for reason in variation_skipped:
                print(f"  {reason}", file=sys.stderr)
    else:
        out_rows = [convert_row(row, args) for row in rows]

    if args.dry_run:
        print(f"would convert {len(out_rows)} row(s) against a {len(header)}-column template")
        return 0

    # utf-8-sig, because the template carries a BOM and the data carries
    # "Pokemon" with an accent -- without it eBay is left guessing at the
    # encoding the Action column's own CC=UTF-8 already declares.
    # A row can carry an optional item specific -- C:Rarity below is one --
    # that this particular downloaded template doesn't have a column for.
    # eBay's per-category template varies which item specifics it exposes,
    # so DictWriter's strict "no extra keys" check would fail the whole
    # export over one non-critical field a different category's template
    # might well have. Dropped, not failed: the row still ships with every
    # field the template actually supports.
    header_set = set(header)
    out_rows = [{key: value for key, value in row.items() if key in header_set} for row in out_rows]

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        f.write(info_row + "\r\n")
        writer = csv.DictWriter(f, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"wrote {len(out_rows)} row(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
