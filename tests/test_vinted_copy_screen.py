from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import unquote

import pytest
from sqlalchemy import select

import automation_control.vinted_listings as screens
from automation_control.models import InventoryItem, InventoryStatus, PlatformConfig, VintedListing
from test_vinted_listings import URL, context


NOW = datetime(2026, 11, 1, 0, 15, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(screens, "_now_utc", lambda: NOW)


def test_copy_screen_includes_copy_all_and_original_photo_actions(context, clock):
    session, client = context
    saved = session.get(PlatformConfig, "vinted_draft:i-inv")
    saved.value = {**saved.value, "hold_reason": "Internal identification reminder", "suggested_price": "35.99"}
    session.commit()
    body = client.get("/collectibles/vinted/i-inv/post").text
    assert "data-copy='v-title'" in body
    assert "data-copy='v-desc'" in body
    assert "data-copy='v-all'>Copy all details" in body
    all_copy = unescape(body.split("id='v-all'", 1)[1].split(">", 1)[1].split("</textarea>", 1)[0])
    assert "Sonny Angel Cat Life Persian" in all_copy
    assert "Opened figure." in all_copy
    assert "Price: AUD 14.99 (postage extra)" in all_copy
    assert "35.99" not in all_copy
    assert "Internal identification reminder" not in all_copy
    assert "href='/collectibles/vinted/i-inv/photo/0'" in body
    assert "Open photo 1" in body
    assert "href='/collectibles/vinted/i-inv/photo/0/download' download" in body
    assert "Download original" in body
    assert "measure and weigh the packed parcel" in body
    assert "small for a single figure" not in body


def test_recording_form_defaults_to_sydney_today(context, clock):
    _, client = context
    body = client.get("/collectibles/vinted/i-inv/post").text
    assert "name='posted_date' type='date' value='2026-11-01'" in body
    assert "max='2026-11-01'" in body
    assert "Check unsold status" in body
    assert "does not publish automatically" in body


@pytest.mark.parametrize("posted_date", ["", "2026-11-01"])
def test_recording_today_keeps_real_timestamp_and_reservation_time(context, clock, posted_date):
    session, client = context
    response = client.post("/collectibles/vinted/i-inv/posted", data={
        "url": URL, "price": "14.99", "posted_date": posted_date,
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "error=" not in response.headers["location"]
    listing = session.scalar(select(VintedListing))
    assert screens._utc(listing.posted_at) == NOW
    assert screens._utc(listing.stock_reserved_at) == NOW


def test_backdated_posting_date_uses_end_of_sydney_day_without_backdating_stock(context, clock):
    session, client = context
    response = client.post("/collectibles/vinted/i-inv/posted", data={
        "url": URL, "price": "14.99", "posted_date": "2026-10-31",
    }, follow_redirects=False)
    assert "error=" not in response.headers["location"]
    listing = session.scalar(select(VintedListing))
    assert screens._utc(listing.posted_at) == datetime(2026, 10, 31, 12, 59, 59, 999999, tzinfo=UTC)
    assert screens._utc(listing.stock_reserved_at) == NOW


def test_older_valid_posting_date_is_accepted(context, clock):
    session, client = context
    response = client.post("/collectibles/vinted/i-inv/posted", data={
        "url": URL, "price": "14.99", "posted_date": "2025-10-30",
    }, follow_redirects=False)
    assert "error=" not in response.headers["location"]
    listing = session.scalar(select(VintedListing))
    assert screens._utc(listing.posted_at) == datetime(2025, 10, 30, 12, 59, 59, 999999, tzinfo=UTC)


@pytest.mark.parametrize(("posted_date", "message"), [
    ("2026-11-02", "cannot be in the future"),
    ("2026-02-30", "YYYY-MM-DD"),
    ("20261101", "YYYY-MM-DD"),
    ("yesterday", "YYYY-MM-DD"),
])
def test_invalid_posting_dates_do_not_reserve_stock(context, clock, posted_date, message):
    session, client = context
    response = client.post("/collectibles/vinted/i-inv/posted", data={
        "url": URL, "price": "14.99", "posted_date": posted_date,
    }, follow_redirects=False)
    assert response.status_code == 303
    assert message in unquote(response.headers["location"])
    assert session.scalar(select(VintedListing)) is None
    inventory = session.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (1, InventoryStatus.AVAILABLE)


@pytest.mark.parametrize(("offset", "days", "due"), [
    (timedelta(days=14), 14, False),
    (timedelta(days=1, seconds=1), 2, False),
    (timedelta(seconds=1), 1, False),
    (timedelta(0), 0, True),
    (timedelta(seconds=-1), 0, True),
])
def test_fourteen_day_boundary_uses_utc_for_naive_sqlite_timestamps(offset, days, due):
    posted = (NOW - timedelta(days=14) + offset).replace(tzinfo=None)
    local_due, remaining_days, is_due = screens._review_timing(posted, NOW)
    assert remaining_days == days
    assert is_due is due
    assert local_due.astimezone(UTC) == NOW + offset
    assert str(local_due.tzinfo) == "Australia/Sydney"


def test_countdown_crossing_daylight_savings_is_fourteen_elapsed_days():
    posted = datetime(2026, 3, 29, 12, tzinfo=UTC)
    local_due, days, due = screens._review_timing(posted, posted + timedelta(days=14))
    assert local_due.isoformat() == "2026-04-12T22:00:00+10:00"
    assert (days, due) == (0, True)


def test_live_screen_shows_due_in_sydney_and_requires_unsold_check(context, clock):
    session, client = context
    client.post("/collectibles/vinted/i-inv/posted", data={"url": URL, "price": "14.99"})
    listing = session.scalar(select(VintedListing))
    listing.posted_at = NOW - timedelta(days=14)
    session.commit()
    body = client.get("/collectibles/vinted/live").text
    assert "eBay review due — check that this item is still unsold on Vinted." in body
    assert "Due 01 Nov 2026, 11:15 AEDT" in body
    assert "This does not publish automatically." in body


@pytest.mark.parametrize("action", ["sold", "remove"])
def test_sold_and_removed_listings_do_not_have_an_ebay_countdown(context, clock, action):
    session, client = context
    client.post("/collectibles/vinted/i-inv/posted", data={"url": URL, "price": "14.99"})
    listing = session.scalar(select(VintedListing))
    listing.posted_at = NOW - timedelta(days=20)
    session.commit()
    client.post(f"/collectibles/vinted/listing/{listing.id}/{action}", data={"price": "14.99"})
    body = client.get("/collectibles/vinted/live").text
    assert "eBay review due" not in body
    assert "eBay review in" not in body
