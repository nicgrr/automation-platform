from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.buying import traffic_light
from automation_control.database import Base, get_session
from automation_control.models import BuyThresholdConfig, PotentialPurchaseStatus, PurchaseLotStatus


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


def test_purchase_lot_lifecycle(client):
    create = client.post(
        "/purchase-lots",
        data={"source": "FB Marketplace Collection", "seller": "Jane", "asking_price": "1000", "target_buy_pct": "55"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    lot_url = create.headers["location"]

    client.post(f"{lot_url}/items", data={"description": "Kaido OP17", "market_value": "400", "quantity": "1"})
    client.post(f"{lot_url}/items", data={"description": "Mihawk", "market_value": "100", "quantity": "1"})

    detail = client.get(lot_url)
    assert detail.status_code == 200
    assert "$500.00" in detail.text   # market value of lines
    assert "RED" in detail.text       # asking $1000 vs $500 market is a bad deal

    updated = client.post(f"{lot_url}/status", data={"status": PurchaseLotStatus.PURCHASED.value, "offered_price": "300"}, follow_redirects=False)
    assert updated.status_code == 303


def test_potential_stock_watchlist_add_and_advance(client):
    create = client.post(
        "/potential-stock",
        data={"description": "12 Sonny Angels", "seller": "", "source": "", "url": "",
              "asking_price": "300", "market_value": "450", "target_price": "250"},
        follow_redirects=False,
    )
    assert create.status_code == 303

    board = client.get("/potential-stock")
    assert "12 Sonny Angels" in board.text
    assert "Watching (1)" in board.text

    import re
    detail_url = re.search(r"/potential-stock/([\w-]+)", board.text).group(0)
    advance = client.post(f"{detail_url}/status", data={"status": PotentialPurchaseStatus.OFFER_SENT.value}, follow_redirects=False)
    assert advance.status_code == 303

    board2 = client.get("/potential-stock")
    assert "Offer Sent (1)" in board2.text
    assert "Watching (0)" in board2.text


def test_buying_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    for url in ["/buying-calculator", "/purchase-lots", "/potential-stock"]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code in (302, 303, 401), url
