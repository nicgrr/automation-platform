"""One figurine, live on Vinted and eBay: it moves after 14 days and sells only once."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import automation_control.ebay_publish as ebay_publish
import automation_control.vinted_fallback as fallback
import automation_control.vinted_listings as screens
from automation_control.config import Settings
from automation_control.ebay_oauth import INVENTORY_WRITE_SCOPE
from automation_control.models import (
    EbayCredential, EbayListingLink, EbayListingLinkStatus, InventoryItem, InventoryStatus,
    ShopListing, ShopListingStatus, VintedListing, VintedListingStatus,
)
from automation_control.selling_channels import ebay_channel_block_reason
from automation_control.stock import decrement_listing_quantity
from scripts.audit_inventory_vs_listings import check_active_listing_gone_inventory
from test_vinted_listings import URL, _post, context  # noqa: F401

PHOTO = "https://example.test/figure.jpg"
NOW = datetime(2026, 12, 1, tzinfo=UTC)


def settings():
    return Settings(
        ebay_bulk_upload_only=False, ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="letter-1", ebay_return_policy_id="ret-1",
        ebay_merchant_location_key="loc-1", ebay_figure_fulfillment_policy_id="tracked-1",
        discord_webhook_url="https://example.test/hook",
    )


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(fallback, "send_discord_message", lambda message, url: sent.append(message))
    monkeypatch.setattr(fallback, "get_settings", settings)
    monkeypatch.setattr(ebay_publish, "get_settings", settings)
    return sent


@pytest.fixture
def ebay(monkeypatch):
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda s: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.get_offers = AsyncMock(return_value=[])
    adapter.create_offer = AsyncMock(return_value="offer-1")
    adapter.publish_offer = AsyncMock(return_value="ebay-1")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)
    return adapter


def posted(context, *, quantity=1, days_ago=15, parcel=True):
    session, client = context
    stock = session.get(InventoryItem, "inv")
    stock.quantity, stock.scan_image_path = quantity, PHOTO
    session.add(EbayCredential(id=1, environment="sandbox", encrypted_access_token=b"a", encrypted_refresh_token=b"r",
                               access_token_expires_at=NOW + timedelta(days=3650), scopes=INVENTORY_WRITE_SCOPE))
    session.commit()
    assert _post(client).status_code == 303
    vinted = session.scalar(select(VintedListing))
    vinted.posted_at = NOW - timedelta(days=days_ago)
    if parcel:
        fallback.save_parcel(session, "150", "18", "12", "8")
    session.commit()
    return session, vinted


async def move(session):
    return await fallback.run_fallback(session, now=NOW, apply=True)


@pytest.mark.asyncio
async def test_nothing_moves_before_fourteen_days(context, alerts, ebay):
    session, _ = posted(context, days_ago=13)
    assert await move(session) == []
    ebay.upsert_inventory_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_it_moves_after_fourteen_days_and_stays_on_vinted(context, alerts, ebay):
    session, vinted = posted(context)
    assert (await move(session))[0].startswith("MOVED")

    inventory = ebay.upsert_inventory_item.await_args.args[1]
    offer = ebay.create_offer.await_args.args[0]
    assert inventory["condition"] == "USED_EXCELLENT"
    assert inventory["packageWeightAndSize"]["weight"] == {"value": 0.15, "unit": "KILOGRAM"}
    assert inventory["product"]["aspects"]["Type"] == ["Figure"]
    assert inventory["product"]["imageUrls"] == [PHOTO]
    assert offer["categoryId"] == fallback.CATEGORY_ID
    assert offer["listingPolicies"]["fulfillmentPolicyId"] == "tracked-1"
    assert offer["pricingSummary"]["price"]["value"] == "14.99"

    session.refresh(vinted)
    stock = session.get(InventoryItem, "inv")
    assert vinted.status == VintedListingStatus.ACTIVE
    assert (stock.quantity, stock.status) == (0, InventoryStatus.RESERVED)
    assert session.scalar(select(EbayListingLink)).status == EbayListingLinkStatus.ACTIVE
    assert any("Moved to eBay" in message for message in alerts)


@pytest.mark.asyncio
async def test_a_second_run_does_not_list_it_again(context, alerts, ebay):
    session, _ = posted(context)
    await move(session)
    assert await move(session) == []
    assert ebay.create_offer.await_count == 1


@pytest.mark.asyncio
async def test_it_reuses_the_figures_existing_listing(context, alerts, ebay):
    session, vinted = posted(context)
    existing = ShopListing(catalog_item_id="sonny", inventory_item_id="inv", title="Old", price=Decimal("38.13"),
                           currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE, condition="New")
    session.add(existing)
    session.commit()
    await move(session)
    assert session.scalars(select(ShopListing)).all() == [existing]
    assert existing.price == vinted.price and existing.ebay_photo_paths == [PHOTO]


@pytest.mark.asyncio
async def test_no_parcel_blocks_and_alerts_once_and_changes_nothing(context, alerts, ebay):
    session, vinted = posted(context, parcel=False)
    existing = ShopListing(catalog_item_id="sonny", inventory_item_id="inv", title="Old", price=Decimal("38.13"),
                           currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE, condition="New")
    session.add(existing)
    session.commit()
    assert "parcel" in (await move(session))[0]
    await move(session)
    ebay.upsert_inventory_item.assert_not_awaited()
    assert len([m for m in alerts if "Couldn't move" in m]) == 1
    session.refresh(existing)
    assert existing.price == Decimal("38.13") and ebay_channel_block_reason(session, existing)
    assert "parcel" in fallback.state_for(session, vinted.id)["blocked"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change, expected", [
    ({"condition": "Bit chipped, see notes"}, "no eBay equivalent"),
    ({"hold_reason": "Confirm edition"}, "on hold"),
    ({"description": ""}, "description is empty"),
])
async def test_incomplete_details_never_reach_ebay(context, alerts, ebay, change, expected):
    session, vinted = posted(context)
    source = fallback.source_for(session, vinted.id)
    fallback._save_config(session, fallback.SOURCE_PREFIX + vinted.id, {**source, **change})
    session.commit()
    assert expected in (await move(session))[0]
    ebay.upsert_inventory_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_ebay_failure_is_reported_and_retried(context, alerts, ebay):
    session, _ = posted(context)
    ebay.publish_offer = AsyncMock(side_effect=ebay_publish.EbayApiError("eBay down"))
    assert (await move(session))[0].startswith("BLOCKED")
    ebay.publish_offer = AsyncMock(return_value="ebay-1")
    assert (await move(session))[0].startswith("MOVED")


@pytest.mark.asyncio
async def test_an_ebay_sale_closes_vinted_and_uses_the_reserved_unit(context, alerts, ebay):
    session, vinted = posted(context)
    await move(session)
    listing = session.scalar(select(ShopListing))
    decrement_listing_quantity(session, listing, 1)
    session.commit()

    session.refresh(vinted)
    stock = session.get(InventoryItem, "inv")
    assert vinted.status == VintedListingStatus.REMOVED
    assert (stock.quantity, stock.status) == (0, InventoryStatus.SOLD)
    assert listing.status == ShopListingStatus.SOLD_OUT
    assert fallback.state_for(session, vinted.id)["delete_from_vinted"] is True
    assert any("delete this from Vinted" in m and URL.split("-")[0] in m for m in alerts)


@pytest.mark.asyncio
async def test_an_ebay_sale_leaves_a_second_copy_in_stock(context, alerts, ebay):
    session, _ = posted(context, quantity=2)
    await move(session)
    decrement_listing_quantity(session, session.scalar(select(ShopListing)), 1)
    session.commit()
    stock = session.get(InventoryItem, "inv")
    assert (stock.quantity, stock.status) == (1, InventoryStatus.AVAILABLE)


@pytest.mark.asyncio
async def test_after_removal_from_vinted_an_ebay_sale_uses_normal_stock(context, alerts, ebay):
    session, vinted = posted(context)
    await move(session)
    assert context[1].post(f"/collectibles/vinted/listing/{vinted.id}/remove", follow_redirects=False).status_code == 303
    decrement_listing_quantity(session, session.scalar(select(ShopListing)), 1)
    session.commit()
    stock = session.get(InventoryItem, "inv")
    assert (stock.quantity, stock.status) == (0, InventoryStatus.SOLD)


def _sell_on_vinted(context, vinted):
    return context[1].post(f"/collectibles/vinted/listing/{vinted.id}/sold", data={"price": "14.99", "fees": "0"},
                           follow_redirects=False)


@pytest.mark.asyncio
async def test_a_vinted_sale_takes_it_off_ebay(context, alerts, ebay, monkeypatch):
    session, vinted = posted(context)
    await move(session)
    link = session.scalar(select(EbayListingLink))
    link_id = link.shop_listing_id

    async def ended(shop_listing_id):
        assert shop_listing_id == link_id
        link.status = EbayListingLinkStatus.ENDED
        session.commit()

    monkeypatch.setattr(screens, "end_ebay_listing", ended)
    monkeypatch.setattr(screens, "asyncio", _Runner())
    response = _sell_on_vinted(context, vinted)
    assert "ended" in response.headers["location"]
    listing = session.scalar(select(ShopListing))
    assert (listing.status, listing.quantity) == (ShopListingStatus.SOLD_OUT, 0)
    assert session.get(InventoryItem, "inv").status == InventoryStatus.SOLD


class _Runner:
    """The route is sync but the test's event loop is already running."""

    @staticmethod
    def run(coroutine):
        try:
            coroutine.send(None)
        except StopIteration:
            pass


@pytest.mark.asyncio
async def test_a_failed_ebay_end_after_a_vinted_sale_is_loud(context, alerts, ebay, monkeypatch):
    session, vinted = posted(context)
    await move(session)

    async def failed(shop_listing_id):
        return None

    monkeypatch.setattr(screens, "end_ebay_listing", failed)
    monkeypatch.setattr(screens, "asyncio", _Runner())
    response = _sell_on_vinted(context, vinted)
    assert "did%20NOT%20end" in response.headers["location"]
    assert any("did NOT end" in message for message in alerts)
    assert fallback.state_for(session, vinted.id)["end_on_ebay"] is True


def test_a_vinted_sale_without_an_ebay_listing_is_unchanged(context, alerts):
    session, vinted = posted(context, days_ago=2)
    assert "Sale%20recorded" in _sell_on_vinted(context, vinted).headers["location"]
    assert session.get(InventoryItem, "inv").status == InventoryStatus.SOLD


@pytest.mark.asyncio
async def test_the_audit_accepts_the_shared_reservation_only_while_vinted_is_live(context, alerts, ebay):
    session, vinted = posted(context)
    await move(session)
    assert check_active_listing_gone_inventory(session) == []
    vinted.status = VintedListingStatus.REMOVED
    session.commit()
    assert len(check_active_listing_gone_inventory(session)) == 1


def test_a_figure_that_has_not_moved_stays_blocked_from_ebay(context):
    session, _ = context
    listing = ShopListing(catalog_item_id="sonny", inventory_item_id="inv", title="Figure", price=Decimal("10"),
                          currency="AUD", quantity=1, status=ShopListingStatus.DRAFT)
    session.add(listing)
    session.commit()
    assert ebay_channel_block_reason(session, listing)


def test_the_parcel_form_refuses_bad_sizes(context):
    session, client = context
    response = client.post("/collectibles/vinted/parcel", follow_redirects=False,
                           data={"weight_g": "0", "length_cm": "18", "width_cm": "12", "height_cm": "8"})
    assert "error=" in response.headers["location"]
    assert fallback.saved_parcel(session) == {}
