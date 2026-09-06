from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.commerce import active_fee_rule
from automation_control.database import Base, get_session
from automation_control.models import Marketplace, MarketplaceFeeRule


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


def test_recording_a_sale_creates_the_customer_and_computes_net_profit(client):
    response = client.post(
        "/sales",
        data={"marketplace_id": "", "customer_name": "Alex", "description": "Kaido OP17", "quantity": "1",
              "unit_price": "100", "cost_basis": "40", "fees_amount": "10", "shipping_revenue": "0",
              "shipping_cost": "5", "packaging_cost": "2"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/sales")
    assert "Kaido OP17" in page.text
    assert "$43.00" in page.text  # 100 - 40 - 10 - 5 - 2 = 43

    customers = client.get("/customers")
    assert "Alex" in customers.text
    assert "$100.00" in customers.text  # lifetime value


def test_commerce_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    for url in ["/marketplaces", "/sales", "/customers"]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code in (302, 303, 401), url
