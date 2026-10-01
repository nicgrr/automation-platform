"""On-demand CRM status report -- Phase 3c of the 2026-09-29 improvement
plan: package the nightly reconciliation audit (scripts/audit_inventory_vs_listings.py)
plus review-queue backlog size, stale-price count, and background-job
health into one thing to invoke on demand instead of checking each by
hand. Backs the `/crm-health-check` Skill.

Read-only. Prints a plain report; exit code is 0 only when every section
is clean.

    .venv/bin/python -m scripts.crm_health_check
"""

import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.job_health import JOBS, status_for
from automation_control.models import CardPrice, CatalogItem, InventoryItem, InventoryStatus
from automation_control.nav_counts import queue_counts
from scripts.audit_inventory_vs_listings import CHECKS

STALE_PRICE_DAYS = 30


def stale_priced_available_items(session, *, days: int = STALE_PRICE_DAYS) -> int:
    """AVAILABLE inventory whose catalog item has no price observation
    (of any source) newer than `days` -- includes items with no price at
    all."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    # CardPrice.catalog_item_id maps the historical physical `card_id`
    # column, whose foreign key and identifier space are CatalogItem. Using InventoryItem.card_id here
    # collapses every non-TCG collectible (where card_id is NULL) into one
    # fake item and misses its real price rows.
    available_item_ids = set(session.scalars(
        select(InventoryItem.catalog_item_id).where(
            InventoryItem.status == InventoryStatus.AVAILABLE,
            InventoryItem.catalog_item_id.isnot(None),
            InventoryItem.catalog_item_id.not_in(
                select(CatalogItem.id).where(CatalogItem.pricing_policy == "manual_only")
            ),
        )
    ))
    if not available_item_ids:
        return 0
    fresh_item_ids = set(session.scalars(
        select(CardPrice.catalog_item_id)
        .where(CardPrice.catalog_item_id.in_(available_item_ids), CardPrice.fetched_at >= cutoff)
        .distinct()
    ))
    return len(available_item_ids - fresh_item_ids)


def main() -> int:
    healthy = True

    print("=== Inventory / listings reconciliation ===")
    with SessionLocal() as session:
        total = 0
        for label, check in CHECKS:
            findings = check(session)
            total += len(findings)
            if findings:
                print(f"  {label}: {len(findings)} finding(s)")
        if total:
            print(f"  {total} total -- run `.venv/bin/python -m scripts.audit_inventory_vs_listings` for detail.")
            healthy = False
        else:
            print("  clean.")
    print()

    print("=== Review-queue backlog ===")
    with SessionLocal() as session:
        counts = queue_counts(session, get_settings())
        any_backlog = False
        for href, count in counts.items():
            if count:
                any_backlog = True
                healthy = False
                print(f"  {href}: {count} pending")
        if not any_backlog:
            print("  clean.")
    print()

    print(f"=== Stale prices (no observation in {STALE_PRICE_DAYS}+ days) ===")
    with SessionLocal() as session:
        stale = stale_priced_available_items(session)
        print(f"  {stale} available item(s) with a stale or missing price")
        if stale:
            healthy = False
    print()

    print("=== Background jobs ===")
    any_job_issue = False
    for job in JOBS:
        status = status_for(job)
        if status.needs_attention:
            any_job_issue = True
            healthy = False
            print(f"  {job.label}: {status.state} -- {status.detail or job.what}")
    if not any_job_issue:
        print("  all running on schedule.")
    print()

    print("=== Recovery evidence ===")
    from automation_control.recovery_health import recovery_statuses
    recovery_issue = False
    for status in recovery_statuses():
        print(f"  {status.label}: {status.detail}")
        if status.needs_attention:
            recovery_issue = True
            healthy = False
    if not recovery_issue:
        print("  backup and restore evidence are current.")
    print()

    print("All clear." if healthy else "Needs attention -- see sections above.")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
