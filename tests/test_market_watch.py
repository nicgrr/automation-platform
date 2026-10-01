from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.market_watch import UNMATCHED_SET, grouped_market, listing_set
from automation_control.market_watch_service import classify_item_kind, landed_cost_aud, upsert_listing
from automation_control.models import MarketAlertImport, MarketListing, MarketListingMatch, MarketWatchTerm


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'watch.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "tester"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_classifies_separate_product_shapes():
    assert classify_item_kind("Sonny Angel unopened blind box") == "blind_box"
    assert classify_item_kind("Sonny Angel full case of 12") == "full_case"
    assert classify_item_kind("Sonny Angel Robby figure") == "individual"
    assert classify_item_kind("12x Sonny Angel Dreaming Hippers blind box") == "full_case"
    assert classify_item_kind("Anteater Sonny Angel") == "individual"


def test_landed_cost_includes_shipping_fx_and_tax():
    tax, total = landed_cost_aud("20", "5", "1.5", Decimal("0.10"))
    assert tax == Decimal("3.75")
    assert total == Decimal("41.25")


def test_upsert_deduplicates_market_listing(session):
    first, created = upsert_listing(session, source="ebay", external_id="123", title="Sonny Angel Robby figure",
                                    url="https://example.test/123", price="10", shipping="2", currency="AUD", country="AU")
    again, created_again = upsert_listing(session, source="ebay", external_id="123", title="Sonny Angel Robby figure",
                                          url="https://example.test/123", price="9", shipping="2", currency="AUD", country="AU")
    assert created and not created_again and first.id == again.id
    assert session.query(MarketListing).count() == 1
    assert again.landed_aud == Decimal("11.00")


def test_dashboard_shows_images_and_landed_cost(session):
    upsert_listing(session, source="ebay", external_id="x", title="Sonny Angel Peach figure",
                   url="https://example.test/x", image_url="https://img.example.test/x.jpg",
                   price="30", shipping="5", currency="AUD", country="AU")
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "tester"
    try:
        response = TestClient(app).get("/market-watch")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert "Sonny Angel Peach figure" in response.text
    assert "A$35.00 landed" in response.text
    assert "https://img.example.test/x.jpg" in response.text


def test_groups_character_under_its_canonical_set(session):
    item, _ = upsert_listing(session, source="ebay", external_id="cream", title="Sonny Angel Cream Puff figure Sweets Series",
                             url="https://example.test/cream", price="20", currency="AUD", country="AU")
    assert listing_set(item) == "Sweets Series"
    assert grouped_market([item])["Sweets Series"]["individual"] == [item]


def test_unrecognised_title_stays_in_review_group(session):
    item, _ = upsert_listing(session, source="ebay", external_id="mystery", title="Sonny Angel mystery collectible",
                             url="https://example.test/mystery", price="20", currency="AUD", country="AU")
    assert listing_set(item) == UNMATCHED_SET


def test_dashboard_shows_set_median_and_range(session):
    for number, price in enumerate(("20", "30", "100")):
        upsert_listing(session, source="ebay", external_id=f"sweet-{number}",
                       title=f"Sonny Angel Sweets Series Cream Puff figure {number}",
                       url=f"https://example.test/{number}", price=price, currency="AUD", country="AU")
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "tester"
    try:
        response = TestClient(app).get("/market-watch")
    finally:
        app.dependency_overrides.clear()
    assert "Sweets Series" in response.text
    assert "A$30.00" in response.text
    assert "median · 3 listings" in response.text
    assert "range A$20.00–A$100.00" in response.text


def test_watch_is_restricted_to_australia_and_japan(client, session):
    for country in ("AU", "JP", "US"):
        upsert_listing(
            session, source="ebay", external_id=country,
            title=f"Sonny Angel {country} figure", url=f"https://example.test/{country}",
            price="20", currency="AUD", country=country,
        )
    session.commit()

    body = client.get("/market-watch").text

    assert "Sonny Angel AU figure" in body
    assert "Sonny Angel JP figure" in body
    assert "Sonny Angel US figure" not in body
    assert "Australia &amp; Japan" in body


def test_country_filter_can_show_japan_only(client, session):
    for country in ("AU", "JP"):
        upsert_listing(
            session, source="ebay", external_id=country,
            title=f"Sonny Angel {country} figure", url=f"https://example.test/{country}",
            price="20", currency="AUD", country=country,
        )
    session.commit()

    body = client.get("/market-watch?country=JP").text

    assert "Sonny Angel JP figure" in body
    assert "Sonny Angel AU figure" not in body


def test_manual_facebook_find_enters_the_same_market_watch(client, session):
    response = client.post("/market-watch/listings", data={
        "source": "facebook", "url": "https://www.facebook.com/marketplace/item/123",
        "title": "Sonny Angel Facebook figure", "country": "AU",
        "price": "30", "shipping": "5", "currency": "AUD",
    }, follow_redirects=False)

    assert response.status_code == 303
    listing = session.scalar(select(MarketListing).where(MarketListing.source == "facebook"))
    assert listing is not None
    assert listing.country == "AU"
    assert listing.landed_aud == Decimal("35.00")


def test_manual_listing_rejects_wrong_domain_and_country(client, session):
    response = client.post("/market-watch/listings", data={
        "source": "depop", "url": "https://example.test/fake",
        "title": "Fake", "country": "US", "price": "30", "shipping": "0", "currency": "AUD",
    }, follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert session.query(MarketListing).count() == 0


def test_manual_japan_find_converts_jpy_to_aud(client, session):
    with patch("automation_control.market_watch.exchange_rate_to_aud", new=AsyncMock(return_value=Decimal("0.01"))):
        response = client.post("/market-watch/listings", data={
            "source": "depop", "url": "https://www.depop.com/products/example-listing",
            "title": "Sonny Angel Japan figure", "country": "JP",
            "price": "2000", "shipping": "500", "currency": "JPY",
        }, follow_redirects=False)

    assert response.status_code == 303
    listing = session.scalar(select(MarketListing).where(MarketListing.source == "depop"))
    assert listing.landed_aud == Decimal("25.00")


def test_refresh_button_runs_the_country_limited_ebay_collector(client):
    with patch("scripts.scan_collectible_market.run", new=AsyncMock(return_value=0)) as run:
        response = client.post("/market-watch/refresh", follow_redirects=False)

    assert response.status_code == 303
    run.assert_awaited_once_with(20, False, False, None)


def test_can_create_pause_and_resume_a_generic_collectible_watch(client, session):
    response = client.post("/market-watch/watches", data={
        "name": "Dragon Ball Carddass",
        "query": "Dragon Ball Carddass",
        "category": "Cards",
        "countries": ["AU", "JP"],
        "sources": ["ebay", "public_web"],
        "max_landed_aud": "120.50",
    }, follow_redirects=False)

    assert response.status_code == 303
    watch = session.scalar(select(MarketWatchTerm).where(MarketWatchTerm.name == "Dragon Ball Carddass"))
    assert watch is not None
    assert watch.countries == ["AU", "JP"]
    assert watch.sources == ["ebay", "public_web"]
    assert watch.max_landed_aud == Decimal("120.50")

    paused = client.post(f"/market-watch/watches/{watch.id}/toggle", follow_redirects=False)
    session.refresh(watch)
    assert paused.status_code == 303
    assert watch.active is False

    client.post(f"/market-watch/watches/{watch.id}/toggle", follow_redirects=False)
    session.refresh(watch)
    assert watch.active is True

    updated = client.post(f"/market-watch/watches/{watch.id}", data={
        "name": "Dragon Ball vintage",
        "query": "Dragon Ball Carddass vintage",
        "category": "Vintage cards",
        "countries": ["JP"],
        "sources": ["ebay"],
        "max_landed_aud": "",
    }, follow_redirects=False)
    session.refresh(watch)
    assert updated.status_code == 303
    assert watch.name == "Dragon Ball vintage"
    assert watch.countries == ["JP"]
    assert watch.sources == ["ebay"]
    assert watch.max_landed_aud is None


def test_watch_creation_rejects_locations_outside_australia_and_japan(client, session):
    response = client.post("/market-watch/watches", data={
        "name": "Bad scope", "query": "bad", "countries": ["US"], "sources": ["ebay"],
    }, follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert session.scalar(select(MarketWatchTerm).where(MarketWatchTerm.name == "Bad scope")) is None


def test_share_page_prefills_a_facebook_listing(client):
    response = client.get(
        "/market-watch/share",
        params={
            "title": "Rare card",
            "url": "https://www.facebook.com/marketplace/item/321",
        },
    )

    assert response.status_code == 200
    assert "Share to EzBay" in response.text
    assert "value='facebook' selected" in response.text
    assert "value='Rare card'" in response.text
    assert "facebook.com/marketplace/item/321" in response.text


def test_alert_email_imports_gumtree_and_buyee_links_and_deduplicates(client, session):
    watch = MarketWatchTerm(
        name="Sonny Angel", query="Sonny Angel", countries=["AU", "JP"], sources=["ebay"], active=True,
    )
    session.add(watch)
    session.commit()
    raw = b"""From: alerts@example.test\r
To: collector@example.test\r
Subject: New Sonny Angel listings\r
Message-ID: <market-alert-1@example.test>\r
Content-Type: text/html; charset=utf-8\r
\r
<a href=\"https://www.gumtree.com.au/s-ad/sydney/collectables/sonny/123\">Sonny Angel Peach A$45.00</a>
<a href=\"https://buyee.jp/item/yahoo/auction/abc\">Sonny Angel Robby JPY 3,000</a>
"""

    with patch("automation_control.market_alert_import.exchange_rate_to_aud", new=AsyncMock(return_value=Decimal("0.01"))):
        first = client.post(
            "/market-watch/alerts/import", data={"country": "AU"},
            files={"email_file": ("alert.eml", raw, "message/rfc822")}, follow_redirects=False,
        )
        second = client.post(
            "/market-watch/alerts/import", data={"country": "AU"},
            files={"email_file": ("alert.eml", raw, "message/rfc822")}, follow_redirects=False,
        )

    assert first.status_code == second.status_code == 303
    listings = session.scalars(select(MarketListing).order_by(MarketListing.source)).all()
    assert [(row.source, row.country) for row in listings] == [("buyee", "JP"), ("gumtree", "AU")]
    assert listings[0].price == Decimal("3000.00")
    assert listings[1].price == Decimal("45.00")
    assert session.query(MarketAlertImport).count() == 1
    assert session.query(MarketListingMatch).count() == 2
    assert "already+imported" in second.headers["location"]


def test_manual_shared_listing_can_be_saved_without_a_known_price(client, session):
    response = client.post("/market-watch/listings", data={
        "source": "facebook",
        "url": "https://www.facebook.com/marketplace/item/999",
        "title": "Unknown-price collectible",
        "country": "AU",
        "price": "",
        "shipping": "0",
        "currency": "AUD",
    }, follow_redirects=False)

    assert response.status_code == 303
    listing = session.scalar(select(MarketListing).where(MarketListing.source == "facebook"))
    assert listing.price == Decimal("0.00")
    assert listing.raw["price_available"] is False
    assert "Price not supplied" in client.get("/market-watch").text
