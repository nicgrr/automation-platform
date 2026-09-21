import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import InventoryItem


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


def test_add_a_supply_item_and_list_it(client):
    create = client.post(
        "/accessories",
        data={"name": "Ultra Pro Eclipse Sleeves - 100ct", "brand": "Ultra Pro", "product_type": "Sleeves",
              "pack_size": "100ct", "color": "Matte Black", "retail_price": "9.99"},
        follow_redirects=False,
    )
    assert create.status_code == 303

    listing = client.get("/accessories")
    assert "Ultra Pro Eclipse Sleeves - 100ct" in listing.text
    assert "$9.99" in listing.text


def test_accessories_page_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/accessories", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


def test_adding_with_a_quantity_creates_an_inventory_item(client, session):
    client.post(
        "/accessories",
        data={"name": "Dragon Shield Deck Box", "brand": "Dragon Shield", "product_type": "Deck Box",
              "pack_size": "", "color": "", "retail_price": "", "quantity": "5"},
        follow_redirects=False,
    )
    item = session.query(InventoryItem).one()
    assert item.card_id is None
    assert item.quantity == 5
    assert item.catalog_item_id is not None

    listing = client.get("/accessories")
    assert ">5<" in listing.text


def test_adding_with_no_quantity_creates_no_inventory_item(client, session):
    client.post(
        "/accessories",
        data={"name": "Dragon Shield Deck Box", "brand": "Dragon Shield", "product_type": "Deck Box",
              "pack_size": "", "color": "", "retail_price": ""},
        follow_redirects=False,
    )
    assert session.query(InventoryItem).count() == 0
    listing = client.get("/accessories")
    assert "Dragon Shield Deck Box" in listing.text
