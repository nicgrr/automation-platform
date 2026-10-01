"""One-off: withdraw the older, redundant ShopListing wherever the same
physical InventoryItem currently backs more than one ACTIVE listing.

Real incident, confirmed 2026-09-28: 3 cards (Tapu Lele #92, Professor's
Research #88, Cloyster #91) each had two active listings against the
same inventory -- either a literal accidental duplicate (Tapu Lele: same
price, created milliseconds apart) or a newer, currently-priced listing
that already covers the full remaining inventory count on its own
(Professor's Research / Cloyster: the newer listing's own quantity
already equals total stock, making the older one pure redundant
overselling risk -- two listings independently offering the same
physical card is exactly the same overselling shape whichever way it
happened). The newer listing (by created_at) is kept in every case,
since it carries this app's current pricing convention; the older one is
withdrawn, not deleted, so it can be reviewed and reactivated by hand if
that read turns out to be wrong for a specific card.

    .venv/bin/python -m scripts.withhold_duplicate_active_listings --dry-run
    .venv/bin/python -m scripts.withhold_duplicate_active_listings
"""

import argparse
import uuid
from collections import defaultdict

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import ShopListing, ShopListingStatus

REASON = ("Withdrawn: this same physical card was also active as a newer listing -- both together "
          "offered more copies for sale than actually exist. See scripts/withhold_duplicate_active_listings.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        active = session.scalars(
            select(ShopListing).where(
                ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.inventory_item_id.isnot(None)
            )
        ).all()
        by_inv: dict[str, list[ShopListing]] = defaultdict(list)
        for listing in active:
            by_inv[listing.inventory_item_id].append(listing)

        withdrawn = 0
        for inv_id, listings in by_inv.items():
            if len(listings) < 2:
                continue
            listings.sort(key=lambda l: l.created_at)
            keep, *rest = listings[::-1]  # newest first: keep it, withdraw the rest
            for listing in rest:
                withdrawn += 1
                print(f"  {'would withdraw' if args.dry_run else 'withdrawing'}  {listing.title}  "
                     f"${listing.price}  qty={listing.quantity}  ({listing.id})  "
                     f"-- keeping {keep.id} (${keep.price}, qty={keep.quantity})")
                if not args.dry_run:
                    listing.status = ShopListingStatus.WITHDRAWN
                    record_event(
                        session, actor_type="user", actor_id=args.user, action="shop_listing.withdraw",
                        resource_type="shop_listing", resource_id=listing.id, outcome="success",
                        correlation_id=str(uuid.uuid4()),
                        details={"reason": REASON, "duplicate_of": keep.id, "price": str(listing.price)},
                    )

        print(f"\n{withdrawn} duplicate listing(s) {'would be' if args.dry_run else ''} withdrawn".replace("  ", " "))
        if args.dry_run:
            print("--dry-run: nothing written.")
            return 0

        session.commit()
        print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
