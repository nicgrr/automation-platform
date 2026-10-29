from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet, CardVariant, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus,
    ShopListing, ShopListingStatus,
    ShopOrder, ShopOrderItem, ShopOrderStatus, StorageLocation, TcgCard,
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


def test_lists_paid_and_pending_orders_that_need_action_by_default(client, session):
    make_order(session, status=ShopOrderStatus.PAID, reference="NLV-PAID0001")
    make_order(session, status=ShopOrderStatus.PENDING_PAYMENT, reference="NLV-PEND0001")
    body = client.get("/shop-orders").text
    assert "NLV-PAID0001" in body
    assert "NLV-PEND0001" in body


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


def test_marking_shipped_rejects_blank_or_overlong_tracking(client, session):
    order = make_order(session, status=ShopOrderStatus.PACKED)
    assert client.post(f"/shop-orders/{order.id}/ship", data={"tracking_number": "   "}).status_code == 400
    assert client.post(f"/shop-orders/{order.id}/ship", data={"tracking_number": "X" * 129}).status_code == 400
    session.refresh(order)
    assert order.status == ShopOrderStatus.PACKED


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


def make_reserved_order(session, *, reference="NLV-RESERVE1"):
    session.add(CatalogItem(id="reserved-card", name="Reserved card", item_type=CatalogItemType.TCG_CARD))
    inventory = InventoryItem(id="reserved-inv", catalog_item_id="reserved-card", quantity=0,
                              status=InventoryStatus.RESERVED)
    listing = ShopListing(id="reserved-listing", catalog_item_id="reserved-card",
                          inventory_item_id="reserved-inv", title="Reserved card",
                          price=Decimal("20.00"), quantity=0, status=ShopListingStatus.SOLD_OUT)
    order = ShopOrder(reference=reference, email="buyer@example.test", ship_to_name="A Buyer",
                      ship_to_address="1 Test St", ship_to_country="Australia",
                      items_total=Decimal("20.00"), shipping_total=Decimal("9.95"),
                      grand_total=Decimal("29.95"), currency="AUD",
                      status=ShopOrderStatus.PENDING_PAYMENT, stock_reserved_at=datetime.now(UTC))
    session.add_all([inventory, listing, order])
    session.flush()
    session.add(ShopOrderItem(order_id=order.id, listing_id=listing.id, title=listing.title,
                              unit_price=listing.price, quantity=1))
    session.commit()
    return order, listing, inventory


def test_manual_payment_finalises_a_reservation_without_decrementing_twice(client, session):
    order, listing, inventory = make_reserved_order(session)
    client.post(f"/shop-orders/{order.id}/paid", data={"payment_confirmed": "true"},
                follow_redirects=False)
    session.refresh(order)
    session.refresh(listing)
    session.refresh(inventory)
    assert order.status == ShopOrderStatus.PAID
    assert listing.quantity == 0
    assert inventory.quantity == 0
    assert inventory.status == InventoryStatus.SOLD


def test_manual_payment_requires_funds_received_confirmation(client, session):
    order, _listing, _inventory = make_reserved_order(session)
    response = client.post(f"/shop-orders/{order.id}/paid", follow_redirects=False)
    assert response.status_code == 400
    session.refresh(order)
    assert order.status == ShopOrderStatus.PENDING_PAYMENT


def test_cancelling_pending_order_restores_stock_exactly_once(client, session):
    order, listing, inventory = make_reserved_order(session)
    client.post(f"/shop-orders/{order.id}/cancel", follow_redirects=False)
    client.post(f"/shop-orders/{order.id}/cancel", follow_redirects=False)
    session.refresh(order)
    session.refresh(listing)
    session.refresh(inventory)
    assert order.status == ShopOrderStatus.CANCELLED
    assert listing.quantity == 1
    assert listing.status == ShopListingStatus.ACTIVE
    assert inventory.quantity == 1
    assert inventory.status == InventoryStatus.LISTED


def test_stripe_backed_cancel_expires_session_before_restoring(client, session):
    order, listing, _inventory = make_reserved_order(session)
    order.stripe_session_id = "cs_test_cancel"
    session.commit()
    with patch("automation_control.shop_orders._expire_stripe_session", new=AsyncMock()) as expire:
        client.post(f"/shop-orders/{order.id}/cancel", follow_redirects=False)
    expire.assert_awaited_once_with("cs_test_cancel")
    session.refresh(listing)
    assert listing.quantity == 1


def test_refund_requires_confirmation_and_only_restocks_when_confirmed(client, session):
    order, listing, inventory = make_reserved_order(session)
    client.post(f"/shop-orders/{order.id}/paid", data={"payment_confirmed": "true"},
                follow_redirects=False)

    denied = client.post(f"/shop-orders/{order.id}/refund", data={}, follow_redirects=False)
    assert denied.status_code == 400
    session.refresh(order)
    assert order.status == ShopOrderStatus.PAID

    client.post(f"/shop-orders/{order.id}/refund",
                data={"refund_confirmed": "true", "restock": "true", "reason": "Returned"},
                follow_redirects=False)
    session.refresh(order)
    session.refresh(listing)
    session.refresh(inventory)
    assert order.status == ShopOrderStatus.REFUNDED
    assert order.refunded_at is not None
    assert listing.quantity == 1
    assert inventory.quantity == 1
    assert "Returned" in order.notes


def test_shipped_order_shows_its_tracking_number(client, session):
    order = make_order(session, status=ShopOrderStatus.SHIPPED)
    order.tracking_number = "AB999AU"
    session.commit()
    body = client.get("/shop-orders?status=all").text
    assert "AB999AU" in body


# --- order detail: click into any order regardless of status -----------

def test_the_list_links_each_order_to_its_own_detail_page(client, session):
    order = make_order(session)
    body = client.get("/shop-orders?status=all").text
    assert f"href='/shop-orders/{order.id}'" in body


def test_order_detail_shows_cards_and_address(client, session):
    order = make_order(session)
    body = client.get(f"/shop-orders/{order.id}").text
    assert "Charizard #4" in body
    assert "A Buyer" in body
    assert "1 Test St" in body
    assert "buyer@example.test" in body
    assert "$29.95" in body


def test_order_detail_offers_mark_packed_when_paid(client, session):
    order = make_order(session, status=ShopOrderStatus.PAID)
    body = client.get(f"/shop-orders/{order.id}").text
    assert f"/shop-orders/{order.id}/pack" in body


def test_order_detail_offers_confirm_shipped_when_packed(client, session):
    order = make_order(session, status=ShopOrderStatus.PACKED)
    body = client.get(f"/shop-orders/{order.id}").text
    assert f"/shop-orders/{order.id}/ship" in body
    assert "Confirm shipped" in body


def test_order_detail_shows_tracking_once_shipped_with_no_further_action(client, session):
    order = make_order(session, status=ShopOrderStatus.SHIPPED)
    order.tracking_number = "AB999AU"
    session.commit()
    body = client.get(f"/shop-orders/{order.id}").text
    assert "AB999AU" in body
    assert f"/shop-orders/{order.id}/ship" not in body


def test_order_detail_confirming_shipped_from_the_detail_page_works(client, session):
    order = make_order(session, status=ShopOrderStatus.PACKED)
    resp = client.post(f"/shop-orders/{order.id}/ship", data={"tracking_number": "XYZ1"}, follow_redirects=False)
    assert resp.status_code == 303
    session.refresh(order)
    assert order.status == ShopOrderStatus.SHIPPED


def test_order_detail_shows_notes_when_present(client, session):
    order = make_order(session)
    order.notes = "Gift-wrap requested"
    session.commit()
    body = client.get(f"/shop-orders/{order.id}").text
    assert "Gift-wrap requested" in body


def test_order_detail_404s_for_an_unknown_order(client):
    assert client.get("/shop-orders/does-not-exist").status_code == 404


# --- pack assistant: locating the physical card behind a line item -----

def make_order_with_real_card(session, *, status=ShopOrderStatus.PAID, location=None):
    """A line item with the full listing -> inventory -> catalogue chain,
    the shape the pack assistant needs to show a photo and set details."""
    session.add(CardSet(id="sv3", name="Obsidian Flames", game="pokemon"))
    session.add(CatalogItem(id="card-1", name="Charizard ex", item_type=CatalogItemType.TCG_CARD))
    session.add(TcgCard(catalog_item_id="card-1", game="pokemon", set_id="sv3", number="125", rarity="Rare Ultra"))
    loc_id = None
    if location:
        loc = StorageLocation(name=location)
        session.add(loc)
        session.flush()
        loc_id = loc.id
    session.add(InventoryItem(id="inv-1", catalog_item_id="card-1", variant=CardVariant.HOLO,
                              condition="Near Mint", quantity=1, storage_location_id=loc_id))
    session.add(ShopListing(id="listing-1", catalog_item_id="card-1", inventory_item_id="inv-1",
                            title="Charizard ex", price=Decimal("20.00"), status=ShopListingStatus.ACTIVE))
    order = ShopOrder(
        reference="NLV-CARD0001", email="buyer@example.test", ship_to_name="A Buyer",
        ship_to_address="1 Test St", ship_to_country="Australia",
        items_total=Decimal("20.00"), shipping_total=Decimal("9.95"),
        grand_total=Decimal("29.95"), currency="AUD", status=status,
    )
    session.add(order)
    session.flush()
    session.add(ShopOrderItem(order_id=order.id, listing_id="listing-1", title="Charizard ex",
                              unit_price=Decimal("20.00"), quantity=1))
    session.commit()
    return order


def test_pack_assistant_link_shown_for_a_paid_order(client, session):
    order = make_order(session, status=ShopOrderStatus.PAID)
    body = client.get("/shop-orders").text
    assert f"/shop-orders/{order.id}/pack-sheet" in body


def test_pack_sheet_shows_set_and_card_details(client, session):
    order = make_order_with_real_card(session)
    body = client.get(f"/shop-orders/{order.id}/pack-sheet").text
    assert "Obsidian Flames" in body
    assert "#125" in body
    assert "Rare Ultra" in body


def test_pack_sheet_shows_the_stored_location(client, session):
    order = make_order_with_real_card(session, location="Binder A")
    body = client.get(f"/shop-orders/{order.id}/pack-sheet").text
    assert "Pick location" in body
    assert "Binder A" in body


def test_pack_sheet_flags_an_unlocated_card(client, session):
    order = make_order_with_real_card(session)
    body = client.get(f"/shop-orders/{order.id}/pack-sheet").text
    assert "Not located yet" in body


def test_pack_sheet_handles_a_line_with_no_physical_item(client, session):
    """A bulk-lot line (listing_id=None) has nothing to individually
    locate -- the page should say so, not error."""
    order = make_order(session, status=ShopOrderStatus.PAID)
    resp = client.get(f"/shop-orders/{order.id}/pack-sheet")
    assert resp.status_code == 200
    assert "nothing here to individually locate" in resp.text.lower()


def test_pack_sheet_404s_for_an_unknown_order(client):
    assert client.get("/shop-orders/does-not-exist/pack-sheet").status_code == 404


def test_pack_sheet_locates_form_posts_to_the_shared_location_route(client, session):
    """The actual set/get-or-create write lives centrally at
    /inventory/set-location (see test_inventory_review.py) so both this
    page and ebay_sales.py's pack assistant share one implementation --
    this just pins that the pack sheet's form points there with a back
    link that returns to this same order."""
    order = make_order_with_real_card(session)
    body = client.get(f"/shop-orders/{order.id}/pack-sheet").text
    assert "action='/inventory/set-location'" in body
    assert f"/shop-orders/{order.id}/pack-sheet" in body
