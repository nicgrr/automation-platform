from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import ShopOrder, ShopOrderItem, ShopOrderStatus


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


def make_order(session, *, status=ShopOrderStatus.PAID, reference="NLV-TEST0001"):
    order = ShopOrder(
        reference=reference, email="buyer@example.test", ship_to_name="A Buyer",
        ship_to_address="1 Test St", ship_to_country="Australia",
        items_total=Decimal("20.00"), shipping_total=Decimal("9.95"),
        grand_total=Decimal("29.95"), currency="AUD", status=status,
    )
    session.add(order)
    session.flush()
    session.add(ShopOrderItem(order_id=order.id, listing_id=None, title="Charizard #4",
                              unit_price=Decimal("20.00"), quantity=1))
    session.commit()
    return order


def test_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/shop-orders")
    assert resp.status_code in (401, 403, 307, 302)


def test_lists_orders_that_need_action_by_default(client, session):
    make_order(session, status=ShopOrderStatus.PAID, reference="NLV-PAID0001")
    make_order(session, status=ShopOrderStatus.PENDING_PAYMENT, reference="NLV-PEND0001")
    body = client.get("/shop-orders").text
    assert "NLV-PAID0001" in body
    assert "NLV-PEND0001" not in body


def test_all_filter_shows_everything(client, session):
    make_order(session, status=ShopOrderStatus.PAID, reference="NLV-PAID0001")
    make_order(session, status=ShopOrderStatus.PENDING_PAYMENT, reference="NLV-PEND0001")
    body = client.get("/shop-orders?status=all").text
    assert "NLV-PAID0001" in body and "NLV-PEND0001" in body


def test_status_filter_narrows_the_list(client, session):
    make_order(session, status=ShopOrderStatus.SHIPPED, reference="NLV-SHIP0001")
    make_order(session, status=ShopOrderStatus.PAID, reference="NLV-PAID0001")
    body = client.get("/shop-orders?status=shipped").text
    assert "NLV-SHIP0001" in body
    assert "NLV-PAID0001" not in body


def test_order_shows_items_and_customer_details(client, session):
    make_order(session)
    body = client.get("/shop-orders?status=all").text
    assert "Charizard #4" in body
    assert "buyer@example.test" in body
    assert "$29.95" in body


def test_a_paid_order_offers_mark_packed(client, session):
    order = make_order(session, status=ShopOrderStatus.PAID)
    body = client.get("/shop-orders").text
    assert f"/shop-orders/{order.id}/pack" in body


def test_marking_packed_advances_the_status(client, session):
    order = make_order(session, status=ShopOrderStatus.PAID)
    resp = client.post(f"/shop-orders/{order.id}/pack", follow_redirects=False)
    assert resp.status_code == 303
    session.refresh(order)
    assert order.status == ShopOrderStatus.PACKED


def test_a_packed_order_offers_mark_shipped(client, session):
    order = make_order(session, status=ShopOrderStatus.PACKED)
    body = client.get("/shop-orders").text
    assert f"/shop-orders/{order.id}/ship" in body
    assert "Tracking number" in body


def test_marking_shipped_records_the_tracking_number(client, session):
    order = make_order(session, status=ShopOrderStatus.PACKED)
    client.post(f"/shop-orders/{order.id}/ship", data={"tracking_number": "AB123456789AU"},
               follow_redirects=False)
    session.refresh(order)
    assert order.status == ShopOrderStatus.SHIPPED
    assert order.tracking_number == "AB123456789AU"
    assert order.shipped_at is not None


def test_cannot_mark_shipped_from_pending_payment(client, session):
    """A guard against skipping straight from unpaid to shipped."""
    order = make_order(session, status=ShopOrderStatus.PENDING_PAYMENT)
    client.post(f"/shop-orders/{order.id}/ship", data={"tracking_number": "X"}, follow_redirects=False)
    session.refresh(order)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT
    assert order.tracking_number is None


def test_cannot_mark_packed_twice(client, session):
    order = make_order(session, status=ShopOrderStatus.SHIPPED)
    client.post(f"/shop-orders/{order.id}/pack", follow_redirects=False)
    session.refresh(order)
    assert order.status == ShopOrderStatus.SHIPPED  # unchanged, not reverted


def test_unknown_order_id_404s(client):
    assert client.post("/shop-orders/does-not-exist/pack").status_code == 404


def test_shipped_order_shows_its_tracking_number(client, session):
    order = make_order(session, status=ShopOrderStatus.SHIPPED)
    order.tracking_number = "AB999AU"
    session.commit()
    body = client.get("/shop-orders?status=all").text
    assert "AB999AU" in body
