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


def test_refresh_preserves_imported_bundle_details(client, session):
    session.add(EbayListing(
        ebay_listing_id="111", title="Legacy bundle", currency="AUD",
        current_price_minor=1000, listing_status="Active", quantity=1,
        raw_snapshot={
            "source": "trading_api", "seller_reference": "NIC-10",
            "bundle_contents": [{
                "name": "Zapdos ex", "set": "Black Star Promos",
                "number": "SVP 049", "rarity": "Promo",
            }],
            "inventory_overlap_review": {"status": "confirmed_distinct"},
        },
    ))
    session.commit()

    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(price="15.00")])
        client.post("/ebay-listings/refresh")

    row = session.query(EbayListing).one()
    assert row.current_price_minor == 1500
    assert row.raw_snapshot["seller_reference"] == "NIC-10"
    assert row.raw_snapshot["bundle_contents"][0]["number"] == "SVP 049"
    assert row.raw_snapshot["inventory_overlap_review"]["status"] == "confirmed_distinct"

    list_page = client.get("/ebay-listings").text
    assert "href='/ebay-listings/111'" in list_page
    assert "1 cards" in list_page  # the tile's bundle-size flag

    detail_page = client.get("/ebay-listings/111").text
    assert "1 exact card imported" in detail_page
    assert "Zapdos ex" in detail_page
    assert "SVP 049" in detail_page
    assert "Confirmed as separate physical copies" in detail_page


# --- reconciliation by absence (2026-09-27) -- GetMyeBaySelling's
# ActiveList only ever reports what's still active, so a listing that
# fell off it (ended, sold through) needs the fetch that no longer sees
# it to be the thing that notices, not another separate check. Real
# incident this closes: 7 listings ended by hand on eBay stayed "Active"
# in this cache through a real refresh, and kept blocking re-export for
# good since ebay_bulk_export.py's _active_variation_parents treats
# "Active" as gospel. ------------------------------------------------

def test_a_listing_missing_from_a_fresh_fetch_is_marked_ended(client, session):
    session.add(EbayListing(ebay_listing_id="222", title="Old Item", currency="AUD",
                            current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        # The fresh ActiveList has one real listing on it -- "222" simply
        # isn't among them, exactly like a listing the seller ended.
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(item_id="111")])
        client.post("/ebay-listings/refresh")

    row = session.query(EbayListing).filter_by(ebay_listing_id="222").one()
    assert row.listing_status == "Ended"


def test_an_ended_listing_is_never_resurrected_by_a_later_fetch_still_not_seeing_it(client, session):
    """Once marked Ended, a row must not flip back and forth on every
    refresh -- only actually reappearing in a fresh ActiveList should
    ever make something Active again."""
    session.add(EbayListing(ebay_listing_id="222", title="Old Item", currency="AUD",
                            current_price_minor=1000, listing_status="Ended", quantity=1))
    session.commit()

    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(item_id="111")])
        client.post("/ebay-listings/refresh")

    row = session.query(EbayListing).filter_by(ebay_listing_id="222").one()
    assert row.listing_status == "Ended"


def test_an_empty_fresh_fetch_reconciles_nothing_rather_than_ending_everything(client, session):
    """A zero-length ActiveList is far more likely a transient fetch
    problem than every listing ending at once -- treating it as gospel
    would risk wiping out a whole account's Active status over one bad
    response."""
    session.add(EbayListing(ebay_listing_id="222", title="Old Item", currency="AUD",
                            current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[])
        client.post("/ebay-listings/refresh")

    row = session.query(EbayListing).filter_by(ebay_listing_id="222").one()
    assert row.listing_status == "Active"


def test_a_listing_still_present_stays_active_and_is_not_touched_by_reconciliation(client, session):
    session.add(EbayListing(ebay_listing_id="111", title="Old Title", currency="AUD",
                            current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing(item_id="111", price="20.00")])
        client.post("/ebay-listings/refresh")

    row = session.query(EbayListing).filter_by(ebay_listing_id="111").one()
    assert row.listing_status == "Active"
    assert row.current_price_minor == 2000


def test_a_delisted_item_stops_appearing_on_the_page_but_the_row_stays(client, session):
    """A listing that ends is simply absent from the next refresh's
    results -- there is no explicit "ended" signal from this call, only
    absence to reconcile against (see the tests above this one). Either
    way the row itself is kept, not deleted, so its history stays
    intact -- only listing_status changes underneath it."""
    with patch("automation_control.ebay_listings.ebay_services", return_value=_FAKE_SERVICES), \
         patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing("a"), _listing("b")])
        client.post("/ebay-listings/refresh")
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[_listing("a")])
        client.post("/ebay-listings/refresh")

    assert session.query(EbayListing).count() == 2  # row for "b" untouched, not deleted
    page = client.get("/ebay-listings").text
    assert "Test Item" in page
    assert "href='/ebay-listings/b'" not in page
    assert "1 active listing" in page


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

    list_page = client.get("/ebay-listings").text
    assert "href='/ebay-listings/267785450003'" in list_page

    detail_page = client.get("/ebay-listings/267785450003").text
    assert "href='https://x/267785450003'" in detail_page


# --- tile grid + detail page --------------------------------------------

def test_a_listing_with_a_cached_photo_shows_it_on_the_tile(client, session):
    session.add(EbayListing(
        ebay_listing_id="1", title="Pikachu", currency="AUD", current_price_minor=500,
        listing_status="Active", quantity=1,
        raw_snapshot={"source": "trading_api", "image_urls": ["https://i.ebayimg.com/pika.jpg"]},
    ))
    session.commit()

    page = client.get("/ebay-listings").text

    assert "src='https://i.ebayimg.com/pika.jpg'" in page
    assert "No photo cached" not in page


def test_rendered_tiles_are_capped_at_200_but_stats_still_cover_everything(client, session):
    """Matches shop_listings_admin.py's own pattern: the individual cards
    rendered on the page are capped, but the aggregate stats above them
    (Active listings count, total value) must still reflect every row --
    those numbers going wrong because of a rendering cap would be worse
    than the cap itself."""
    for i in range(205):
        session.add(EbayListing(ebay_listing_id=str(i), title=f"Card {i}", currency="AUD",
                                current_price_minor=100, listing_status="Active", quantity=1,
                                raw_snapshot={"source": "trading_api"}))
    session.commit()

    page = client.get("/ebay-listings").text

    assert page.count("class='ebay-tile'") == 200
    assert "205 of 200 shown" not in page  # wrong order would read backwards
    assert "200 of 205 shown, capped at 200 rows." in page
    assert "<div class='value'>205</div>" in page  # the stats total, uncapped


def test_a_listing_with_no_cached_photo_shows_a_placeholder(client, session):
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active", quantity=1,
                            raw_snapshot={"source": "trading_api"}))
    session.commit()

    page = client.get("/ebay-listings").text

    assert "No photo cached" in page


def test_detail_page_shows_every_cached_photo_not_just_the_first(client, session):
    session.add(EbayListing(
        ebay_listing_id="1", title="Bundle", currency="AUD", current_price_minor=1000,
        listing_status="Active", quantity=1,
        raw_snapshot={"source": "trading_api", "image_urls": ["https://x/a.jpg", "https://x/b.jpg", "https://x/c.jpg"]},
    ))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert page.count("<img src=") == 3
    assert "https://x/a.jpg" in page and "https://x/b.jpg" in page and "https://x/c.jpg" in page


def test_detail_page_shows_price_qty_and_sold(client, session):
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=1250, listing_status="Active", quantity=3,
                            raw_snapshot={"source": "trading_api", "quantity_sold": 7}))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert "AUD $12.50" in page
    assert ">3<" in page
    assert ">7<" in page


def test_detail_page_notes_it_is_read_only(client, session):
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active",
                            raw_snapshot={"source": "trading_api"}))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert "no way to edit a live listing from here yet" in page


def test_detail_page_404s_for_an_unknown_listing(client):
    assert client.get("/ebay-listings/does-not-exist").status_code == 404


def test_detail_page_404s_for_a_sell_api_sourced_row(client, session):
    """The Sell API sync writes into the same shared table with a
    different `source` tag -- this route is scoped to trading_api rows
    only, same guard `_cached_rows` already applies to the list page."""
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active",
                            raw_snapshot={"source": "sell_api"}))
    session.commit()

    assert client.get("/ebay-listings/1").status_code == 404


def test_detail_page_shows_a_cached_description_in_a_sandboxed_frame(client, session):
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active",
                            raw_snapshot={"source": "trading_api", "description": "<p>Near mint</p>"}))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert "sandbox=\"\"" in page
    assert "Near mint" in page
    assert "No description cached" not in page


def test_detail_page_says_when_no_description_is_cached(client, session):
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active",
                            raw_snapshot={"source": "trading_api"}))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert "No description cached for this listing yet" in page


def test_a_malicious_description_cannot_break_out_of_the_srcdoc_attribute(client, session):
    """A description containing a literal double-quote must not be able
    to close the srcdoc="..." attribute early and inject an attribute of
    its own onto the iframe tag."""
    session.add(EbayListing(ebay_listing_id="1", title="Pikachu", currency="AUD",
                            current_price_minor=500, listing_status="Active",
                            raw_snapshot={"source": "trading_api",
                                         "description": '"><script>alert(1)</script>'}))
    session.commit()

    page = client.get("/ebay-listings/1").text

    assert "<script>alert(1)</script>" not in page
    assert "&quot;&gt;&lt;script&gt;" in page


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


@pytest.mark.parametrize("total,sold,remaining", [(2, 1, 1), (10, 1, 9), (2, 2, 0), (1, 2, 0), (None, 0, None)])
def test_refresh_caches_remaining_stock_without_repeated_subtraction(session, total, sold, remaining):
    from dataclasses import replace
    from automation_control.ebay_listings import _upsert
    row = replace(_listing(), quantity=total, quantity_sold=sold)
    for _ in range(2):
        _upsert(session, [row])
        saved = session.query(EbayListing).one()
        assert saved.quantity == remaining
        assert saved.raw_snapshot["quantity_total"] == total
        assert saved.raw_snapshot["quantity_sold"] == sold
