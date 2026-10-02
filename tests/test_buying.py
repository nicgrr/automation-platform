from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.buying import traffic_light
from automation_control.database import Base, get_session
from automation_control.models import (
    BuyThresholdConfig, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus,
    PotentialPurchase, PotentialPurchaseStatus, PurchaseLot, PurchaseLotItem,
    PurchaseLotStatus, Retailer, Supplier, SupplierProduct, SupplierStatus,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BuyThresholdConfig(label="Default", green_max_pct=Decimal("55"), yellow_max_pct=Decimal("70"), is_default=True))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_traffic_light_bands():
    config = BuyThresholdConfig(label="t", green_max_pct=Decimal("55"), yellow_max_pct=Decimal("70"))
    assert traffic_light(Decimal("50"), config) == ("ok", "GREEN")
    assert traffic_light(Decimal("55"), config) == ("ok", "GREEN")
    assert traffic_light(Decimal("60"), config) == ("warn", "YELLOW")
    assert traffic_light(Decimal("90"), config) == ("bad", "RED")
    assert traffic_light(None, config) == ("neutral", "UNKNOWN")


def test_calculator_matches_the_worked_example_from_the_spec(client):
    """Market $100, seller wants $60, expected Whatnot sale $90."""
    response = client.get(
        "/buying-calculator",
        params={"market_price": "100", "seller_price": "60", "expected_sale_price": "90"},
    )
    assert response.status_code == 200
    assert "60.0%" in response.text          # buy % of market
    assert "$30.00" in response.text          # gross profit (90 - 60)


def test_calculator_blank_form_shows_no_result(client):
    response = client.get("/buying-calculator")
    assert response.status_code == 200
    assert "Verdict" not in response.text


def test_purchase_lot_lifecycle(client, session):
    first_catalog = CatalogItem(id="lot-card-1", name="Kaido OP17", item_type=CatalogItemType.TCG_CARD)
    second_catalog = CatalogItem(id="lot-card-2", name="Mihawk", item_type=CatalogItemType.TCG_CARD)
    session.add_all([first_catalog, second_catalog])
    session.commit()
    create = client.post(
        "/purchase-lots",
        data={"source": "FB Marketplace Collection", "seller": "Jane", "asking_price": "1000", "target_buy_pct": "55"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    lot_url = create.headers["location"]

    client.post(f"{lot_url}/items", data={
        "description": "Kaido OP17", "catalog_item_id": first_catalog.id,
        "market_value": "400", "quantity": "1", "condition": "Near Mint",
    })
    client.post(f"{lot_url}/items", data={
        "description": "Mihawk", "catalog_item_id": second_catalog.id,
        "market_value": "100", "quantity": "1", "condition": "Near Mint",
    })

    detail = client.get(lot_url)
    assert detail.status_code == 200
    assert "$500.00" in detail.text   # market value of lines
    assert "RED" in detail.text       # asking $1000 vs $500 market is a bad deal

    updated = client.post(f"{lot_url}/status", data={"status": PurchaseLotStatus.PURCHASED.value, "offered_price": "300"}, follow_redirects=False)
    assert updated.status_code == 303
    inventory = session.scalars(select(InventoryItem).order_by(InventoryItem.catalog_item_id)).all()
    assert len(inventory) == 2
    assert sum(item.quantity for item in inventory) == 2
    assert sum(line.allocated_total_cost for line in session.scalars(select(PurchaseLotItem))) == Decimal("300")


def test_potential_stock_compatibility_route_points_to_market_watch(client, session):
    create = client.post(
        "/potential-stock",
        data={"description": "12 Sonny Angels", "seller": "", "source": "", "url": "",
              "asking_price": "300", "market_value": "450", "target_price": "250"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    assert create.headers["location"].startswith("/market-watch?view=candidates")
    assert session.query(PotentialPurchase).count() == 0

    board = client.get("/potential-stock", follow_redirects=False)
    assert board.status_code == 303
    assert board.headers["location"] == "/market-watch?view=candidates"


def test_buying_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    for url in ["/buying-calculator", "/purchase-lots", "/potential-stock"]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code in (302, 303, 401), url


def test_buying_calculator_has_quick_price_widget_wired_to_market_price(client):
    response = client.get("/buying-calculator")
    assert "class='quick-price'" in response.text
    assert "data-price-field='market_price'" in response.text


def test_potential_stock_no_longer_renders_a_second_candidate_form(client):
    response = client.get("/potential-stock", follow_redirects=False)
    assert response.status_code == 303


def test_purchase_lot_detail_has_quick_price_widget(client):
    create = client.post(
        "/purchase-lots",
        data={"source": "FB Marketplace Collection", "seller": "Jane", "asking_price": "1000", "target_buy_pct": "55"},
        follow_redirects=False,
    )
    detail = client.get(create.headers["location"])
    assert "data-name-field='description'" in detail.text
    assert "data-price-field='market_value'" in detail.text


def test_empty_purchase_lot_cannot_be_received(client):
    create = client.post(
        "/purchase-lots",
        data={"source": "Empty lot", "seller": "Jane", "asking_price": "100", "target_buy_pct": "55"},
        follow_redirects=False,
    )
    response = client.post(
        f"{create.headers['location']}/status",
        data={"status": "purchased", "offered_price": "80"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "Add at least one" in response.text


def test_automated_retailer_lot_is_read_only_on_purchase_lot_routes(client, session):
    retailer = Retailer(name="Test retailer")
    session.add(retailer)
    session.flush()
    lot = PurchaseLot(
        source="Test retailer purchase", seller="Test retailer",
        asking_price=Decimal("50"), purchase_price=Decimal("50"),
        status=PurchaseLotStatus.PURCHASED, retailer_id=retailer.id,
    )
    session.add(lot)
    session.commit()

    detail = client.get(f"/purchase-lots/{lot.id}")
    add = client.post(
        f"/purchase-lots/{lot.id}/items",
        data={"description": "Injected", "market_value": "1", "quantity": "1"},
        follow_redirects=False,
    )
    status = client.post(
        f"/purchase-lots/{lot.id}/status",
        data={"status": "declined", "offered_price": "1"},
        follow_redirects=False,
    )

    assert detail.status_code == 200
    assert "created automatically from retailer purchases" in detail.text
    assert "Identify &amp; price" not in detail.text
    assert add.status_code == 409
    assert status.status_code == 409


def test_supplier_purchase_lot_allocates_cost_and_receives_inventory_once(client, session):
    supplier = Supplier(name="Integrated Supplier", account_status=SupplierStatus.ACTIVE)
    first_catalog = CatalogItem(
        id="supplier-item-1", name="Supplier Item One", item_type=CatalogItemType.SEALED_PRODUCT,
    )
    second_catalog = CatalogItem(
        id="supplier-item-2", name="Supplier Item Two", item_type=CatalogItemType.SEALED_PRODUCT,
    )
    session.add_all([supplier, first_catalog, second_catalog])
    session.flush()
    first_product = SupplierProduct(
        supplier_id=supplier.id, catalog_item_id=first_catalog.id,
        description=first_catalog.name, cost=Decimal("10"), min_order_qty=1,
    )
    second_product = SupplierProduct(
        supplier_id=supplier.id, catalog_item_id=second_catalog.id,
        description=second_catalog.name, cost=Decimal("20"), min_order_qty=1,
    )
    session.add_all([first_product, second_product])
    session.commit()
    create = client.post("/purchase-lots", data={
        "supplier_id": supplier.id, "source": "Wholesale order", "seller": "",
        "asking_price": "100", "target_buy_pct": "55",
    }, follow_redirects=False)
    lot_url = create.headers["location"]
    assert client.post(f"{lot_url}/items", data={
        "supplier_product_id": first_product.id, "market_value": "30",
        "quantity": "2", "condition": "Sealed",
    }, follow_redirects=False).status_code == 303
    assert client.post(f"{lot_url}/items", data={
        "supplier_product_id": second_product.id, "market_value": "40",
        "quantity": "1", "condition": "Sealed",
    }, follow_redirects=False).status_code == 303

    received = client.post(
        f"{lot_url}/status", data={"status": "purchased", "offered_price": "90"},
        follow_redirects=False,
    )
    repeated = client.post(
        f"{lot_url}/status", data={"status": "purchased", "offered_price": "90"},
        follow_redirects=False,
    )

    assert received.status_code == 303
    assert repeated.status_code == 409
    lot = session.get(PurchaseLot, lot_url.rsplit("/", 1)[-1])
    lines = session.scalars(
        select(PurchaseLotItem).where(PurchaseLotItem.purchase_lot_id == lot.id)
        .order_by(PurchaseLotItem.description)
    ).all()
    inventory = session.scalars(select(InventoryItem).where(
        InventoryItem.catalog_item_id.in_([first_catalog.id, second_catalog.id])
    ).order_by(InventoryItem.catalog_item_id)).all()
    assert lot.status == PurchaseLotStatus.PURCHASED
    assert lot.inventory_received_at is not None
    assert [line.allocated_total_cost for line in lines] == [Decimal("45"), Decimal("45")]
    assert [line.allocated_unit_cost for line in lines] == [Decimal("22.50"), Decimal("45")]
    assert [(item.quantity, item.allocated_cost_basis, item.status) for item in inventory] == [
        (2, Decimal("22.50"), InventoryStatus.AVAILABLE),
        (1, Decimal("45"), InventoryStatus.AVAILABLE),
    ]
    assert session.query(InventoryItem).filter(
        InventoryItem.catalog_item_id.in_([first_catalog.id, second_catalog.id])
    ).count() == 2
