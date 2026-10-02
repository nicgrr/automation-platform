"""Rehearse an inventory-backed direct sale against a temporary database.

    .venv/bin/python -m scripts.direct_sale_drill
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session, make_engine
from automation_control.models import (
    CatalogItem, CatalogItemType, Customer, InventoryItem, InventoryStatus,
    Marketplace, Sale, SaleItem, SaleStatus,
)


def run() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="ezbay-direct-sale-drill.") as directory:
        engine = make_engine(f"sqlite+pysqlite:///{Path(directory) / 'drill.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            marketplace = Marketplace(id="direct-drill", name="Direct")
            catalog = CatalogItem(
                id="direct-drill-item", name="Direct sale drill item",
                item_type=CatalogItemType.TCG_CARD,
            )
            inventory = InventoryItem(
                id="direct-drill-stock", catalog_item_id=catalog.id, quantity=1,
                condition="Near Mint", status=InventoryStatus.AVAILABLE,
                allocated_cost_basis=Decimal("40"),
            )
            session.add_all([marketplace, catalog, inventory])
            session.commit()
            app.dependency_overrides[get_session] = lambda: session
            app.dependency_overrides[require_dashboard_user] = lambda: "direct-sale-drill"
            try:
                client = TestClient(app)
                data = {
                    "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
                    "customer_name": "Drill Buyer", "customer_email": "buyer@example.test",
                    "quantity": "1", "unit_price": "100", "fees_amount": "10",
                    "shipping_cost": "5", "packaging_cost": "2",
                    "submission_key": "8798fb9f-c252-4495-8262-59e1538eb506",
                }
                first = client.post("/sales", data=data, follow_redirects=False)
                retry = client.post("/sales", data=data, follow_redirects=False)
                assert first.status_code == retry.status_code == 303
                session.refresh(inventory)
                sale = session.scalars(select(Sale)).one()
                item = session.scalars(select(SaleItem)).one()
                customer = session.scalars(select(Customer)).one()
                assert inventory.quantity == 0 and inventory.status == InventoryStatus.SOLD
                assert item.inventory_item_id == inventory.id and item.cost_basis == Decimal("40")
                assert customer.email == "buyer@example.test"
                assert session.query(Sale).count() == 1

                voided = client.post(f"/sales/{sale.id}/void", follow_redirects=False)
                repeated_void = client.post(f"/sales/{sale.id}/void", follow_redirects=False)
                assert voided.status_code == 303 and repeated_void.status_code == 409
                session.refresh(inventory)
                session.refresh(sale)
                assert inventory.quantity == 1 and inventory.status == InventoryStatus.AVAILABLE
                assert sale.status == SaleStatus.VOIDED
                return {
                    "status": "passed",
                    "atomic_stock_decrement": "passed",
                    "customer_handoff": "passed",
                    "cost_snapshot": "passed",
                    "duplicate_submission_guard": "passed",
                    "one_time_void_restore": "passed",
                    "external_calls": "none",
                }
            finally:
                app.dependency_overrides.clear()
        engine.dispose()


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
