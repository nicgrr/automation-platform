"""Recurring reconciliation audit: inventory vs. what's actually listed.

Formalizes checks that were run by hand on 2026-09-28/29 after a real
overselling bug was found by accident (a sold card never got marked sold
in inventory -- see automation_control/stock.py's decrement_listing_quantity,
fixed the same session). Meant to be run regularly (see
scripts/run-inventory-listings-audit.sh / the systemd timer, or a
scheduled Claude session that runs this and applies judgement to the
output) so the next version of that class of bug is caught in a report
instead of by accident again.

Four checks, each independently real incidents this session:
  1. Sold on eBay but inventory still marked available (overselling risk)
  2. A listing is ACTIVE but its inventory isn't (stale listing)
  3. The same physical inventory item backs more than one ACTIVE listing
     (duplicate-listing overselling risk)
  4. A "Complete Your Master Set" listing mixes languages among its real
     children (the original Dragon Ball Fusion World incident's shape)

Read-only. Never writes anything -- this is a report, not a fixer; see
scripts/repair_inventory_after_ebay_sales.py and
scripts/withhold_duplicate_active_listings.py for the one-off repairs
this same investigation already produced.

Exit code is 0 when every check is clean, 1 when anything was found --
so a scheduled caller (cron, or a scheduled Claude session) can tell at
a glance whether the printed detail is worth reading.

    .venv/bin/python -m scripts.audit_inventory_vs_listings
"""

import sys
from collections import defaultdict

from sqlalchemy import func, select

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.discord_notify import send_discord_message
from automation_control.ebay_bulk_export import _latest_export_items, _variation_group_children
from automation_control.vinted_fallback import shares_reserved_stock
from automation_control.models import (
    CatalogItem,
    AuditEvent,
    EbayExportItemStatus,
    EbayImportedOrder,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)


STOCK_CONFIRMED_ACTIONS = ("listing.cancelled_order_stock_restored", "listing.remaining_stock_confirmed")


def check_sold_but_available(session) -> list[str]:
    rows = session.execute(
        select(EbayImportedOrder, ShopListing)
        .join(ShopListing, ShopListing.id == EbayImportedOrder.shop_listing_id)
        .where(EbayImportedOrder.applied_at.isnot(None))
    ).all()
    findings = []
    for order_row, listing in rows:
        # A cancelled eBay order may legitimately be followed by the owner
        # restoring and relisting the same stock.  Keep the imported order as
        # immutable history, but do not report it as an overselling risk once
        # that explicit restoration has been audited after the sale was
        # applied. The same goes for the owner confirming that a copy really
        # remains after a partial sale: AVAILABLE stock is correct then.
        restored_at = session.scalar(
            select(func.max(AuditEvent.occurred_at)).where(
                AuditEvent.action.in_(STOCK_CONFIRMED_ACTIONS),
                AuditEvent.resource_type == "shop_listing",
                AuditEvent.resource_id == listing.id,
                AuditEvent.outcome == "success",
            )
        )
        if restored_at is not None and restored_at >= order_row.applied_at:
            continue
        if not listing.inventory_item_id:
            continue
        inv = session.get(InventoryItem, listing.inventory_item_id)
        if inv and inv.status == InventoryStatus.AVAILABLE:
            findings.append(f"{listing.title}  sold in order {order_row.ebay_order_id}  "
                           f"but inventory {inv.id} is still AVAILABLE")
    return findings


def check_active_listing_gone_inventory(session) -> list[str]:
    active = session.scalars(
        select(ShopListing).where(ShopListing.status == ShopListingStatus.ACTIVE,
                                  ShopListing.inventory_item_id.isnot(None))
    ).all()
    findings = []
    for listing in active:
        inv = session.get(InventoryItem, listing.inventory_item_id)
        if inv is None:
            findings.append(f"{listing.title}  ({listing.id})  -- inventory_item_id points to a deleted row")
        elif inv.status == InventoryStatus.RESERVED and shares_reserved_stock(session, listing):
            continue  # one figurine, live on both Vinted and eBay by the owner's choice
        elif inv.status not in (InventoryStatus.AVAILABLE, InventoryStatus.LISTED):
            findings.append(f"{listing.title}  ({listing.id})  -- ACTIVE but inventory is {inv.status.value}")
    return findings


def check_duplicate_active_listings(session) -> list[str]:
    active = session.scalars(
        select(ShopListing).where(ShopListing.status == ShopListingStatus.ACTIVE,
                                  ShopListing.inventory_item_id.isnot(None))
    ).all()
    by_inv = defaultdict(list)
    for listing in active:
        by_inv[listing.inventory_item_id].append(listing)
    findings = []
    for inv_id, listings in by_inv.items():
        if len(listings) > 1:
            titles = ", ".join(f"{l.title} (${l.price}, {l.id})" for l in listings)
            findings.append(f"inventory_item {inv_id}: {len(listings)} active listings -- {titles}")
    return findings


def check_language_mixed_variation_listings(session) -> list[str]:
    latest = _latest_export_items(session)
    parents = [
        item for item in latest.values()
        if item.kind == "variation_parent" and item.status == EbayExportItemStatus.CONFIRMED and item.ebay_listing_id
    ]
    findings = []
    for parent in parents:
        children = _variation_group_children(session, parent)
        langs = set()
        for child in children:
            listing = session.get(ShopListing, child.sku)
            if not listing:
                continue
            tcg = session.execute(
                select(TcgCard).join(CatalogItem, CatalogItem.id == TcgCard.catalog_item_id)
                .where(CatalogItem.id == listing.catalog_item_id)
            ).scalar()
            if tcg:
                langs.add(tcg.language or "English")
        if len(langs) > 1:
            findings.append(f"{parent.title}  (eBay #{parent.ebay_listing_id})  mixes languages: {sorted(langs)}")
    return findings


CHECKS = [
    ("Sold but still marked available", check_sold_but_available),
    ("Active listing, inventory gone/sold elsewhere", check_active_listing_gone_inventory),
    ("Same inventory item behind multiple active listings", check_duplicate_active_listings),
    ("Language-mixed variation listings", check_language_mixed_variation_listings),
]


def main() -> int:
    total_findings = 0
    report_lines = []
    with SessionLocal() as session:
        for label, check in CHECKS:
            findings = check(session)
            total_findings += len(findings)
            print(f"=== {label}: {len(findings)} finding(s) ===")
            if findings:
                report_lines.append(f"**{label}** ({len(findings)}):")
            for line in findings:
                print(f"  {line}")
                report_lines.append(f"- {line}")
            print()

    if total_findings:
        print(f"{total_findings} total finding(s) across {len(CHECKS)} checks -- see detail above.")
        summary = (f"⚠️ Inventory/listings audit: {total_findings} finding(s)\n" + "\n".join(report_lines))
        send_discord_message(summary, get_settings().discord_webhook_url)
        return 1
    print("All checks clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
