from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, ShopOrder, ShopOrderItem, ShopOrderStatus,
)
from storefront import checkout
from storefront.app import app


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ShippingRate(zone=ShippingZone.AU, label="Australia — tracked", price=Decimal("9.95"), free_over=Decimal("150")))
        db.add(ShippingRate(zone=ShippingZone.INTERNATIONAL, label="International", price=Decimal("29.95")))
        db.add(CatalogItem(id="c1", item_type=CatalogItemType.TCG_CARD, name="Charizard"))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_stripe(monkeypatch):
    """Default to payments not configured -- tests that want the live path
    opt in explicitly, so none of them can ever reach the real Stripe API."""
    monkeypatch.setattr(checkout, "stripe_secret", lambda: None)


def _listing(session, *, price="80.00", quantity=2):
    listing = ShopListing(catalog_item_id="c1", title="Charizard #4", price=Decimal(price),
                          quantity=quantity, status=ShopListingStatus.ACTIVE)
    session.add(listing)
    session.commit()
    return listing


def _fill_cart(client, listing_id):
    client.post("/cart/add", data={"listing_id": listing_id}, follow_redirects=False)


def _order_details():
    return {"email": "buyer@example.test", "name": "A Buyer",
            "address": "1 Test St\nSydney NSW 2000", "country": "Australia", "zone": "au"}


# --- the form ---------------------------------------------------------------

def test_checkout_redirects_when_the_cart_is_empty(client):
    resp = client.get("/checkout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cart"


def test_checkout_shows_totals_and_says_payments_are_not_live(client, session):
    listing = _listing(session)
    _fill_cart(client, listing.id)
    body = client.get("/checkout").text
    assert "$80.00" in body
    assert "$9.95" in body
    assert "$89.95" in body
    assert "aren't switched on yet" in body


# --- placing an order -------------------------------------------------------

def test_order_is_written_even_without_a_payment_provider(client, session):
    listing = _listing(session)
    _fill_cart(client, listing.id)

    resp = client.post("/checkout", data=_order_details(), follow_redirects=False)
    assert resp.status_code == 303

    order = session.query(ShopOrder).one()
    assert order.email == "buyer@example.test"
    assert order.grand_total == Decimal("89.95")
    assert order.status == ShopOrderStatus.PENDING_PAYMENT
    assert order.reference.startswith("NLV-")
    assert session.query(ShopOrderItem).count() == 1


def test_international_orders_are_charged_the_higher_rate(client, session):
    listing = _listing(session)
    _fill_cart(client, listing.id)
    details = _order_details() | {"zone": "international", "country": "New Zealand"}

    client.post("/checkout", data=details, follow_redirects=False)

    order = session.query(ShopOrder).one()
    assert order.zone == ShippingZone.INTERNATIONAL
    assert order.shipping_total == Decimal("29.95")
    assert order.grand_total == Decimal("109.95")


def test_stock_is_not_touched_before_payment(client, session):
    """An abandoned or unpaid order must not quietly take stock off sale."""
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    client.post("/checkout", data=_order_details(), follow_redirects=False)

    session.refresh(listing)
    assert listing.quantity == 2
    assert listing.status == ShopListingStatus.ACTIVE


# --- with Stripe configured -------------------------------------------------

def test_configured_stripe_redirects_to_the_hosted_page(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session)
    _fill_cart(client, listing.id)

    fake = AsyncMock(return_value="https://checkout.stripe.com/c/pay/cs_test_123")
    with patch.object(checkout, "create_stripe_session", new=fake):
        resp = client.post("/checkout", data=_order_details(), follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("https://checkout.stripe.com/")


def test_stripe_line_items_are_built_in_cents_from_the_order(session, monkeypatch):
    """Amounts must come from the order we wrote, never from the browser."""
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, price="80.00")
    order = ShopOrder(reference="NLV-TEST1234", email="b@example.test", ship_to_name="B",
                      ship_to_address="x", currency="AUD", grand_total=Decimal("89.95"))
    lines = [{"listing": listing, "quantity": 1, "is_lot": False}]

    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"url": "https://checkout.stripe.com/c/pay/cs_test"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data=None, auth=None):
            captured["data"] = dict(data)
            captured["auth"] = auth
            return FakeResponse()

    monkeypatch.setattr(checkout.httpx, "AsyncClient", lambda **kw: FakeClient())

    import asyncio
    url = asyncio.run(checkout.create_stripe_session(order, lines, Decimal("9.95"), "https://shop.test"))

    assert url.startswith("https://checkout.stripe.com/")
    assert captured["data"]["line_items[0][price_data][unit_amount]"] == "8000"   # $80.00
    assert captured["data"]["line_items[1][price_data][unit_amount]"] == "995"    # $9.95 postage
    assert captured["data"]["line_items[1][price_data][product_data][name]"] == "Shipping"
    assert captured["auth"] == ("sk_test_fake", "")


# --- completion -------------------------------------------------------------

def test_completion_marks_paid_and_decrements_stock_when_stripe_is_live(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()

    body = client.get(f"/checkout/complete?ref={order.reference}").text

    session.refresh(order)
    session.refresh(listing)
    assert order.status == ShopOrderStatus.PAID
    assert order.paid_at is not None
    assert listing.quantity == 1
    assert "order confirmed" in body.lower()


def test_selling_the_last_one_marks_it_sold_out(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=1)
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()

    client.get(f"/checkout/complete?ref={order.reference}")

    session.refresh(listing)
    assert listing.quantity == 0
    assert listing.status == ShopListingStatus.SOLD_OUT


def test_completion_without_stripe_leaves_the_order_unpaid(client, session):
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()

    body = client.get(f"/checkout/complete?ref={order.reference}").text

    session.refresh(order)
    session.refresh(listing)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT
    assert listing.quantity == 2          # nothing sold, nothing decremented
    assert "arrange payment" in body


def test_unknown_order_reference_404s(client):
    assert client.get("/checkout/complete?ref=NLV-NOPE").status_code == 404


def test_a_bulk_lot_can_be_checked_out(client, session):
    lot = BulkLot(title="Steam Siege — bulk lot (10 cards)", card_count=10,
                  market_value=Decimal("20"), price=Decimal("12.00"),
                  quantity=1, status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.commit()
    _fill_cart(client, f"B:{lot.id}")

    client.post("/checkout", data=_order_details(), follow_redirects=False)

    order = session.query(ShopOrder).one()
    item = session.query(ShopOrderItem).one()
    assert order.items_total == Decimal("12.00")
    assert item.listing_id is None          # a lot isn't a single listing
    assert "bulk lot" in item.title
