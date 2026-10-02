from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.commerce import active_fee_rule
from automation_control.database import Base, get_session
from automation_control.models import (
    CatalogItem, CatalogItemType, Customer, InventoryItem, InventoryStatus, Marketplace,
    MarketplaceFeeRule, Sale, SaleItem, SaleStatus, ShopListing, ShopListingStatus,
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


def _direct_stock(session, *, quantity=2):
    marketplace = session.scalar(select(Marketplace).where(Marketplace.name == "Direct"))
    if marketplace is None:
        marketplace = Marketplace(id="direct", name="Direct")
        session.add(marketplace)
    catalog = CatalogItem(id="direct-item", name="Direct Sale Card", item_type=CatalogItemType.TCG_CARD)
    inventory = InventoryItem(
        id="direct-stock", catalog_item_id=catalog.id, quantity=quantity,
        status=InventoryStatus.AVAILABLE, condition="Near Mint",
        allocated_cost_basis=Decimal("40"),
    )
    session.add_all([catalog, inventory])
    session.commit()
    return marketplace, inventory


def test_category_specific_fee_rule_beats_a_catch_all(session):
    m = Marketplace(id="m1", name="Whatnot")
    session.add(m)
    session.add(MarketplaceFeeRule(id="r1", marketplace_id="m1", category=None, commission_pct=Decimal("10")))
    session.add(MarketplaceFeeRule(id="r2", marketplace_id="m1", category="TCG", commission_pct=Decimal("0"), promotion_label="0% TCG weekend"))
    session.commit()

    rule = active_fee_rule(session, "m1", category="TCG")
    assert rule.id == "r2"

    fallback = active_fee_rule(session, "m1", category="Figures")
    assert fallback.id == "r1"


def test_an_expired_promotion_is_not_active(session):
    m = Marketplace(id="m1", name="Whatnot")
    session.add(m)
    now = datetime.now(UTC)
    session.add(MarketplaceFeeRule(
        id="r1", marketplace_id="m1", category=None, commission_pct=Decimal("10"),
        effective_from=now - timedelta(days=10), effective_to=now - timedelta(days=1),
    ))
    session.commit()

    assert active_fee_rule(session, "m1") is None


def test_recording_a_direct_sale_claims_inventory_creates_customer_and_computes_profit(client, session):
    marketplace, inventory = _direct_stock(session)
    response = client.post(
        "/sales",
        data={
            "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
            "customer_name": "Alex", "customer_email": "ALEX@example.com",
            "customer_phone": "0400000000", "quantity": "1", "unit_price": "100",
            "fees_amount": "10", "shipping_revenue": "0", "shipping_cost": "5",
            "packaging_cost": "2", "tax_amount": "0", "submission_key": "sale-key-1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/sales")
    assert "Direct Sale Card" in page.text
    assert "$43.00" in page.text  # 100 - 40 - 10 - 5 - 2 = 43

    session.refresh(inventory)
    sale = session.scalars(select(Sale)).one()
    item = session.scalars(select(SaleItem)).one()
    customer = session.scalars(select(Customer)).one()
    assert inventory.quantity == 1
    assert item.inventory_item_id == inventory.id
    assert item.cost_basis == Decimal("40")
    assert sale.source == "direct"
    assert customer.email == "alex@example.com"

    customers = client.get("/customers")
    assert "Alex" in customers.text
    assert "alex@example.com" in customers.text
    assert "$100.00" in customers.text  # lifetime value


def test_retried_direct_sale_is_idempotent(client, session):
    marketplace, inventory = _direct_stock(session)
    data = {
        "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
        "customer_name": "Alex", "quantity": "1", "unit_price": "100",
        "submission_key": "sale-key-retry",
    }
    assert client.post("/sales", data=data, follow_redirects=False).status_code == 303
    assert client.post("/sales", data=data, follow_redirects=False).status_code == 303
    session.refresh(inventory)
    assert inventory.quantity == 1
    assert session.query(Sale).count() == 1
    assert session.query(SaleItem).count() == 1


def test_direct_sale_rejects_channel_allocated_inventory(client, session):
    marketplace, inventory = _direct_stock(session)
    session.add(ShopListing(
        catalog_item_id=inventory.catalog_item_id, inventory_item_id=inventory.id,
        title="Already listed", price=Decimal("100"), quantity=1,
        status=ShopListingStatus.ACTIVE,
    ))
    session.commit()

    response = client.post("/sales", data={
        "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
        "quantity": "1", "unit_price": "100",
    }, follow_redirects=False)

    assert response.status_code == 409
    session.refresh(inventory)
    assert inventory.quantity == 2
    assert session.query(Sale).count() == 0


def test_void_direct_sale_restores_inventory_once_and_removes_customer_value(client, session):
    marketplace, inventory = _direct_stock(session, quantity=1)
    client.post("/sales", data={
        "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
        "customer_name": "Alex", "quantity": "1", "unit_price": "100",
    }, follow_redirects=False)
    sale = session.scalars(select(Sale)).one()

    first = client.post(f"/sales/{sale.id}/void", follow_redirects=False)
    second = client.post(f"/sales/{sale.id}/void", follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 409
    session.refresh(inventory)
    session.refresh(sale)
    assert inventory.quantity == 1
    assert inventory.status == InventoryStatus.AVAILABLE
    assert sale.status == SaleStatus.VOIDED
    assert "$0.00" in client.get("/customers").text


@pytest.mark.parametrize("field,value", [
    ("quantity", "0"), ("quantity", "not-a-number"),
    ("unit_price", "0"), ("unit_price", "not-money"),
    ("fees_amount", "-1"),
])
def test_invalid_direct_sale_input_is_rejected_without_stock_change(client, session, field, value):
    marketplace, inventory = _direct_stock(session)
    data = {
        "marketplace_id": marketplace.id, "inventory_item_id": inventory.id,
        "quantity": "1", "unit_price": "100", field: value,
    }
    response = client.post("/sales", data=data, follow_redirects=False)
    assert response.status_code == 400
    session.refresh(inventory)
    assert inventory.quantity == 2
    assert session.query(Sale).count() == 0


def test_commerce_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    for url in ["/marketplaces", "/sales", "/customers"]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code in (302, 303, 401), url
