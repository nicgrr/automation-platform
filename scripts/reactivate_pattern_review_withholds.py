"""One-off: put back every listing withheld by the "151"/Prismatic
Evolutions pattern-review scripts, at the user's explicit request.

Companion to scripts/withhold_unphotographed_151_for_pattern_review.py and
scripts/withhold_unphotographed_prismatic_evolutions_for_pattern_review.py.
Those scripts pulled 46 listings out of ACTIVE because neither this
catalogue nor a physical scan could confirm whether any of them hides a
Poke Ball/Master Ball pattern print. The user asked for them back so they
can pick which ones to export themselves -- this does not resolve that
gap, it just hands the decision back.

Scoped by audit trail, not by re-deriving the same set/price query: only
a listing with a `listing.withhold_for_review` event gets touched, so a
listing withdrawn for an unrelated reason (by hand, or by some other
script) is never swept up by this running again.

    .venv/bin/python -m scripts.reactivate_pattern_review_withholds --dry-run
    .venv/bin/python -m scripts.reactivate_pattern_review_withholds
"""

import argparse
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import AuditEvent, ShopListing, ShopListingStatus

REASON = ("Reactivated at the user's request -- the underlying gap (no photo, no inventory "
          "linked to the real Japanese set) is not resolved, this only returns the listing to "
          "the exportable pool. See scripts/reactivate_pattern_review_withholds.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        withheld_ids = session.scalars(
            select(AuditEvent.resource_id).where(AuditEvent.action == "listing.withhold_for_review").distinct()
        ).all()
        listings = [
            listing for listing in session.scalars(
                select(ShopListing).where(ShopListing.id.in_(withheld_ids), ShopListing.status == ShopListingStatus.WITHDRAWN)
            ).all()
        ]

        print(f"{len(listings)} withheld listing(s) found, still withdrawn")
        for listing in listings:
            print(f"  {'would reactivate' if args.dry_run else 'reactivating'}  ${listing.price}  {listing.title}  ({listing.id})")
            if not args.dry_run:
                listing.status = ShopListingStatus.ACTIVE
                record_event(
                    session, actor_type="user", actor_id=args.user, action="shop_listing.activate",
                    resource_type="shop_listing", resource_id=listing.id, outcome="success",
                    correlation_id=str(uuid.uuid4()), details={"reason": REASON, "price": str(listing.price)},
                )

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        session.commit()
        print(f"\ndone -- {len(listings)} listing(s) reactivated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
