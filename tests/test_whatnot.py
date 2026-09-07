from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import Marketplace, MarketplaceFeeRule, Sale, SaleItem


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Marketplace(id="whatnot", name="Whatnot"))
        db.add(MarketplaceFeeRule(id="r1", marketplace_id="whatnot", commission_pct=Decimal("8"), processing_pct=Decimal("3"), fixed_fee=Decimal("0")))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_show(client) -> str:
    response = client.post("/whatnot", data={"title": "Friday One Piece Night", "show_date": "2026-09-12"}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def test_create_show_and_queue_an_item(client):
    show_url = _create_show(client)
    client.post(f"{show_url}/items", data={"description": "Kaido OP17", "starting_price": "50"})
    detail = client.get(show_url)
    assert "Kaido OP17" in detail.text
    assert "$50.00" in detail.text


def test_marking_an_item_sold_creates_a_real_sale_using_the_active_fee_rule(client, session):
    show_url = _create_show(client)
    show_id = show_url.rsplit("/", 1)[-1]
    add = client.post(f"{show_url}/items", data={"description": "Kaido OP17", "starting_price": "50"}, follow_redirects=False)
    detail = client.get(show_url)
    import re
    item_id = re.search(r"/whatnot/[\w-]+/items/([\w-]+)/outcome", detail.text).group(1)

    outcome = client.post(f"{show_url}/items/{item_id}/outcome", data={"outcome": "sold", "final_price": "60"}, follow_redirects=False)
    assert outcome.status_code == 303

    sales = session.scalars(select(Sale)).all()
    assert len(sales) == 1
    sale = sales[0]
    assert sale.gross_amount == Decimal("60")
    assert sale.fees_amount == Decimal("6.60")  # 60 * (8% + 3%)

    sale_items = session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).all()
    assert len(sale_items) == 1
    assert sale_items[0].description == "Kaido OP17"

    updated_detail = client.get(show_url)
    assert "1 / 1" in updated_detail.text
    assert "$60.00" in updated_detail.text


def test_marking_an_item_unsold_does_not_create_a_sale(client, session):
    show_url = _create_show(client)
    add = client.post(f"{show_url}/items", data={"description": "Common bulk card", "starting_price": "1"}, follow_redirects=False)
    detail = client.get(show_url)
    import re
    item_id = re.search(r"/whatnot/[\w-]+/items/([\w-]+)/outcome", detail.text).group(1)

    client.post(f"{show_url}/items/{item_id}/outcome", data={"outcome": "unsold", "final_price": ""}, follow_redirects=False)

    assert session.scalars(select(Sale)).all() == []


def test_whatnot_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/whatnot", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
