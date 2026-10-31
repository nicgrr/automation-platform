"""Vinted-only figures cannot leak through alternate eBay entry points."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import automation_control.ebay_publish as ebay_publish
import automation_control.ebay_sync as ebay_sync
from automation_control.models import (
    Approval, CatalogItem, CatalogItemType, EbayExportBatch, EbayListingLink,
    EbayListingLinkStatus, InventoryItem, ShopListingStatus,
)
from automation_control.selling_channels import (
    COLLECTIBLE_EBAY_BLOCK_REASON, ebay_channel_block_reason,
)
from scripts.export_ebay_bulk_listing import (
    build_set_grouped_rows, build_single_rows, build_variation_rows,
)
from scripts.import_ebay_orders import _reconcile
from test_ebay_publish import (
    _configured_settings, client, make_listing, session,
)


def _figure(session):
    listing = make_listing(session)
    # Keep a stale TcgCard detail row to reproduce a mixed-intake mismatch.
    # The canonical type must win over the shape of that old detail row.
    session.get(CatalogItem, listing.catalog_item_id).item_type = CatalogItemType.COLLECTIBLE
    session.commit()
    return listing


@pytest.mark.parametrize("kind", [CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD])
def test_card_types_are_not_reserved_for_vinted(session, kind):
    listing = make_listing(session)
    session.get(CatalogItem, listing.catalog_item_id).item_type = kind
    assert ebay_channel_block_reason(session, listing) is None


def test_collectible_stock_cannot_escape_through_a_mismatched_card_listing(session):
    listing = make_listing(session)
    session.add(CatalogItem(id="figure", name="Figure", item_type=CatalogItemType.COLLECTIBLE))
    session.add(InventoryItem(id="stock", catalog_item_id="figure", quantity=1))
    listing.inventory_item_id = "stock"
    session.commit()
    assert ebay_channel_block_reason(session, listing) == COLLECTIBLE_EBAY_BLOCK_REASON
    check = ebay_publish.publication_preflight(session, listing, _configured_settings())
    assert not check.ready
    assert check.errors == (COLLECTIBLE_EBAY_BLOCK_REASON,)


@pytest.mark.parametrize("action,confirmation", [("publish-to-ebay", "publish"), ("revise-on-ebay", "revise")])
def test_collectible_publication_and_revision_stop_before_external_calls(
    client, session, monkeypatch, action, confirmation,
):
    listing = _figure(session)
    if confirmation == "revise":
        session.add(EbayListingLink(
            shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
            status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="123", ebay_offer_id="offer",
        ))
        session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    calls = AsyncMock()
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", calls)
    response = client.post(f"/shop-listings/{listing.id}/{action}", data={
        "confirmation": confirmation, "pricing_override": listing.id,
    })
    assert response.status_code == 400
    assert "reserved for Vinted" in response.json()["detail"]
    calls.assert_not_called()
    assert session.scalar(select(Approval)) is None
    assert listing.price == Decimal("9.99")
    assert listing.quantity == 1


@pytest.mark.parametrize("builder", [build_single_rows, build_set_grouped_rows, build_variation_rows])
def test_all_csv_builders_exclude_figures_even_with_stale_tcg_details(session, monkeypatch, builder):
    import scripts.export_ebay_bulk_listing as export
    figure = _figure(session)
    # The variation export only considers cheap cards; price the figure
    # inside every builder's range so the channel policy is what stops it.
    figure.price = Decimal("1.00")
    session.commit()
    # Export image resolution can upload to R2, so the policy must run first.
    def forbidden_image_upload(*args):
        pytest.fail("a Vinted figure must not enter eBay image preparation")
    monkeypatch.setattr(export, "single_image_url", forbidden_image_upload)
    result = builder(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert result[0] == []
    assert any("reserved for Vinted" in reason for reason in result[-1])
    if builder is build_set_grouped_rows:
        assert result[1] == {}


def test_forged_bulk_export_and_price_override_cannot_export_a_collectible(client, session, monkeypatch):
    from automation_control.api import app
    from test_ebay_bulk_export import _settings
    listing = _figure(session)
    monkeypatch.setattr(app.state, "settings", _settings())
    response = client.post("/ebay-export/build", data={
        "sku": listing.id, "pricing_override": listing.id,
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "Vinted" in response.headers["location"]
    assert session.scalar(select(EbayExportBatch)) is None


def _active_link(session, listing):
    link = EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="123", ebay_offer_id="offer",
        last_pushed_quantity=2,
    )
    session.add(link)
    session.commit()
    return link


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [0, 1])
async def test_background_stock_sync_blocks_positive_stock_but_allows_withdrawal(session, monkeypatch, quantity):
    listing = _figure(session)
    link = _active_link(session, listing)
    monkeypatch.setattr(ebay_sync, "SessionLocal", sessionmaker(bind=session.bind))
    monkeypatch.setattr(ebay_sync, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_sync, "ebay_services", lambda _: (object(), object()))
    token = AsyncMock(return_value="token")
    monkeypatch.setattr(ebay_sync, "valid_user_access_token", token)
    adapter = AsyncMock()
    monkeypatch.setattr(ebay_sync, "EbayWriteAdapter", lambda *a, **kw: adapter)
    await ebay_sync.sync_ebay_after_sale(listing.id, quantity)
    session.refresh(link)
    adapter.set_inventory_quantity.assert_not_called()
    if quantity:
        token.assert_not_called()
        assert link.last_error == COLLECTIBLE_EBAY_BLOCK_REASON
        assert link.last_pushed_quantity == 2
    else:
        adapter.withdraw_offer.assert_awaited_once_with("offer")
        assert link.status == EbayListingLinkStatus.ENDED


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [0, 1])
async def test_recurring_reconciliation_cannot_restore_collectible_stock(session, quantity):
    listing = _figure(session)
    listing.quantity = quantity
    link = _active_link(session, listing)
    adapter = AsyncMock()
    healed, failed = await _reconcile(session, adapter)
    adapter.set_inventory_quantity.assert_not_called()
    if quantity:
        assert (healed, failed) == (0, 1)
        assert link.last_error == COLLECTIBLE_EBAY_BLOCK_REASON
        adapter.withdraw_offer.assert_not_called()
    else:
        assert (healed, failed) == (1, 0)
        adapter.withdraw_offer.assert_awaited_once_with("offer")


@pytest.mark.asyncio
async def test_title_revision_script_skips_collectibles_before_connecting(session, monkeypatch):
    import scripts.revise_ebay_titles as script
    listing = _figure(session)
    _active_link(session, listing)
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=session.bind))
    token = AsyncMock()
    monkeypatch.setattr(script, "valid_user_access_token", token)
    assert await script._run(apply=True, ebay_listing_ids=set()) == 0
    token.assert_not_called()
