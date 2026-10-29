"""One-off repair: catch up every InventoryItem left AVAILABLE by a real
past eBay sale, from before decrement_listing_quantity actually touched
inventory (see automation_control/stock.py, fixed 2026-09-28).

That bug meant every sale through checkout.py or import_ebay_orders.py
correctly sold out the ShopListing but never touched the physical
InventoryItem behind it -- so the card stayed marked AVAILABLE forever,
with nothing to say it was actually gone. This applies the same
quantity-decrement math retroactively, once, for every EbayImportedOrder
line that's genuinely applied (applied_at set, a real ShopListing
resolved) but whose linked InventoryItem never moved.

    .venv/bin/python -m scripts.repair_inventory_after_ebay_sales --dry-run
    .venv/bin/python -m scripts.repair_inventory_after_ebay_sales
"""

import argparse
import uuid

from sqlalchemy import func, select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import AuditEvent, EbayImportedOrder, InventoryItem, InventoryStatus, ShopListing

REASON = ("Backfilled: this card sold via eBay before decrement_listing_quantity actually touched "
          "inventory (fixed 2026-09-28) -- see scripts/repair_inventory_after_ebay_sales.py.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        sold_rows = session.execute(
            select(EbayImportedOrder, ShopListing)
            .join(ShopListing, ShopListing.id == EbayImportedOrder.shop_listing_id)
            .where(EbayImportedOrder.applied_at.isnot(None))
        ).all()

        fixed = 0
        for order_row, listing in sold_rows:
            restored_at = session.scalar(
                select(func.max(AuditEvent.occurred_at)).where(
                    AuditEvent.action == "listing.cancelled_order_stock_restored",
                    AuditEvent.resource_type == "shop_listing",
                    AuditEvent.resource_id == listing.id,
                    AuditEvent.outcome == "success",
                )
            )
            if restored_at is not None and restored_at >= order_row.applied_at:
                continue
            if not listing.inventory_item_id:
                continue
            item = session.get(InventoryItem, listing.inventory_item_id)
            if item is None or item.status != InventoryStatus.AVAILABLE:
                continue

            before = {"quantity": item.quantity, "status": item.status.value}
            new_quantity = max(0, item.quantity - order_row.quantity)
            print(f"  {'would fix' if args.dry_run else 'fixing'}  {listing.title}  "
                 f"order={order_row.ebay_order_id}  qty {item.quantity} -> {new_quantity}")
            if args.dry_run:
                fixed += 1
                continue

            item.quantity = new_quantity
            if item.quantity == 0:
                item.status = InventoryStatus.SOLD
            record_event(
                session, actor_type="user", actor_id=args.user, action="inventory.repair_after_ebay_sale",
                resource_type="inventory_item", resource_id=item.id, outcome="success",
                correlation_id=str(uuid.uuid4()),
                details={"reason": REASON, "before": before,
                        "after": {"quantity": item.quantity, "status": item.status.value},
                        "ebay_order_id": order_row.ebay_order_id},
            )
            fixed += 1

        print(f"\n{fixed} inventory item(s) {'would be' if args.dry_run else ''} fixed".replace("  ", " "))
        if args.dry_run:
            print("--dry-run: nothing written.")
            return 0

        session.commit()
        print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
