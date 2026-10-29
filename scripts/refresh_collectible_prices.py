#!/usr/bin/env python
"""Refresh due non-TCG collectible prices using configured provider rules.

Safe for a systemd timer: provider failures are isolated per item, cached eBay
observations suppress calls until the rule's refresh interval expires, and the
category guard makes TCG rows ineligible before any adapter is invoked.
"""

import argparse
import asyncio

from sqlalchemy import select

from automation_control.collectible_pricing_service import (
    CollectiblePricingError, calculate_valuation, observations, refresh_ebay,
    require_collectible, save_valuation,
)
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, InventoryItem


async def run(*, limit: int | None = None, dry_run: bool = False) -> int:
    settings = get_settings()
    refreshed = cached = failed = 0
    with SessionLocal() as session:
        stmt = (
            select(CatalogItem.id).join(InventoryItem, InventoryItem.catalog_item_id == CatalogItem.id)
            .where(CatalogItem.item_type == CatalogItemType.COLLECTIBLE, InventoryItem.quantity > 0)
            .distinct().order_by(CatalogItem.id)
        )
        if limit:
            stmt = stmt.limit(limit)
        for item_id in session.scalars(stmt):
            try:
                count = await refresh_ebay(session, settings, item_id, force=False)
                if count:
                    value = calculate_valuation(observations(session, item_id))
                    save_valuation(session, require_collectible(session, item_id), value)
                    refreshed += 1
                else:
                    cached += 1
            except Exception as exc:
                session.rollback()
                failed += 1
                print(f"warning: {item_id}: {type(exc).__name__}: {exc}")
            else:
                session.flush()
        if dry_run:
            session.rollback()
        else:
            session.commit()
    print(f"refreshed={refreshed} cached={cached} failed={failed} dry_run={dry_run}")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
