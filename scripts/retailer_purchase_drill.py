"""Rehearse retailer purchase, inventory, lot and refund transactions.

    .venv/bin/python -m scripts.retailer_purchase_drill

The drill uses a temporary SQLite database and makes no external calls.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.database import Base, make_engine
from automation_control.models import (
    InventoryItem, InventoryStatus, PurchaseLot, PurchaseLotItem, PurchaseLotStatus,
    RetailerFundEntry, RetailerProduct, RetailerPurchaseStatus, ShopListing, ShopListingStatus,
)
from automation_control.retailer_goals_service import (
    ensure_gameology_program, record_purchase, refund_purchase,
)


def run() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="ezbay-retailer-drill.") as directory:
        engine = make_engine(f"sqlite+pysqlite:///{Path(directory) / 'drill.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            program = ensure_gameology_program(session)
            product = session.scalars(
                select(RetailerProduct).where(RetailerProduct.program_id == program.id)
            ).first()
            result = record_purchase(
                session, program=program, product=product, unit_price=Decimal("100"),
                quantity=2, purchase_date=date.today(), eligible_amount=Decimal("200"),
                shipping=Decimal("10"), order_number="DRILL-ORDER-1",
                notes="Temporary transaction rehearsal",
            )
            purchase = result["purchase"]
            inventory = session.get(InventoryItem, purchase.inventory_item_id)
            lot = session.get(PurchaseLot, purchase.purchase_lot_id)
            line = session.scalar(select(PurchaseLotItem).where(
                PurchaseLotItem.retailer_purchase_id == purchase.id
            ))
            assert inventory.quantity == 2
            assert inventory.allocated_cost_basis == Decimal("105.00")
            assert lot.status == PurchaseLotStatus.PURCHASED
            assert lot.purchase_price == Decimal("210.00")
            assert line.inventory_item_id == inventory.id
            assert session.scalar(select(RetailerFundEntry.id).where(
                RetailerFundEntry.purchase_id == purchase.id
            )) is not None

            listing = ShopListing(
                catalog_item_id=inventory.catalog_item_id, inventory_item_id=inventory.id,
                title="Temporary drill listing", price=Decimal("125"), quantity=1,
                status=ShopListingStatus.ACTIVE,
            )
            session.add(listing)
            session.commit()
            try:
                refund_purchase(session, purchase)
            except ValueError as exc:
                assert "Withdraw" in str(exc)
                session.rollback()
            else:
                raise AssertionError("allocated inventory refund was not blocked")
            session.refresh(purchase)
            assert purchase.status == RetailerPurchaseStatus.RECORDED

            listing = session.get(ShopListing, listing.id)
            listing.status = ShopListingStatus.WITHDRAWN
            session.commit()
            refund_purchase(session, purchase)
            session.refresh(inventory)
            session.refresh(lot)
            assert inventory.quantity == 0 and inventory.status == InventoryStatus.RETURNED
            assert lot.status == PurchaseLotStatus.REFUNDED

            repurchase = record_purchase(
                session, program=program, product=product, unit_price=Decimal("90"),
                quantity=1, purchase_date=date.today(), eligible_amount=Decimal("90"),
                order_number="DRILL-ORDER-2",
            )["purchase"]
            session.refresh(inventory)
            assert repurchase.inventory_item_id == inventory.id
            assert inventory.quantity == 1 and inventory.status == InventoryStatus.AVAILABLE

            result = {
                "status": "passed",
                "purchase_lot_link": "passed",
                "canonical_inventory": "passed",
                "landed_cost_allocation": "passed",
                "fund_ledger_link": "passed",
                "allocated_stock_refund_guard": "passed",
                "refund_reversal": "passed",
                "repurchase_reactivation": "passed",
                "external_calls": "none",
            }
        engine.dispose()
        return result


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
