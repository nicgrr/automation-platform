import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardVariant, CatalogItem, CatalogItemType, Customer, InventoryItem,
    Sale, SaleItem, Supplier, SupplierStatus,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_export_page_requires_login():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/export")
    assert resp.status_code == 401


def test_export_page_links_every_csv(client):
    resp = client.get("/export")
    assert resp.status_code == 200
    for path in ["/export/inventory.csv", "/export/sales.csv", "/export/sale-items.csv", "/export/customers.csv", "/export/suppliers.csv"]:
        assert path in resp.text


def test_export_inventory_csv(client, session):
    session.add(CatalogItem(id="item1", item_type=CatalogItemType.COLLECTIBLE, name="Peach Sonny Angel"))
    session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="item1", variant=CardVariant.NORMAL, condition="New", quantity=3))
    session.commit()

    resp = client.get("/export/inventory.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "Peach Sonny Angel" in resp.text
    assert ",3," in resp.text or resp.text.strip().endswith("3")


def test_export_sales_and_sale_items_csv(client, session):
    session.add(Customer(id="cust1", display_name="Alex Buyer"))
    session.add(Sale(id="sale1", customer_id="cust1", gross_amount=60, fees_amount=6.6))
    session.add(SaleItem(id="si1", sale_id="sale1", description="Charizard ex", quantity=1, unit_price=60))
    session.commit()

    sales_csv = client.get("/export/sales.csv")
    assert sales_csv.status_code == 200
    assert "sale1" in sales_csv.text
    assert "cust1" in sales_csv.text

    items_csv = client.get("/export/sale-items.csv")
    assert items_csv.status_code == 200
    assert "Charizard ex" in items_csv.text


def test_export_customers_csv(client, session):
    session.add(Customer(id="cust1", display_name="Alex Buyer", segment="whale"))
    session.commit()
    resp = client.get("/export/customers.csv")
    assert "Alex Buyer" in resp.text
    assert "whale" in resp.text


def test_export_suppliers_csv(client, session):
    session.add(Supplier(id="sup1", name="Big Wholesale Co", account_status=SupplierStatus.ACTIVE, wholesale_discount_pct=15))
    session.commit()
    resp = client.get("/export/suppliers.csv")
    assert "Big Wholesale Co" in resp.text
    assert "active" in resp.text  # .value, not the enum member name -- matches what import_suppliers_csv.py expects back
