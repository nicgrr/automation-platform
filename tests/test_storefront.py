from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CardSet, CatalogItem, CatalogItemType, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, TcgCard,
)
from storefront.app import app


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ShippingRate(zone=ShippingZone.AU, label="Australia — tracked", price=Decimal("9.95"), free_over=Decimal("150")))
        db.add(ShippingRate(zone=ShippingZone.INTERNATIONAL, label="International", price=Decimal("29.95")))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _listing(session, *, title="Charizard #4", price="80.00", status=ShopListingStatus.ACTIVE, quantity=1):
    session.add(CatalogItem(id="c1", item_type=CatalogItemType.TCG_CARD, name="Charizard"))
    session.add(TcgCard(catalog_item_id="c1", game="pokemon", set_id="sv1", number="4"))
    listing = ShopListing(catalog_item_id="c1", title=title, price=Decimal(price), quantity=quantity, status=status)
    session.add(listing)
    session.commit()
    return listing


def _lot(session, *, cards=185, price="105.47", status=ShopListingStatus.ACTIVE):
    session.add(CardSet(id="sv1", name="Scarlet & Violet"))
    lot = BulkLot(set_id="sv1", title=f"Scarlet & Violet — bulk lot ({cards} cards)",
                  card_count=cards, market_value=Decimal("234.38"), price=Decimal(price), status=status)
    session.add(lot)
    session.commit()
    return lot


# --- the isolation that matters most ---------------------------------------

def test_health_checks_database_without_exposing_shop_data(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_storefront_exposes_no_admin_routes(client):
    """The whole reason the shop is a separate app: a public visitor must
    not be able to reach inventory, scanning, or credentials, by
    construction rather than by an auth check that could be misconfigured."""
    for path in ["/dashboard", "/inventory", "/api/ebay/status", "/collectibles", "/deal-check", "/export"]:
        assert client.get(path).status_code == 404, path


def test_no_api_docs_are_published(client):
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404, path


# --- browsing ---------------------------------------------------------------

def test_home_shows_active_listings(client, session):
    _listing(session)
    body = client.get("/").text
    assert "Charizard #4" in body
    assert "$80.00" in body


def test_draft_listings_are_never_public(client, session):
    _listing(session, title="Not ready yet", status=ShopListingStatus.DRAFT)
    assert "Not ready yet" not in client.get("/").text
    assert "Not ready yet" not in client.get("/browse").text


def test_search_filters_listings(client, session):
    _listing(session, title="Charizard #4")
    assert "Charizard" in client.get("/browse?q=chari").text
    assert "Charizard" not in client.get("/browse?q=blastoise").text


def test_item_detail_404s_for_a_withdrawn_listing(client, session):
    listing = _listing(session, status=ShopListingStatus.WITHDRAWN)
    assert client.get(f"/item/{listing.id}").status_code == 404


# --- bulk master ------------------------------------------------------------

def test_bulk_master_lists_lots(client, session):
    _lot(session)
    body = client.get("/bulk").text
    assert "Scarlet &amp; Violet — bulk lot (185 cards)" in body
    assert "$105.47" in body


def _grid_titles(html: str) -> list[str]:
    """Titles from the product grid only. The nav drawer also lists set
    names on every page, so asserting against the whole document would
    pass or fail for the wrong reasons."""
    import re
    return re.findall(r"<div class='card-title'>(.*?)</div>", html)


def test_bulk_filters_narrow_results(client, session):
    _lot(session, cards=185, price="105.47")
    session.add(BulkLot(set_id=None, title="Tiny lot (6 cards)", card_count=6,
                        market_value=Decimal("10"), price=Decimal("12.00"),
                        status=ShopListingStatus.ACTIVE))
    session.commit()

    assert any("Tiny lot" in t for t in _grid_titles(client.get("/bulk").text))
    # too few cards
    assert not any("Tiny lot" in t for t in _grid_titles(client.get("/bulk?min_cards=50").text))
    # too dear
    assert not any("Scarlet" in t for t in _grid_titles(client.get("/bulk?max_price=20").text))


def test_bulk_detail_shows_the_saving_against_market(client, session):
    lot = _lot(session)
    body = client.get(f"/bulk/{lot.id}").text
    assert "185" in body
    assert "under market" in body


# --- cart -------------------------------------------------------------------

def test_add_single_then_see_it_with_au_postage(client, session):
    listing = _listing(session, price="80.00")
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    body = client.get("/cart").text
    assert "$80.00" in body
    assert "$9.95" in body       # AU postage
    assert "$89.95" in body      # total


def test_international_zone_charges_the_higher_rate(client, session):
    listing = _listing(session, price="80.00")
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    body = client.get("/cart?zone=international").text
    assert "$29.95" in body
    assert "$109.95" in body


def test_free_shipping_applies_over_the_threshold(client, session):
    listing = _listing(session, price="160.00")
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    body = client.get("/cart").text
    assert "free over" in body
    assert "$160.00" in body


def test_a_bulk_lot_can_be_carted_alongside_singles(client, session):
    listing = _listing(session, price="80.00")
    lot = _lot(session, price="105.47")
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    client.post("/cart/add", data={"listing_id": f"B:{lot.id}"}, follow_redirects=False)
    body = client.get("/cart").text
    assert "Charizard #4" in body
    assert "bulk lot" in body
    assert "$185.47" in body  # 80.00 + 105.47, one postage charge


def test_removing_a_line_empties_the_cart(client, session):
    listing = _listing(session)
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    client.post("/cart/remove", data={"listing_id": listing.id}, follow_redirects=False)
    assert "Nothing in here yet" in client.get("/cart").text


def test_cannot_add_something_that_is_not_for_sale(client, session):
    listing = _listing(session, status=ShopListingStatus.DRAFT)
    assert client.post("/cart/add", data={"listing_id": listing.id}).status_code == 404


def test_a_withdrawn_item_silently_drops_out_of_an_existing_cart(client, session):
    """A cart is a cookie; the database is the truth. Something pulled from
    sale after it was added must not appear at checkout."""
    listing = _listing(session)
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    listing.status = ShopListingStatus.WITHDRAWN
    session.commit()
    assert "Nothing in here yet" in client.get("/cart").text


def test_a_tampered_cart_cookie_is_ignored_not_fatal(client):
    client.cookies.set("nlv_cart", "not-json{{{")
    assert client.get("/cart").status_code == 200
