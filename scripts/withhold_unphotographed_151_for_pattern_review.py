"""One-off: withdraw every active listing for an unphotographed "151"
(sv3pt5, English) holding, pending a manual check for a Japanese Poke
Ball/Master Ball pattern print hiding underneath it.

Why this set specifically, right now (2026-09-27): the Poke Ball/Master
Ball reverse-pattern mechanic on "151" originated in the JAPANESE release
(jp-sv2a, "ポケモンカード151") -- confirmed by web search, not the English
one. A check of this catalogue found ZERO inventory correctly linked to
jp-sv2a at all, which is the same shape as the Dragon Ball Fusion World
incident a day earlier: a physically Japanese card, scanned and
identify-matched against the closest visual reference, lands under the
wrong (English) set with no way for that reference to know a pattern
print even exists. Every one of the 38 unphotographed "151" holdings is
therefore unverifiable either way -- an ordinary English common and a
genuine Japanese Master Ball pull look identical to this app's own
records without a photo, and the Dragon Ball incident already showed
what shipping one of the wrong ones costs.

Withdrawn, not deleted or re-priced: nothing here changes what a card is
actually worth, it just keeps these specific rows out of every export
until someone looks at the physical card and either confirms it's an
ordinary print (flip back to active, nothing else to do) or files it
properly (a Japanese scan, a jp-sv2a link, a corrected price -- same
playbook as the Dragon Ball fix).

    .venv/bin/python -m scripts.withhold_unphotographed_151_for_pattern_review --dry-run
    .venv/bin/python -m scripts.withhold_unphotographed_151_for_pattern_review
"""

import argparse
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, InventoryItem, ShopListing, ShopListingStatus, TcgCard

ENGLISH_151_SET_ID = "sv3pt5"
REASON = ("Withheld pending a physical check for a Japanese Poke Ball/Master Ball pattern print -- "
          "no inventory is correctly linked to the real jp-sv2a set, and this card has no photo on "
          "file to rule that out. See scripts/withhold_unphotographed_151_for_pattern_review.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        rows = session.execute(
            select(ShopListing, InventoryItem)
            .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
            .join(InventoryItem, InventoryItem.id == ShopListing.inventory_item_id)
            .where(
                TcgCard.set_id == ENGLISH_151_SET_ID,
                ShopListing.status == ShopListingStatus.ACTIVE,
                InventoryItem.scan_image_path.is_(None),
            )
        ).all()

        print(f"{len(rows)} active, unphotographed '151' listing(s) found")
        for listing, inv in rows:
            print(f"  {'would withhold' if args.dry_run else 'withholding'}  ${listing.price}  {listing.title}  ({listing.id})")
            if not args.dry_run:
                listing.status = ShopListingStatus.WITHDRAWN
                record_event(
                    session, actor_type="user", actor_id=args.user, action="listing.withhold_for_review",
                    resource_type="shop_listing", resource_id=listing.id, outcome="success",
                    correlation_id=str(uuid.uuid4()), details={"reason": REASON, "price": str(listing.price)},
                )

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        session.commit()
        print(f"\ndone -- {len(rows)} listing(s) withdrawn pending review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
