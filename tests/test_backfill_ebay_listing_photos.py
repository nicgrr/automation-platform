from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import scripts.backfill_ebay_listing_photos as script
from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading import ItemDetails
from automation_control.database import Base
from automation_control.models import EbayListing


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_listing(Session, *, ebay_listing_id="1", title="Pikachu", image_urls=None, source="trading_api"):
    with Session() as session:
        snapshot = {"source": source}
        if image_urls is not None:
            snapshot["image_urls"] = image_urls
        session.add(EbayListing(ebay_listing_id=ebay_listing_id, title=title, currency="AUD",
                                current_price_minor=500, listing_status="Active", quantity=1,
                                raw_snapshot=snapshot))
        session.commit()


def _patch_common(monkeypatch, Session, *, adapter_return=None, adapter_side_effect=None):
    monkeypatch.setattr(script, "SessionLocal", Session)
    monkeypatch.setattr(script, "configured", lambda settings: True)
    monkeypatch.setattr(script, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(script, "valid_user_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(script.asyncio, "sleep", AsyncMock(return_value=None))

    adapter = AsyncMock()
    if adapter_side_effect is not None:
        adapter.get_item_details = AsyncMock(side_effect=adapter_side_effect)
    else:
        adapter.get_item_details = AsyncMock(return_value=adapter_return)
    monkeypatch.setattr(script, "EbayTradingReadAdapter", lambda *a, **k: adapter)
    return adapter


def test_refuses_to_run_when_not_configured(monkeypatch, Session):
    monkeypatch.setattr(script, "SessionLocal", Session)
    monkeypatch.setattr(script, "configured", lambda settings: False)
    with pytest.raises(SystemExit):
        script.main([])


def test_backfills_photos_and_description_for_a_listing_missing_them(monkeypatch, Session):
    _seed_listing(Session, ebay_listing_id="1")
    _patch_common(monkeypatch, Session, adapter_return=ItemDetails(
        picture_urls=["https://x/a.jpg", "https://x/b.jpg"], description="<p>NM</p>",
    ))

    result = script.main([])
    assert result == 0

    with Session() as session:
        row = session.query(EbayListing).filter_by(ebay_listing_id="1").one()
        assert row.raw_snapshot["image_urls"] == ["https://x/a.jpg", "https://x/b.jpg"]
        assert row.raw_snapshot["description"] == "<p>NM</p>"
        assert row.raw_snapshot["details_source"] == "ebay_get_item"


def test_a_listing_that_already_has_photos_is_skipped(monkeypatch, Session):
    _seed_listing(Session, ebay_listing_id="1", image_urls=["https://x/existing.jpg"])
    adapter = _patch_common(monkeypatch, Session, adapter_return=ItemDetails(picture_urls=["https://x/new.jpg"], description=None))

    script.main([])

    adapter.get_item_details.assert_not_called()
    with Session() as session:
        row = session.query(EbayListing).filter_by(ebay_listing_id="1").one()
        assert row.raw_snapshot["image_urls"] == ["https://x/existing.jpg"]


def test_a_sell_api_sourced_row_is_never_touched(monkeypatch, Session):
    """The shared table also holds rows from the Sell API sync -- this
    backfill is scoped to trading_api rows only, same as the rest of
    ebay_listings.py."""
    _seed_listing(Session, ebay_listing_id="1", source="sell_api")
    adapter = _patch_common(monkeypatch, Session, adapter_return=ItemDetails(picture_urls=["https://x/a.jpg"], description=None))

    script.main([])

    adapter.get_item_details.assert_not_called()


def test_dry_run_writes_nothing(monkeypatch, Session):
    _seed_listing(Session, ebay_listing_id="1")
    _patch_common(monkeypatch, Session, adapter_return=ItemDetails(picture_urls=["https://x/a.jpg"], description=None))

    script.main(["--dry-run"])

    with Session() as session:
        row = session.query(EbayListing).filter_by(ebay_listing_id="1").one()
        assert "image_urls" not in row.raw_snapshot


def test_a_failed_lookup_is_recorded_and_the_run_continues(monkeypatch, Session):
    _seed_listing(Session, ebay_listing_id="1")
    _seed_listing(Session, ebay_listing_id="2")
    _patch_common(monkeypatch, Session, adapter_side_effect=[
        EbayApiError("item ended"), ItemDetails(picture_urls=["https://x/a.jpg"], description=None),
    ])

    result = script.main([])
    assert result == 0

    with Session() as session:
        row1 = session.query(EbayListing).filter_by(ebay_listing_id="1").one()
        row2 = session.query(EbayListing).filter_by(ebay_listing_id="2").one()
        assert "image_urls" not in row1.raw_snapshot
        assert row2.raw_snapshot["image_urls"] == ["https://x/a.jpg"]


def test_a_listing_with_no_photos_returned_is_left_unmodified(monkeypatch, Session):
    _seed_listing(Session, ebay_listing_id="1")
    _patch_common(monkeypatch, Session, adapter_return=ItemDetails(picture_urls=[], description=None))

    script.main([])

    with Session() as session:
        row = session.query(EbayListing).filter_by(ebay_listing_id="1").one()
        assert "image_urls" not in row.raw_snapshot
