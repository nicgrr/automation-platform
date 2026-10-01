import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus, ShippingRate, ShippingZone,
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

def _checkout_with_a_stripe_session(client, session, listing) -> ShopOrder:
    """Place an order as if Stripe checkout was actually reached: the
    mocked create_stripe_session in these tests never talks to Stripe, so
    a session id is stamped on afterwards the way the real flow would."""
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()
    order.stripe_session_id = "cs_test_123"
    session.commit()
    return order


def test_completion_marks_paid_and_decrements_stock_when_stripe_confirms_it(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=2)
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session",
                      new=AsyncMock(return_value={"payment_status": "paid"})):
        body = client.get(f"/checkout/complete?ref={order.reference}").text

    session.refresh(order)
    session.refresh(listing)
    assert order.status == ShopOrderStatus.PAID
    assert order.paid_at is not None
    assert listing.quantity == 1
    assert "order confirmed" in body.lower()


def test_landing_on_the_success_url_is_not_by_itself_proof_of_payment(client, session, monkeypatch):
    """The whole point of verifying: reaching this URL is trivial for
    anyone with the reference. Stripe saying the session is still unpaid
    must NOT be overridden by the mere fact of the redirect happening."""
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=2)
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session",
                      new=AsyncMock(return_value={"payment_status": "unpaid"})):
        body = client.get(f"/checkout/complete?ref={order.reference}").text

    session.refresh(order)
    session.refresh(listing)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT
    assert listing.quantity == 2  # nothing decremented for an unconfirmed payment
    assert "confirming your payment" in body.lower()


def test_a_stripe_api_hiccup_does_not_falsely_confirm_or_crash(client, session, monkeypatch):
    """fetch_checkout_session returns None on any network/API failure --
    that must read as "not confirmed", never as "paid", and must never
    500 the page a paying customer is looking at."""
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=2)
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session", new=AsyncMock(return_value=None)):
        response = client.get(f"/checkout/complete?ref={order.reference}")

    assert response.status_code == 200
    session.refresh(order)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT


def test_a_second_visit_after_paid_does_not_double_decrement(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=2)
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session",
                      new=AsyncMock(return_value={"payment_status": "paid"})):
        client.get(f"/checkout/complete?ref={order.reference}")
        client.get(f"/checkout/complete?ref={order.reference}")  # revisit, e.g. a refresh

    session.refresh(listing)
    assert listing.quantity == 1  # decremented once, not twice


def test_selling_the_last_one_marks_it_sold_out(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    listing = _listing(session, quantity=1)
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session",
                      new=AsyncMock(return_value={"payment_status": "paid"})):
        client.get(f"/checkout/complete?ref={order.reference}")

    session.refresh(listing)
    assert listing.quantity == 0
    assert listing.status == ShopListingStatus.SOLD_OUT


def test_a_real_sale_also_marks_the_physical_card_sold(client, session, monkeypatch):
    """Real incident, 2026-09-28: a checkout used to leave the physical
    InventoryItem behind a sold-out listing marked AVAILABLE forever --
    nothing downstream (the dashboard count, a re-scan, a second listing
    built from stock) had any way to know the card was actually gone."""
    monkeypatch.setattr(checkout, "stripe_secret", lambda: "sk_test_fake")
    item = InventoryItem(catalog_item_id="c1", quantity=1, status=InventoryStatus.AVAILABLE)
    session.add(item)
    session.commit()
    listing = _listing(session, quantity=1)
    listing.inventory_item_id = item.id
    session.commit()
    order = _checkout_with_a_stripe_session(client, session, listing)

    with patch.object(checkout, "fetch_checkout_session",
                      new=AsyncMock(return_value={"payment_status": "paid"})):
        client.get(f"/checkout/complete?ref={order.reference}")

    session.refresh(item)
    assert item.quantity == 0
    assert item.status == InventoryStatus.SOLD


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


# --- signature verification --------------------------------------------------

def test_a_valid_signature_verifies(monkeypatch):
    import hashlib
    import hmac
    import time

    secret, body = "whsec_test", b'{"type":"checkout.session.completed"}'
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert checkout.verify_stripe_signature(body, header, secret) is True


def test_a_tampered_body_fails_verification():
    import hashlib
    import hmac
    import time

    secret = "whsec_test"
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + b"original", hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert checkout.verify_stripe_signature(b"tampered", header, secret) is False


def test_an_old_timestamp_is_rejected_as_a_replay():
    import hashlib
    import hmac

    secret, body = "whsec_test", b"payload"
    old_ts = str(int(time.time()) - 10_000)
    sig = hmac.new(secret.encode(), f"{old_ts}.".encode() + body, hashlib.sha256).hexdigest()
    header = f"t={old_ts},v1={sig}"
    assert checkout.verify_stripe_signature(body, header, secret) is False


@pytest.mark.parametrize("header,secret", [
    ("", "whsec_test"),
    ("t=123,v1=deadbeef", ""),
    ("garbage", "whsec_test"),
    ("t=notanumber,v1=deadbeef", "whsec_test"),
])
def test_malformed_or_missing_signature_inputs_are_refused(header, secret):
    assert checkout.verify_stripe_signature(b"x", header, secret) is False


def test_a_wrong_secret_fails_verification():
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_right", f"{ts}.".encode() + b"body", hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert checkout.verify_stripe_signature(b"body", header, "whsec_wrong") is False


# --- webhook endpoint --------------------------------------------------------

def _sign(secret: str, body: bytes) -> str:
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_webhook_503s_when_not_configured(client):
    resp = client.post("/webhooks/stripe", content=b"{}")
    assert resp.status_code == 503


def test_webhook_rejects_a_bad_signature(client, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    resp = client.post("/webhooks/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=bad"})
    assert resp.status_code == 400


def test_webhook_marks_the_order_paid_and_decrements_stock(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()
    order.stripe_session_id = "cs_test_123"
    session.commit()

    event = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": order.reference,
                            "payment_status": "paid", "id": "cs_test_123"}},
    }).encode()
    resp = client.post("/webhooks/stripe", content=event,
                       headers={"stripe-signature": _sign("whsec_test", event)})

    assert resp.status_code == 200
    session.refresh(order)
    session.refresh(listing)
    assert order.status == ShopOrderStatus.PAID
    assert listing.quantity == 1


def test_webhook_ignores_an_unpaid_session(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()

    event = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": order.reference, "payment_status": "unpaid"}},
    }).encode()
    client.post("/webhooks/stripe", content=event, headers={"stripe-signature": _sign("whsec_test", event)})

    session.refresh(order)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT


def test_webhook_ignores_events_for_an_unknown_reference(client, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    event = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": "NLV-NOPE", "payment_status": "paid"}},
    }).encode()
    resp = client.post("/webhooks/stripe", content=event,
                       headers={"stripe-signature": _sign("whsec_test", event)})
    assert resp.status_code == 200  # acknowledged, just nothing to do


def test_webhook_acknowledges_event_types_it_does_not_act_on(client, monkeypatch):
    """Stripe retries anything but a 2xx -- an event this endpoint isn't
    built to handle should still be acked, not bounced forever."""
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    event = json.dumps({"type": "customer.created", "data": {"object": {}}}).encode()
    resp = client.post("/webhooks/stripe", content=event,
                       headers={"stripe-signature": _sign("whsec_test", event)})
    assert resp.status_code == 200


def test_webhook_does_not_double_process_a_retried_event(client, session, monkeypatch):
    monkeypatch.setattr(checkout, "stripe_webhook_secret", lambda: "whsec_test")
    listing = _listing(session, quantity=2)
    _fill_cart(client, listing.id)
    with patch.object(checkout, "create_stripe_session", new=AsyncMock(return_value=None)):
        client.post("/checkout", data=_order_details(), follow_redirects=False)
    order = session.query(ShopOrder).one()

    event = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {"client_reference_id": order.reference, "payment_status": "paid"}},
    }).encode()
    headers = {"stripe-signature": _sign("whsec_test", event)}
    client.post("/webhooks/stripe", content=event, headers=headers)
    client.post("/webhooks/stripe", content=event, headers=headers)  # Stripe's own retry

    session.refresh(listing)
    assert listing.quantity == 1  # decremented once


def test_fetch_checkout_session_returns_none_on_a_network_error(monkeypatch):
    import httpx as httpx_module

    class BoomClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx_module.ConnectError("no route")

    monkeypatch.setattr(checkout.httpx, "AsyncClient", lambda **k: BoomClient())
    result = __import__("asyncio").run(checkout.fetch_checkout_session("sk_test", "cs_test"))
    assert result is None


def test_is_stripe_session_paid_reads_the_payment_status_field():
    assert checkout._is_stripe_session_paid({"payment_status": "paid"}) is True
    assert checkout._is_stripe_session_paid({"payment_status": "unpaid"}) is False
    assert checkout._is_stripe_session_paid({}) is False
    assert checkout._is_stripe_session_paid(None) is False


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
