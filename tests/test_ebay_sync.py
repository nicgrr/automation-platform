from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import automation_control.ebay_sync as ebay_sync
from automation_control.config import Settings
from automation_control.database import Base
from automation_control.models import EbayListingLink, EbayListingLinkStatus


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
    )


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine


@pytest.mark.asyncio
async def test_does_nothing_when_ebay_is_not_configured(engine, monkeypatch):
    monkeypatch.setattr(ebay_sync, "SessionLocal", sessionmaker(bind=engine))
    # default Settings() has no ebay credentials -- configured() is False
    monkeypatch.setattr(ebay_sync, "get_settings", Settings)
    await ebay_sync.sync_ebay_after_sale("listing-1", 0)  # must not raise


@pytest.mark.asyncio
async def test_does_nothing_when_no_active_link_exists(engine, monkeypatch):
    monkeypatch.setattr(ebay_sync, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(ebay_sync, "get_settings", _configured_settings)
    await ebay_sync.sync_ebay_after_sale("listing-1", 0)  # no EbayListingLink row at all -- must not raise


@pytest.mark.asyncio
async def test_withdraws_the_offer_when_quantity_hits_zero(engine, monkeypatch):
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(EbayListingLink(shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
                                    status=EbayListingLinkStatus.ACTIVE, ebay_offer_id="offer-1"))
        session.commit()

    monkeypatch.setattr(ebay_sync, "SessionLocal", Session)
    monkeypatch.setattr(ebay_sync, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_sync, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_sync, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.withdraw_offer = AsyncMock(return_value=None)
    monkeypatch.setattr(ebay_sync, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_sync.sync_ebay_after_sale("listing-1", 0)

    adapter.withdraw_offer.assert_awaited_once_with("offer-1")
    with Session() as session:
        link = session.query(EbayListingLink).filter_by(shop_listing_id="listing-1").one()
        assert link.status == EbayListingLinkStatus.ENDED
        assert link.last_pushed_quantity == 0
        assert link.last_error is None


@pytest.mark.asyncio
async def test_pushes_a_reduced_quantity_without_ending_the_listing(engine, monkeypatch):
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(EbayListingLink(shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
                                    status=EbayListingLinkStatus.ACTIVE, ebay_offer_id="offer-1"))
        session.commit()

    monkeypatch.setattr(ebay_sync, "SessionLocal", Session)
    monkeypatch.setattr(ebay_sync, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_sync, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_sync, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.set_inventory_quantity = AsyncMock(return_value=None)
    monkeypatch.setattr(ebay_sync, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_sync.sync_ebay_after_sale("listing-1", 2)

    adapter.set_inventory_quantity.assert_awaited_once_with("listing-1", 2)
    with Session() as session:
        link = session.query(EbayListingLink).filter_by(shop_listing_id="listing-1").one()
        assert link.status == EbayListingLinkStatus.ACTIVE  # still active, just fewer left
        assert link.last_pushed_quantity == 2


@pytest.mark.asyncio
async def test_a_failed_ebay_call_is_recorded_and_never_raised(engine, monkeypatch):
    from automation_control.adapters.ebay import EbayApiError

    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(EbayListingLink(shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
                                    status=EbayListingLinkStatus.ACTIVE, ebay_offer_id="offer-1"))
        session.commit()

    monkeypatch.setattr(ebay_sync, "SessionLocal", Session)
    monkeypatch.setattr(ebay_sync, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_sync, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_sync, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.set_inventory_quantity = AsyncMock(side_effect=EbayApiError("rate limited"))
    monkeypatch.setattr(ebay_sync, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_sync.sync_ebay_after_sale("listing-1", 3)  # must not raise

    with Session() as session:
        link = session.query(EbayListingLink).filter_by(shop_listing_id="listing-1").one()
        assert link.status == EbayListingLinkStatus.ACTIVE  # left alone for the reconciliation sweep to retry
        assert "rate limited" in link.last_error
