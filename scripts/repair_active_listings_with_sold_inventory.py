"""One-off repair: a ShopListing left ACTIVE whose own InventoryItem is
already SOLD (found by scripts/audit_inventory_vs_listings.py's second
check, 2026-09-29, right after fixing scripts/repair_inventory_after_ebay_sales.py's
four findings). For at least one of these (Ivysaur #2) the audit trail shows
it was actually re-activated by hand on 2026-09-27, days after the real
eBay sale, because nothing about the sale had touched the listing at all --
whatever applied that order never went through decrement_listing_quantity's
listing-side branch either, so there was nothing to signal it had sold.

This closes the listing side to match: any ACTIVE ShopListing whose linked
InventoryItem is SOLD gets marked SOLD_OUT and its quantity zeroed, so it
stops being exportable/orderable for a card that's already gone.

    .venv/bin/python -m scripts.repair_active_listings_with_sold_inventory --dry-run
    .venv/bin/python -m scripts.repair_active_listings_with_sold_inventory
"""

import argparse
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import InventoryItem, InventoryStatus, ShopListing, ShopListingStatus

REASON = ("Backfilled: this listing's own InventoryItem was already SOLD but the listing "
          "itself was still ACTIVE -- found by scripts/audit_inventory_vs_listings.py, "
          "2026-09-29. See scripts/repair_active_listings_with_sold_inventory.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        active = session.scalars(
            select(ShopListing).where(ShopListing.status == ShopListingStatus.ACTIVE,
                                      ShopListing.inventory_item_id.isnot(None))
        ).all()

        fixed = 0
        for listing in active:
            item = session.get(InventoryItem, listing.inventory_item_id)
            if item is None or item.status == InventoryStatus.AVAILABLE:
                continue

            before = {"status": listing.status.value, "quantity": listing.quantity}
            print(f"  {'would fix' if args.dry_run else 'fixing'}  {listing.title}  ({listing.id})  "
                 f"inventory is {item.status.value}, listing quantity {listing.quantity} -> 0")
            if args.dry_run:
                fixed += 1
                continue

            listing.quantity = 0
            listing.status = ShopListingStatus.SOLD_OUT
            record_event(
                session, actor_type="user", actor_id=args.user, action="listing.repair_sold_inventory_mismatch",
                resource_type="shop_listing", resource_id=listing.id, outcome="success",
                correlation_id=str(uuid.uuid4()),
                details={"reason": REASON, "before": before,
                        "after": {"status": listing.status.value, "quantity": listing.quantity},
                        "inventory_item_id": item.id, "inventory_status": item.status.value},
            )
            fixed += 1

        print(f"\n{fixed} listing(s) {'would be' if args.dry_run else ''} fixed".replace("  ", " "))
        if args.dry_run:
            print("--dry-run: nothing written.")
            return 0

        session.commit()
        print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
