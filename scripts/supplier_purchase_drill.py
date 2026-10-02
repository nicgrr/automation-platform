"""Rehearse supplier catalogue, purchase-lot and inventory receiving.

    .venv/bin/python -m scripts.supplier_purchase_drill

Uses a temporary database and makes no external calls.
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
    CatalogItem, CatalogItemType, InventoryItem, PurchaseLot, PurchaseLotItem,
    PurchaseLotStatus, Supplier, SupplierProduct,
)


def run() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="ezbay-supplier-drill.") as directory:
        engine = make_engine(f"sqlite+pysqlite:///{Path(directory) / 'drill.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            catalogs = [
                CatalogItem(id="supplier-drill-one", name="Supplier drill one", item_type=CatalogItemType.SEALED_PRODUCT),
                CatalogItem(id="supplier-drill-two", name="Supplier drill two", item_type=CatalogItemType.SEALED_PRODUCT),
            ]
            session.add_all(catalogs)
            session.commit()
            app.dependency_overrides[get_session] = lambda: session
            app.dependency_overrides[require_dashboard_user] = lambda: "supplier-drill"
            try:
                client = TestClient(app)
                assert client.post("/suppliers", data={
                    "name": "Temporary Drill Supplier", "contact": "", "website": "",
                    "categories": "TCG", "account_status": "active",
                    "wholesale_discount_pct": "",
                }, follow_redirects=False).status_code == 303
                supplier = session.scalars(select(Supplier)).one()
                for catalog, cost in zip(catalogs, ("10", "20")):
                    response = client.post(f"/suppliers/{supplier.id}/products", data={
                        "catalog_item_id": catalog.id, "cost": cost, "min_order_qty": "1",
                    }, follow_redirects=False)
                    assert response.status_code == 303
                products = session.scalars(select(SupplierProduct).order_by(SupplierProduct.cost)).all()

                create = client.post("/purchase-lots", data={
                    "supplier_id": supplier.id, "source": "Temporary wholesale order",
                    "seller": "", "asking_price": "100", "target_buy_pct": "55",
                }, follow_redirects=False)
                lot_url = create.headers["location"]
                for product, market, quantity in zip(products, ("30", "40"), ("2", "1")):
                    response = client.post(f"{lot_url}/items", data={
                        "supplier_product_id": product.id, "market_value": market,
                        "quantity": quantity, "condition": "Sealed",
                    }, follow_redirects=False)
                    assert response.status_code == 303
                receipt = client.post(f"{lot_url}/status", data={
                    "status": "purchased", "offered_price": "90",
                }, follow_redirects=False)
                repeated = client.post(f"{lot_url}/status", data={
                    "status": "purchased", "offered_price": "90",
                }, follow_redirects=False)
                assert receipt.status_code == 303 and repeated.status_code == 409

                lot = session.get(PurchaseLot, lot_url.rsplit("/", 1)[-1])
                lines = session.scalars(
                    select(PurchaseLotItem).where(PurchaseLotItem.purchase_lot_id == lot.id)
                ).all()
                inventory = session.scalars(select(InventoryItem).where(
                    InventoryItem.catalog_item_id.in_([catalog.id for catalog in catalogs])
                )).all()
                assert lot.status == PurchaseLotStatus.PURCHASED
                assert lot.inventory_received_at is not None
                assert sum((line.allocated_total_cost for line in lines), Decimal("0")) == Decimal("90")
                assert len(inventory) == 2 and sum(item.quantity for item in inventory) == 3
                assert all(line.inventory_item_id for line in lines)
                return {
                    "status": "passed",
                    "supplier_catalogue_link": "passed",
                    "purchase_lot_link": "passed",
                    "proportional_cost_allocation": "passed",
                    "canonical_inventory_receipt": "passed",
                    "duplicate_receipt_guard": "passed",
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
