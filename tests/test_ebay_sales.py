from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import EbayImportedOrder


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


def make_order(session, ebay_order_id, *, sku="sku-1", dismissed=False, applied=True,
               buyer="A Buyer", price="12.00"):
    row = EbayImportedOrder(
        ebay_order_id=ebay_order_id, sku=sku, quantity=1, marketplace_id="EBAY_AU",
        observed_at=datetime.now(UTC), applied_at=datetime.now(UTC) if applied else None,
        dismissed_at=datetime.now(UTC) if dismissed else None,
        shop_listing_id=None,
        raw_snapshot={
            "buyer": {"username": buyer},
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {
                    "fullName": buyer,
                    "contactAddress": {"addressLine1": "1 Test St", "city": "Sydney",
                                        "stateOrProvince": "NSW", "postalCode": "2000",
                                        "countryCode": "AU"},
                }}}
            ],
            "lineItems": [{"sku": sku, "title": "Charizard #4", "lineItemCost": {"value": price, "currency": "AUD"}}],
            "pricingSummary": {"total": {"value": price, "currency": "AUD"}},
        },
    )
    session.add(row)
    session.commit()
    return row


def test_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/ebay-sales")
    assert resp.status_code in (401, 403, 307, 302)


def test_the_page_has_a_mobile_breakpoint(client, session):
    assert "@media (max-width: 680px)" in client.get("/ebay-sales").text


def test_an_applied_order_shows_up_by_default(client, session):
    make_order(session, "ORD-1")
    body = client.get("/ebay-sales").text
    assert "ORD-1" in body
    assert "Charizard" in body
    assert "1 Test St" in body


def test_an_unapplied_row_is_never_shown(client, session):
    make_order(session, "ORD-1", applied=False)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_default_filter_hides_a_completed_order(client, session):
    make_order(session, "ORD-1", dismissed=True)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_all_filter_shows_a_completed_order_too(client, session):
    make_order(session, "ORD-1", dismissed=True)
    body = client.get("/ebay-sales?status=all").text
    assert "ORD-1" in body


def test_completed_filter_shows_only_completed_orders(client, session):
    make_order(session, "ORD-NEEDS-ACTION")
    make_order(session, "ORD-DONE", dismissed=True)
    body = client.get("/ebay-sales?status=completed").text
    assert "ORD-DONE" in body
    assert "ORD-NEEDS-ACTION" not in body


def test_a_multi_line_order_is_only_completed_once_every_line_is(client, session):
    make_order(session, "ORD-1", sku="sku-1", dismissed=True)
    make_order(session, "ORD-1", sku="sku-2", dismissed=False)
    body = client.get("/ebay-sales").text  # default: needs action
    assert "ORD-1" in body


def test_marking_complete_dismisses_every_line_on_the_order(client, session):
    make_order(session, "ORD-1", sku="sku-1")
    make_order(session, "ORD-1", sku="sku-2")
    resp = client.post("/ebay-sales/ORD-1/complete", follow_redirects=False)
    assert resp.status_code == 303

    rows = session.query(EbayImportedOrder).filter_by(ebay_order_id="ORD-1").all()
    assert all(r.dismissed_at is not None for r in rows)

    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body
    body = client.get("/ebay-sales?status=completed").text
    assert "ORD-1" in body


def test_discard_asks_for_confirmation(client, session):
    make_order(session, "ORD-1")
    body = client.get("/ebay-sales").text
    assert "onsubmit=\"return confirm('Discard this order from the active view?');\"" in body


def test_discarding_an_order_also_removes_it_from_the_active_view(client, session):
    make_order(session, "ORD-1")
    client.post("/ebay-sales/ORD-1/discard", follow_redirects=False)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_discarding_records_a_distinct_audit_action_from_completing(client, session):
    from automation_control.models import AuditEvent

    make_order(session, "ORD-1")
    make_order(session, "ORD-2")
    client.post("/ebay-sales/ORD-1/complete")
    client.post("/ebay-sales/ORD-2/discard")

    complete_event = session.query(AuditEvent).filter_by(resource_id="ORD-1").one()
    discard_event = session.query(AuditEvent).filter_by(resource_id="ORD-2").one()
    assert complete_event.action == "ebay_sale.complete"
    assert discard_event.action == "ebay_sale.discard"


def test_a_needs_action_order_is_open_by_default(client, session):
    make_order(session, "ORD-OPEN")
    body = client.get("/ebay-sales").text
    assert "<details class='panel order-block' open>" in body


def test_a_completed_order_is_collapsed_by_default(client, session):
    make_order(session, "ORD-DONE", dismissed=True)
    body = client.get("/ebay-sales?status=completed").text
    assert "<details class='panel order-block' open>" not in body
    assert "<details class='panel order-block'>" in body
