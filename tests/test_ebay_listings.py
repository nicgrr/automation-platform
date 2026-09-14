"""The /ebay-listings page: showing what the previous route couldn't.

Route-level, unlike the adapter tests -- these check what a human sees on
the page and what actually lands in the shared EbayListing table, which
is also read by /api/ebay/listings' Sell API sync. Keeping the two
sources apart in that shared table is the thing most worth a test here.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading import ActiveListing
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.ebay_oauth import OAuthError
from automation_control.models import EbayListing

_FAKE_SERVICES = ("cipher", "oauth")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "nic"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _listing(item_id="111", title="Test Item", price="12.50", qty=1) -> ActiveListing:
    from decimal import Decimal
    return ActiveListing(ebay_item_id=item_id, title=title, price=Decimal(price), currency="AUD",
                         quantity=qty, quantity_sold=0, listing_type="FixedPriceItem",
                         view_item_url=f"https://x/{item_id}", start_time=None)


def test_never_checked_says_so_plainly(client):
    page = client.get("/ebay-listings").text
    assert "Never checked" in page
    assert "Nothing cached yet" in page


def test_a_successful_refresh_shows_the_listing_and_caches_it(client, session):
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing()])
        resp = client.post("/ebay-listings/refresh", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/ebay-listings?refreshed=1"

    page = client.get("/ebay-listings").text
    assert "Test Item" in page
    assert "AUD $12.50" in page
    assert "1 active listing" in page

    row = session.query(EbayListing).one()
    assert row.ebay_listing_id == "111"
    assert row.raw_snapshot["source"] == "trading_api"


def test_a_failed_refresh_shows_the_real_error_not_a_traceback(client):
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(
            side_effect=EbayApiError("eBay Trading API request failed: token expired"))
        resp = client.post("/ebay-listings/refresh", follow_redirects=True)

    assert resp.status_code == 200
    assert "token expired" in resp.text


def test_not_connected_is_reported_not_raised(client):
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token",
              new=AsyncMock(side_effect=OAuthError("eBay Production is not connected"))):
        resp = client.post("/ebay-listings/refresh", follow_redirects=True)

    assert resp.status_code == 200
    assert "not connected" in resp.text


def test_a_second_refresh_updates_price_rather_than_duplicating_the_row(client, session):
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(price="10.00")])
        client.post("/ebay-listings/refresh")
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(price="15.00")])
        client.post("/ebay-listings/refresh")

    rows = session.query(EbayListing).all()
    assert len(rows) == 1
    assert rows[0].current_price_minor == 1500


def test_a_delisted_item_stops_appearing_on_the_page_but_the_row_stays(client, session):
    """A listing that ends is simply absent from the next refresh's
    results -- there is no explicit "ended" signal from this call to act
    on, so the row is left as the last-known state rather than guessed at
    or deleted."""
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing("a"), _listing("b")])
        client.post("/ebay-listings/refresh")
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing("a")])
        client.post("/ebay-listings/refresh")

    assert session.query(EbayListing).count() == 2  # row for "b" untouched, not deleted


def test_a_sell_api_row_does_not_appear_on_this_page(client, session):
    """/api/ebay/listings writes into the same table with no source tag.
    This page must only ever show rows it wrote itself, or the two
    read paths would silently blend."""
    session.add(EbayListing(ebay_listing_id="sell-api-row", title="From the other sync",
                            currency="AUD", current_price_minor=999, listing_status="ACTIVE",
                            raw_snapshot={}))
    session.commit()

    page = client.get("/ebay-listings").text
    assert "From the other sync" not in page
    assert "Never checked" in page


def test_the_view_item_link_points_at_the_real_ebay_listing(client):
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing("267785450003")])
        client.post("/ebay-listings/refresh")

    page = client.get("/ebay-listings").text
    assert "href='https://x/267785450003'" in page


def test_settings_links_to_the_page_when_checked(client, session):
    session.add(EbayListing(ebay_listing_id="a", title="X", currency="AUD",
                            current_price_minor=100, listing_status="Active",
                            raw_snapshot={"source": "trading_api"}))
    session.commit()

    from unittest.mock import patch as p
    with p("automation_control.settings_page.configured", return_value=True), \
         p("automation_control.settings_page.configured_for_writes", return_value=True):
        page = client.get("/settings").text

    assert "/ebay-listings" in page
