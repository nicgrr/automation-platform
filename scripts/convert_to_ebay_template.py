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

_LOT_CARD_COUNT_RE = re.compile(r"\((\d+) cards\)")

PLACEHOLDER = "REPLACE_ME"

# The two header rows are \r-delimited; everything after is real data
# once opened correctly, so a plain csv.reader over the \r-split text
# works exactly like any other CSV from there.
INFO_ROW_PREFIX = "﻿Info,Version="


def read_ebay_header(template_path: str) -> list[str]:
    with open(template_path, "r", encoding="utf-8-sig", newline="") as f:
        raw = f.read()
    rows = raw.split("\r")
    if len(rows) < 2 or not rows[1].startswith("*Action("):
        raise SystemExit(
            f"{template_path} doesn't look like an eBay File Exchange template "
            "(expected an Info row then a header row starting with '*Action(...)')"
        )
    return next(csv.reader([rows[1]]))


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
# this inventory is).
DEFAULT_CONDITION_ID = "4000"


def convert_row(row: dict, args) -> dict:
    is_lot = "bulk lot" in row["title"].lower()
    name, _, number = row["title"].partition(" #")

    out = {
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)": "Add",
        "CustomLabel": row["sku"],
        "*Category": LOT_CATEGORY_ID if is_lot else SINGLE_CATEGORY_ID,
        "*Title": row["title"][:80],
        "*ConditionID": args.condition_id,
        "*C:Type": TYPE,
        "*C:Franchise": FRANCHISE,
        "*C:Manufacturer": MANUFACTURER,
        "PicURL": row["image_url"],
        "*Description": row["description"],
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
    else:
        out["C:Card Name"] = name.strip()
        if number:
            out["C:Card Number"] = number.strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", default="ebay-category-listing-template.csv",
                        help="the real template downloaded from Seller Hub")
    parser.add_argument("--in", dest="in_path", default="ebay_bulk_listing.csv",
                        help="our own export (see export_ebay_bulk_listing.py)")
    parser.add_argument("--out", default="ebay_bulk_listing_ready.csv")
    parser.add_argument("--condition-id", default=DEFAULT_CONDITION_ID,
                        help=f"eBay condition ID (default {DEFAULT_CONDITION_ID} = Ungraded -- override only if some "
                             "of this inventory is professionally graded)")
    parser.add_argument("--location", default=PLACEHOLDER)
    parser.add_argument("--dispatch-days", default=PLACEHOLDER)
    parser.add_argument("--payment-policy", default=PLACEHOLDER)
    parser.add_argument("--return-policy", default=PLACEHOLDER)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    placeholders = {
        k: v for k, v in vars(args).items()
        if k not in ("template", "in_path", "out", "dry_run") and v == PLACEHOLDER
    }
    if placeholders:
        print(f"NOTE: still using placeholders for: {', '.join(placeholders)} -- "
              "these need a real value before uploading.\n", file=sys.stderr)

    header = read_ebay_header(args.template)
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

    out_rows = [convert_row(row, args) for row in rows]

    if args.dry_run:
        print(f"would convert {len(out_rows)} row(s) against a {len(header)}-column template")
        return 0

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"wrote {len(out_rows)} row(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
