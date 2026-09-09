from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import automation_control.ebay_publish as ebay_publish
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    Approval, CatalogItem, CatalogItemType, EbayCategoryMapping, EbayListingLink, EbayListingLinkStatus,
    ShopListing, ShopListingStatus, TcgCard,
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


def make_listing(session, listing_id="listing-1", *, status=ShopListingStatus.ACTIVE, game="pokemon"):
    item_id = f"item-{listing_id}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name="Test Card"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id="sv1", number="1"))
    listing = ShopListing(id=listing_id, catalog_item_id=item_id, title="Test Card #1",
                          price=Decimal("9.99"), currency="AUD", quantity=1, status=status)
    session.add(listing)
    session.commit()
    return listing


def test_404_for_a_listing_that_does_not_exist(client):
    resp = client.post("/shop-listings/nope/publish-to-ebay")
    assert resp.status_code == 404


def test_400_for_a_withdrawn_listing(client, session):
    make_listing(session, status=ShopListingStatus.WITHDRAWN)
    resp = client.post("/shop-listings/listing-1/publish-to-ebay")
    assert resp.status_code == 400


def test_400_when_already_active_on_ebay(client, session):
    make_listing(session)
    session.add(EbayListingLink(shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU", status=EbayListingLinkStatus.ACTIVE))
    session.commit()
    resp = client.post("/shop-listings/listing-1/publish-to-ebay")
    assert resp.status_code == 400
    assert "active" in resp.json()["detail"]


def test_503_when_ebay_writes_are_not_configured(client, session):
    make_listing(session)
    resp = client.post("/shop-listings/listing-1/publish-to-ebay")
    assert resp.status_code == 503
    assert "not fully configured" in resp.json()["detail"]


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="ful-1",
        ebay_return_policy_id="ret-1", ebay_merchant_location_key="loc-1",
    )


def test_503_when_no_category_mapping_exists_for_the_game(client, session, monkeypatch):
    make_listing(session, game="dragon_ball_fusion_world")
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    resp = client.post("/shop-listings/listing-1/publish-to-ebay")
    assert resp.status_code == 503
    assert "category mapping" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_successful_publish_creates_an_active_link_and_an_approval(session, monkeypatch):
    make_listing(session)
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="pokemon", ebay_category_id="183454"))
    session.commit()

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.upsert_inventory_item = AsyncMock(return_value=None)
    adapter.create_offer = AsyncMock(return_value="offer-123")
    adapter.publish_offer = AsyncMock(return_value="ebay-999")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    result = await ebay_publish.publish_to_ebay("listing-1", user="testuser", session=session)
    assert result.status_code == 303

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.ebay_offer_id == "offer-123"
    assert link.ebay_listing_id == "ebay-999"
    assert link.last_pushed_quantity == 1

    approval = session.get(Approval, link.approval_id)
    assert approval.action == "ebay.publish_listing"
    assert approval.status.value == "approved"


@pytest.mark.asyncio
async def test_a_failed_ebay_call_marks_the_link_failed_without_touching_the_listing(session, monkeypatch):
    from automation_control.adapters.ebay import EbayApiError

    make_listing(session)
    session.add(EbayCategoryMapping(marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="pokemon", ebay_category_id="183454"))
    session.commit()

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.upsert_inventory_item = AsyncMock(side_effect=EbayApiError("boom"))
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await ebay_publish.publish_to_ebay("listing-1", user="testuser", session=session)
    assert exc_info.value.status_code == 502

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.FAILED
    assert "boom" in link.last_error

    listing = session.get(ShopListing, "listing-1")
    assert listing.status == ShopListingStatus.ACTIVE  # untouched -- still sellable on the website
