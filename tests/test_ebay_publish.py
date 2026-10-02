import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import automation_control.ebay_publish as ebay_publish
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.ebay_oauth import INVENTORY_WRITE_SCOPE
from automation_control.models import (
    Approval, CardSet, CardVariant, CatalogItem, CatalogItemType, EbayCategoryMapping, EbayCredential,
    EbayListingLink, EbayListingLinkStatus, InventoryItem, ShopListing, ShopListingStatus, TcgCard,
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
    session.merge(CardSet(id="sv1", name="Scarlet & Violet"))
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name="Test Card",
                            image_url="https://example.test/card.jpg"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id="sv1", number="1"))
    listing = ShopListing(id=listing_id, catalog_item_id=item_id, title="Test Card #1",
                          condition="Near Mint · Normal", price=Decimal("9.99"), currency="AUD",
                          quantity=1, status=status)
    session.add(listing)
    session.add(EbayCredential(
        id=1, environment="sandbox", encrypted_access_token=b"access", encrypted_refresh_token=b"refresh",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1), scopes=INVENTORY_WRITE_SCOPE,
    ))
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
    assert resp.status_code == 400
    assert "not fully configured" in resp.json()["detail"]


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="ful-1",
        ebay_return_policy_id="ret-1", ebay_merchant_location_key="loc-1",
    )


def add_category_mapping(session, game="pokemon"):
    session.add(EbayCategoryMapping(
        marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game=game,
        ebay_category_id="183454",
    ))
    session.commit()


def test_preview_requires_an_explicit_production_confirmation(client, session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    settings = _configured_settings().model_copy(update={
        "ebay_env": "production", "ebay_client_id": "app-PRD-x", "ebay_runame": "app-PRD-runame",
    })
    session.get(EbayCredential, 1).environment = "production"
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "READY" in resp.text
    assert "eBay PRODUCTION" in resp.text
    assert "name='confirmation' value='publish' required" in resp.text
    assert "Use CSV fallback" in resp.text


def test_post_without_confirmation_cannot_create_an_approval(client, session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.post("/shop-listings/listing-1/publish-to-ebay")

    assert resp.status_code == 400
    assert "review and confirm" in resp.json()["detail"]
    assert session.scalars(select(Approval)).all() == []


def test_preflight_blocks_prices_below_ebays_minimum(client, session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("0.99")
    add_category_mapping(session)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "BLOCKED" in resp.text
    assert "at least AUD $1.00" in resp.text
    assert "Publish live to eBay" not in resp.text


def test_preflight_blocks_two_sellable_listings_backed_by_the_same_inventory(client, session, monkeypatch):
    listing = make_listing(session)
    inventory = InventoryItem(
        id="stock-1", catalog_item_id=listing.catalog_item_id,
        variant=CardVariant.NORMAL, condition="Near Mint", quantity=2,
    )
    session.add(inventory)
    session.flush()
    listing.inventory_item_id = inventory.id
    session.add(ShopListing(
        id="listing-duplicate", catalog_item_id=listing.catalog_item_id,
        inventory_item_id=inventory.id, title="Duplicate card", condition="Near Mint · Normal",
        price=Decimal("8.99"), currency="AUD", quantity=1,
        status=ShopListingStatus.DRAFT,
    ))
    add_category_mapping(session)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "BLOCKED" in resp.text
    assert "Another active or draft listing already uses this inventory" in resp.text
    assert "Publish live to eBay" not in resp.text


@pytest.mark.asyncio
async def test_a_second_simultaneous_publish_is_rejected_before_any_write(session):
    make_listing(session)
    lock = asyncio.Lock()
    await lock.acquire()
    ebay_publish._PUBLISH_LOCKS["listing-1"] = lock
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await ebay_publish.publish_to_ebay(
                "listing-1", confirmation="publish", user="testuser", session=session,
            )
        assert exc_info.value.status_code == 409
        assert session.scalars(select(Approval)).all() == []
    finally:
        lock.release()
        ebay_publish._PUBLISH_LOCKS.pop("listing-1", None)


def test_503_when_no_category_mapping_exists_for_the_game(client, session, monkeypatch):
    make_listing(session, game="dragon_ball_fusion_world")
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    resp = client.post("/shop-listings/listing-1/publish-to-ebay")
    assert resp.status_code == 400
    assert "category mapping" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_successful_publish_creates_an_active_link_and_an_approval(session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.upsert_inventory_item = AsyncMock(return_value=None)
    adapter.get_offers = AsyncMock(return_value=[])
    adapter.create_offer = AsyncMock(return_value="offer-123")
    adapter.publish_offer = AsyncMock(return_value="ebay-999")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    result = await ebay_publish.publish_to_ebay(
        "listing-1", confirmation="publish", user="testuser", session=session,
    )
    assert result.status_code == 303

    inventory_payload = adapter.upsert_inventory_item.await_args.args[1]
    assert inventory_payload["condition"] == "USED_VERY_GOOD"
    assert inventory_payload["conditionDescriptors"] == [{"name": "40001", "values": ["400010"]}]
    assert inventory_payload["product"]["aspects"]["Game"] == ["Pokémon TCG"]
    assert inventory_payload["product"]["imageUrls"] == ["https://example.test/card.jpg"]
    offer_payload = adapter.create_offer.await_args.args[0]
    assert offer_payload["listingDuration"] == "GTC"

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
        await ebay_publish.publish_to_ebay(
            "listing-1", confirmation="publish", user="testuser", session=session,
        )
    assert exc_info.value.status_code == 502

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.FAILED
    assert "boom" in link.last_error

    listing = session.get(ShopListing, "listing-1")
    assert listing.status == ShopListingStatus.ACTIVE  # untouched -- still sellable on the website


@pytest.mark.asyncio
async def test_retry_reuses_an_unpublished_offer_found_by_sku(session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.PUBLISHING,
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.get_offers = AsyncMock(return_value=[{
        "offerId": "offer-existing", "sku": "listing-1", "marketplaceId": "EBAY_AU",
        "status": "UNPUBLISHED",
    }])
    adapter.publish_offer = AsyncMock(return_value="ebay-999")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_publish.publish_to_ebay(
        "listing-1", confirmation="publish", user="testuser", session=session,
    )

    adapter.create_offer.assert_not_awaited()
    adapter.update_offer.assert_awaited_once()
    adapter.publish_offer.assert_awaited_once_with("offer-existing")
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.ebay_offer_id == "offer-existing"


@pytest.mark.asyncio
async def test_retry_reconciles_a_publish_that_succeeded_before_the_process_stopped(session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.PUBLISHING,
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.get_offers = AsyncMock(return_value=[{
        "offerId": "offer-existing", "sku": "listing-1", "marketplaceId": "EBAY_AU",
        "status": "PUBLISHED", "listing": {"listingId": "ebay-existing"},
    }])
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_publish.publish_to_ebay(
        "listing-1", confirmation="publish", user="testuser", session=session,
    )

    adapter.create_offer.assert_not_awaited()
    adapter.update_offer.assert_not_awaited()
    adapter.publish_offer.assert_not_awaited()
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.ebay_listing_id == "ebay-existing"


@pytest.mark.asyncio
async def test_full_publish_contract_reaches_all_three_inventory_api_stages(session, monkeypatch):
    """DB row -> real adapter HTTP payloads -> active link, without touching eBay."""
    make_listing(session)
    add_category_mapping(session)
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    calls = []

    async def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.method == "PUT" and "/inventory_item/" in request.url.path:
            payload = json.loads(request.content)
            assert request.headers["Content-Language"] == "en-AU"
            assert payload["conditionDescriptors"][0]["values"] == ["400010"]
            assert payload["product"]["aspects"]["Set"] == ["Scarlet & Violet"]
            assert payload["product"]["imageUrls"] == ["https://example.test/card.jpg"]
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.endswith("/offer"):
            assert request.url.params["sku"] == "listing-1"
            return httpx.Response(200, json={"offers": []})
        if request.method == "POST" and request.url.path.endswith("/offer"):
            payload = json.loads(request.content)
            assert payload["listingDuration"] == "GTC"
            assert payload["listingPolicies"] == {
                "fulfillmentPolicyId": "ful-1", "paymentPolicyId": "pay-1", "returnPolicyId": "ret-1",
            }
            return httpx.Response(201, json={"offerId": "offer-contract"})
        if request.method == "POST" and request.url.path.endswith("/offer/offer-contract/publish"):
            return httpx.Response(200, json={"listingId": "ebay-contract"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    real_adapter = ebay_publish.EbayWriteAdapter("tok", client)
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: real_adapter)

    result = await ebay_publish.publish_to_ebay(
        "listing-1", confirmation="publish", user="testuser", session=session,
    )
    await client.aclose()

    assert result.status_code == 303
    assert calls == [
        ("PUT", "/sell/inventory/v1/inventory_item/listing-1"),
        ("GET", "/sell/inventory/v1/offer"),
        ("POST", "/sell/inventory/v1/offer"),
        ("POST", "/sell/inventory/v1/offer/offer-contract/publish"),
    ]
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.ebay_offer_id == "offer-contract"
    assert link.ebay_listing_id == "ebay-contract"
