"""Consolidate every active single-card listing into one spreadsheet,
grouped and sorted by set, for a human review pass before anything goes
back up on eBay.

Deliberately not an eBay-format file -- this is for you to read, not to
upload. Columns are plain and sortable: Set, Number, Card Name, Price,
Condition, Rarity, SKU. Flag anything that looks wrong (a card in the
wrong set, a name that doesn't match its number, a price that looks off)
and send it back; the SKU column is what ties a row back to the exact
ShopListing to fix.

    .venv/bin/python -m scripts.export_cards_by_set_review
    .venv/bin/python -m scripts.export_cards_by_set_review --out cards_review.csv
"""

import argparse
import csv

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, ShopListing, ShopListingStatus, TcgCard
from scripts.export_ebay_bulk_listing import single_rarity, single_set_name, single_set_total

FIELDNAMES = ["set_name", "set_total", "number", "card_name", "title", "price", "condition", "rarity", "sku"]


def _sort_key(row: dict) -> tuple:
    try:
        number = int(row["number"])
    except ValueError:
        number = 0
    return (row["set_name"], number)


def build_rows(session, status: ShopListingStatus) -> list[dict]:
    listings = session.scalars(select(ShopListing).where(ShopListing.status == status)).all()

    rows = []
    for listing in listings:
        item = session.get(CatalogItem, listing.catalog_item_id)
        tcg = session.get(TcgCard, listing.catalog_item_id)
        if tcg is None:
            continue  # not a TCG card (e.g. a collectible) -- nothing to group by set
        name, _, number = listing.title.partition(" #")
        rows.append({
            "set_name": single_set_name(session, item),
            "set_total": single_set_total(session, item),
            "number": number.strip(),
            "card_name": name.strip(),
            "title": listing.title,
            "price": f"{listing.price:.2f}",
            "condition": listing.condition or "",
            "rarity": single_rarity(session, item),
            "sku": listing.id,
        })
    rows.sort(key=_sort_key)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", default="active", choices=[s.value for s in ShopListingStatus])
    parser.add_argument("--out", default="cards_by_set_review.csv")
    args = parser.parse_args()

    with SessionLocal() as session:
        rows = build_rows(session, ShopListingStatus(args.status))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    sets = {r["set_name"] for r in rows}
    print(f"wrote {len(rows)} card(s) across {len(sets)} set(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
