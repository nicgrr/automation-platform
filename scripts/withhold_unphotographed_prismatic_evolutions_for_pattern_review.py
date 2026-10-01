"""One-off: withdraw specific unphotographed Prismatic Evolutions listings
pending a manual check for a Poke Ball/Master Ball pattern print.

Companion to scripts/withhold_unphotographed_151_for_pattern_review.py, but
scoped to individual listing ids rather than a whole set: Prismatic
Evolutions applies the pattern mechanic across nearly the entire set (not
just one Japanese-origin subset like "151"), so most of its cards are
priced correctly either way -- these six are the specific rows flagged as
the highest-plausibility candidates (the "ex"/special-art print tier) that
also happen to have no photo on file to confirm or rule out a pattern
print. Two other copies of one of these cards (Vaporeon #22) DO have a
photo on file and were visually checked directly -- both show an ordinary
holo/reverse-holo print, not a ball pattern, so those two stay active and
are deliberately left out of this list.

Withdrawn, not deleted or re-priced -- same reasoning as the 151 script.

    .venv/bin/python -m scripts.withhold_unphotographed_prismatic_evolutions_for_pattern_review --dry-run
    .venv/bin/python -m scripts.withhold_unphotographed_prismatic_evolutions_for_pattern_review
"""

import argparse
import uuid

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import ShopListing, ShopListingStatus

LISTING_IDS = [
    "80fb1b3a-c21f-4e99-bc73-e4d8806a456b",  # Leafeon ex #6
    "b62f4638-cee7-4231-9936-0c46d503d2ba",  # Wellspring Mask Ogerpon ex #27
    "9a469e03-17f9-4dac-92f9-66223934173a",  # Vaporeon #22 (unphotographed copy)
    "6f1a09f9-f26d-400e-9006-3c89ceb5aa4e",  # Glaceon #25
    "dda1219f-c5c9-4b31-8178-6e103478f214",  # Glaceon #25 (second copy)
    "58ec4f9c-77ac-4ec5-a263-3cdbe674ae1b",  # Espeon #33
]
REASON = ("Withheld pending a physical check for a Poke Ball/Master Ball pattern print -- "
          "this card has no photo on file to rule that out. See "
          "scripts/withhold_unphotographed_prismatic_evolutions_for_pattern_review.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        listings = [session.get(ShopListing, lid) for lid in LISTING_IDS]
        found = [listing for listing in listings if listing is not None and listing.status == ShopListingStatus.ACTIVE]

        print(f"{len(found)} active, unphotographed listing(s) found")
        for listing in found:
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
        print(f"\ndone -- {len(found)} listing(s) withdrawn pending review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
