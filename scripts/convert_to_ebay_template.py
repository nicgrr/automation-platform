"""Convert our own ebay_bulk_listing.csv (see export_ebay_bulk_listing.py)
into eBay's real File Exchange bulk-upload format for the Trading Cards
category -- the one downloaded from Seller Hub -> Reports -> Uploads ->
Get template.

That file is a real oddity worth knowing before you touch it by hand:
it's \\r-delimited (old Mac-style line endings), not \\n, so `wc -l` and a
plain text editor both misread it as one giant line. Row 1 is an "Info"
marker, row 2 is the actual 133-column header (starting with the
"*Action(...)" column), row 3 onward is data.

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
# *rejected* for 183455 (Mixed Card Lots), so lots need a different,
# still-unverified value (see --lot-condition-id).
DEFAULT_CONDITION_ID = "4000"

# eBay's Condition Descriptor for ungraded CCG singles (ID 40001) is a
# required sub-field of ConditionID 4000, confirmed by a real failed
# upload ("Card Condition (40001) is a required field" on 990/1014 rows).
# Two independent community sources agree the numeric form is the
# reliable one to send, and both agree 400010 means "Near Mint or
# Better" -- which is what this whole export already assumes as the
# scanned condition, so one constant covers the actual data rather than
# a full text->ID mapping table for conditions nothing here currently has.
CARD_CONDITION_DESCRIPTOR_NEAR_MINT = "400010"


def convert_row(row: dict, args) -> dict:
    is_lot = "bulk lot" in row["title"].lower()
    title = sanitize_title(row["title"])
    name, _, number = title.partition(" #")

    out = {
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)": "Add",
        "CustomLabel": row["sku"],
        "*Category": LOT_CATEGORY_ID if is_lot else SINGLE_CATEGORY_ID,
        "*Title": title[:80],
        "*ConditionID": args.lot_condition_id if is_lot else args.condition_id,
        "*C:Type": TYPE,
        "*C:Franchise": FRANCHISE,
        "*C:Manufacturer": MANUFACTURER,
        "PicURL": row["image_url"],
        "*Description": sanitize_title(row["description"]),
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
    }
    if is_lot:
        match = _LOT_CARD_COUNT_RE.search(row["title"])
        if match:
            out["C:Number of Cards"] = match.group(1)
        # Not populated: 183455's own condition-descriptor scheme (if it
        # has one at all) is unverified, unlike the singles' 4000/40001
        # pair below -- confirmed only once already, no need to guess twice.
    else:
        out["C:Card Name"] = name.strip()
        if number:
            out["C:Card Number"] = number.strip()
        if row.get("rarity"):
            out["C:Rarity"] = sanitize_title(row["rarity"])
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

    The trait is "MPN" and the value is "001/162 Pineco" (padded number /
    set total, then name) -- matching a real, live, successful competitor
    listing found and inspected on 2026-09-14 ("Force Collectables"'
    Temporal Forces "Pick your card" listing), not our own first guess.
    That first guess used "Card #" as the trait name with a bare number
    as the value; "Card #" itself only avoided the *initial* failure
    ("Card Number is not allowed as a variation specific" -- Card Number
    is a required item specific in this category and can't double as the
    variation trait), but told a buyer nothing useful in the picker. MPN
    sidesteps the conflict entirely (it's a different item specific, not
    competing with the required Card Number one) and is the field real
    sellers in this category actually use.

    A second card sharing the same printed number is skipped, not
    included as a second variation with a colliding trait value: a real
    upload rejected 3 of 24 sets with "Duplicate VariationSpecifics trait
    value" for exactly this (a reprint/alt-art sharing its base card's
    number). eBay's variation model has no room for two options with the
    same MPN -- and even if it did, a buyer couldn't tell them apart from
    the label alone. The skipped card stays real inventory, just not sold
    through this listing; the returned skip reasons are what a caller
    reports so it isn't silently dropped.
    """
    VARIATION_TRAIT = "MPN"
    values: list[str] = []
    seen_numbers: set[str] = set()
    child_rows = []
    skipped = []
    for card in cards:
        child = convert_row(card, args)
        number = child.get("C:Card Number", "").strip()
        if not number:
            skipped.append(f"{card['title']} ({card['sku']}) -- no readable card number, can't be a variation")
            continue
        if number in seen_numbers:
            skipped.append(f"{card['title']} ({card['sku']}) -- another card in {set_name} already uses "
                           f"card number {number} (a reprint/alt-art sharing its base number); list this one separately")
            continue
        seen_numbers.add(number)
        set_total = (card.get("set_total") or "").strip()
        padded = number.zfill(len(set_total)) if set_total else number
        # "001/162 Pineco", the format a real successful listing uses --
        # bare "148" told a buyer nothing. ; and | are eBay's own
        # separators in this field, so real card text can't collide with them.
        value = f"{padded}/{set_total} {child.get('C:Card Name', '')}".strip() if set_total else \
            f"{padded} {child.get('C:Card Name', '')}".strip()
        value = value.replace(";", ",").replace("|", "-")
        values.append(value)
        child["Relationship"] = "Variation"
        child["RelationshipDetails"] = f"{VARIATION_TRAIT}={value}"
        child_rows.append(child)

    if not child_rows:
        return [], skipped

    parent = dict(child_rows[0])
    parent.update({
        "CustomLabel": _parent_sku(set_name),
        "*Title": sanitize_title(f"{set_name} - pick your card")[:80],
        "*Description": sanitize_title(
            f"Choose your card from {set_name}. Select a variation below to see its price and photo."
        ),
        "*Quantity": "",
        "*StartPrice": "",
        "PicURL": cards[0]["image_url"],
        "C:Card Name": "",
        "C:Card Number": "",
        "C:Rarity": "",
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
    parser.add_argument("--lot-condition-id", default=PLACEHOLDER,
                        help="eBay condition ID for BULK LOTS -- 4000 was confirmed *invalid* for category 183455 "
                             "by a real upload, and no verified replacement is known yet. List one lot by hand "
                             "through eBay's normal flow to find the real value before setting this.")
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
            print(f"skipped {len(variation_skipped)} card(s) -- see reason per row:", file=sys.stderr)
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
