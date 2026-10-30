from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.bundle_singles import BATCH_ID
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice, CardVariant, CatalogItem, CatalogItemType, InventoryItem, InventoryPlacement,
    ListingPhotoBatch, ListingPhotoPair, ListingPhotoPairStatus, ShopListing, ShopListingStatus,
    StorageLocation,
)


def _card(session, position, name, price, *, matched=True, inventory=True, low=None, median=None,
          slot=None, status=ShopListingStatus.ACTIVE):
    cid = f"cat-{position}"
    session.add(CatalogItem(id=cid, item_type=CatalogItemType.TCG_CARD, name=name))
    if inventory:
        session.add(InventoryItem(
            id=f"inv-{position}", catalog_item_id=cid, variant=CardVariant.HOLO,
            condition="Near Mint", quantity=1,
        ))
    session.add(ShopListing(
        id=f"listing-{position}", catalog_item_id=cid,
        inventory_item_id=f"inv-{position}" if inventory else None,
        title=f"{name} {position}/100", condition="Near Mint", price=Decimal(price),
        currency="AUD", quantity=1, status=status,
    ))
    session.flush()
    pair = ListingPhotoPair(
        id=f"pair-{position}", batch_id=BATCH_ID, position=position,
        front_image_path=f"front-{position}.jpg", back_image_path="back.jpg",
        matched_shop_listing_id=f"listing-{position}" if matched else None,
        status=ListingPhotoPairStatus.READY,
    )
    session.add(pair)
    session.flush()
    now = datetime.now(UTC)
    for source, value in (("ebay_active_low", low), ("ebay_active_median", median)):
        if value is not None:
            session.add(CardPrice(card_id=cid, source=source, price=Decimal(value), currency="AUD", fetched_at=now))
    if slot:
        location = StorageLocation(name=slot, kind="card_slot")
        session.add(location)
        session.flush()
        session.add(InventoryPlacement(
            inventory_item_id=f"inv-{position}" if inventory else None,
            listing_photo_pair_id=pair.id, storage_location_id=location.id, quantity=1,
        ))


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ListingPhotoBatch(id=BATCH_ID, total_pairs=5))
        _card(session, 1, "Alcremie", "9.85", low="5.57", median="9.38")           # above median
        _card(session, 3, "Probopass", "3.99", low="4.50", median="5.61", slot="PK-01-001")  # below low
        _card(session, 4, "Gothitelle", "7.99", matched=False, slot="PK-01-002")   # unmatched, via placement
        _card(session, 5, "Kadabra", "6.00", low="5.00", median="7.00")            # within range
        session.add(ListingPhotoPair(
            id="pair-2", batch_id=BATCH_ID, position=2, front_image_path="wrap.jpg",
            status=ListingPhotoPairStatus.NEEDS_REVIEW,
        ))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()


def test_list_shows_every_card_and_skips_reference_photos(client):
    html = client.get("/bundle-singles").text
    for position in (1, 3, 4, 5):
        assert f"href='/bundle-singles/{position}'" in html
    assert "href='/bundle-singles/2'" not in html
    assert "4 cards split out" in html
    assert "Start with the first card" in html


def test_price_position_against_the_ebay_range(client):
    html = client.get("/bundle-singles").text
    assert html.count("Above eBay median") == 1
    assert html.count("Below eBay low") == 1
    assert html.count("Within eBay range") == 1
    assert html.count("No eBay data") == 1


def test_a_pair_the_recognition_could_not_match_still_resolves_through_its_inventory(client):
    html = client.get("/bundle-singles/4").text
    assert "Gothitelle 4/100" in html
    assert "PK-01-002" in html
    assert "reserved" in html


def test_step_view_links_edit_preview_and_neighbours(client):
    html = client.get("/bundle-singles/3").text
    assert "/shop-listings/listing-3/edit" in html
    assert "/shop-listings/listing-3/ebay-preview" in html
    assert "href='/bundle-singles/1'" in html and "href='/bundle-singles/4'" in html
    assert "2 of 4" in html
    assert "$3.99" in html and "$5.61" in html


def test_first_and_last_cards_have_no_dead_end_links(client):
    first = client.get("/bundle-singles/1").text
    last = client.get("/bundle-singles/5").text
    assert "Previous" not in first and "Next" in first
    assert "Next" not in last and "Previous" in last


def test_listing_without_inventory_is_flagged_not_hidden(client):
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    session = app.dependency_overrides[get_session]()
    session.get(ShopListing, "listing-5").inventory_item_id = None
    session.commit()
    assert "no inventory" in client.get("/bundle-singles/5").text


def test_unknown_or_reference_positions_are_404(client):
    assert client.get("/bundle-singles/2").status_code == 404
    assert client.get("/bundle-singles/99").status_code == 404


def test_pages_require_login():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/bundle-singles", follow_redirects=False)
    assert response.status_code in (302, 303, 307, 401)
