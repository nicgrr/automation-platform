from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import scripts.import_ebay_orders as script
from automation_control.adapters.ebay import EbayApiError
from automation_control.config import Settings
from automation_control.database import Base
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, EbayCredential, EbayImportedOrder, EbayListingLink, EbayListingLinkStatus,
    ShopListing, ShopListingStatus,
)


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="ful-1",
        ebay_return_policy_id="ret-1", ebay_merchant_location_key="loc-1",
    )


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_listing(Session, listing_id="listing-1", quantity=3):
    with Session() as session:
        # A real connected account always has this row (only ever created
        # by ebay_oauth.store_user_tokens, from a completed OAuth
        # exchange) -- seeded here so the script's cursor update has a
        # row to write to, matching the real invariant rather than having
        # the script paper over a state that can't happen in production.
        from datetime import UTC, datetime
        if session.get(EbayCredential, 1) is None:
            session.add(EbayCredential(id=1, encrypted_access_token=b"x", encrypted_refresh_token=b"y",
                                       access_token_expires_at=datetime.now(UTC), scopes="sell.inventory"))
        session.add(CatalogItem(id=f"item-{listing_id}", item_type=CatalogItemType.TCG_CARD, name="Test"))
        session.add(ShopListing(id=listing_id, catalog_item_id=f"item-{listing_id}", title="Test #1",
                                price=Decimal("9.99"), currency="AUD", quantity=quantity, status=ShopListingStatus.ACTIVE))
        session.add(EbayListingLink(shop_listing_id=listing_id, sku=listing_id, marketplace_id="EBAY_AU",
                                    status=EbayListingLinkStatus.ACTIVE, ebay_offer_id="offer-1", last_pushed_quantity=quantity))
        session.commit()


def _seed_credential(session):
    from datetime import UTC, datetime
    if session.get(EbayCredential, 1) is None:
        session.add(EbayCredential(id=1, encrypted_access_token=b"x", encrypted_refresh_token=b"y",
                                   access_token_expires_at=datetime.now(UTC), scopes="sell.inventory"))


def _seed_unlinked_listing(Session, listing_id="listing-1", quantity=3):
    """A ShopListing with no EbayListingLink at all -- what you get from
    uploading export_ebay_bulk_listing.py's CSV by hand through eBay's
    Bulk Listing Tool, rather than publishing through ebay_publish.py."""
    with Session() as session:
        _seed_credential(session)
        session.add(CatalogItem(id=f"item-{listing_id}", item_type=CatalogItemType.TCG_CARD, name="Test"))
        session.add(ShopListing(id=listing_id, catalog_item_id=f"item-{listing_id}", title="Test #1",
                                price=Decimal("2.00"), currency="AUD", quantity=quantity, status=ShopListingStatus.ACTIVE))
        session.commit()


def _seed_unlinked_lot(Session, lot_id="lot-1", card_count=30, quantity=1):
    with Session() as session:
        _seed_credential(session)
        session.add(BulkLot(id=lot_id, title="Obsidian Flames — bulk lot (30 cards)", card_count=card_count,
                            price=Decimal("20"), currency="AUD", quantity=quantity, status=ShopListingStatus.ACTIVE))
        session.commit()


def _patch_common(monkeypatch, Session, orders_payload, *, adapter_overrides=None):
    monkeypatch.setattr(script, "SessionLocal", Session)
    monkeypatch.setattr(script, "get_settings", _configured_settings)
    monkeypatch.setattr(script, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(script, "valid_user_access_token", AsyncMock(return_value="tok"))

    adapter = AsyncMock()
    adapter.get_orders = AsyncMock(return_value=orders_payload)
    adapter.withdraw_offer = AsyncMock(return_value=None)
    adapter.set_inventory_quantity = AsyncMock(return_value=None)
    if adapter_overrides:
        for name, value in adapter_overrides.items():
            setattr(adapter, name, value)
    monkeypatch.setattr(script, "EbayWriteAdapter", lambda *a, **k: adapter)
    return adapter


def test_refuses_to_run_when_not_configured_for_writes(monkeypatch, Session):
    monkeypatch.setattr(script, "SessionLocal", Session)
    monkeypatch.setattr(script, "get_settings", Settings)  # bare defaults -- not configured
    with pytest.raises(SystemExit):
        script.main([])


def test_applies_a_new_order_and_decrements_the_listing(monkeypatch, Session):
    _seed_listing(Session, quantity=3)
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "listing-1", "quantity": 2}]}]}
    _patch_common(monkeypatch, Session, orders)

    result = script.main([])
    assert result == 0

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.quantity == 1
        record = session.scalar(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == "ord-1"))
        assert record.applied_at is not None
        link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
        assert link.last_pushed_quantity == 1
        credential = session.get(EbayCredential, 1)
        assert credential.last_order_sync_at is not None


def test_running_twice_never_double_decrements(monkeypatch, Session):
    _seed_listing(Session, quantity=3)
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "listing-1", "quantity": 2}]}]}
    _patch_common(monkeypatch, Session, orders)

    script.main([])
    script.main([])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.quantity == 1  # not 0 -- the second run's line was skipped, already applied


def test_dry_run_writes_nothing(monkeypatch, Session):
    _seed_listing(Session, quantity=3)
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "listing-1", "quantity": 2}]}]}
    _patch_common(monkeypatch, Session, orders)

    script.main(["--dry-run"])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.quantity == 3
        assert session.scalar(select(EbayImportedOrder)) is None
        credential = session.get(EbayCredential, 1)
        assert credential.last_order_sync_at is None  # dry-run touches nothing, including the cursor


def test_an_order_for_an_unknown_sku_is_recorded_but_not_applied(monkeypatch, Session):
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "no-such-sku", "quantity": 1}]}]}
    _patch_common(monkeypatch, Session, orders)

    script.main([])

    with Session() as session:
        record = session.scalar(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == "ord-1"))
        assert record is not None
        assert record.applied_at is not None  # marked handled so it isn't re-logged forever
        assert record.shop_listing_id is None


def test_reconciliation_sweep_repushes_a_drifted_quantity(monkeypatch, Session):
    _seed_listing(Session, quantity=5)
    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        link.last_pushed_quantity = 5  # matches for now
        listing = session.get(ShopListing, "listing-1")
        listing.quantity = 2  # drifted -- e.g. a Phase 4 push that failed earlier
        session.commit()

    orders = {"orders": []}
    adapter = _patch_common(monkeypatch, Session, orders)

    script.main([])

    adapter.set_inventory_quantity.assert_awaited_once_with("listing-1", 2)
    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        assert link.last_pushed_quantity == 2
        assert link.last_error is None


def test_reconciliation_ends_the_link_when_quantity_has_reached_zero(monkeypatch, Session):
    _seed_listing(Session, quantity=5)
    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        link.last_pushed_quantity = 5
        listing = session.get(ShopListing, "listing-1")
        listing.quantity = 0
        listing.status = ShopListingStatus.SOLD_OUT
        session.commit()

    orders = {"orders": []}
    adapter = _patch_common(monkeypatch, Session, orders)

    script.main([])

    adapter.withdraw_offer.assert_awaited_once_with("offer-1")
    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        assert link.status == EbayListingLinkStatus.ENDED


def test_a_failed_reconciliation_push_is_recorded_not_raised(monkeypatch, Session):
    _seed_listing(Session, quantity=5)
    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        link.last_pushed_quantity = 5
        listing = session.get(ShopListing, "listing-1")
        listing.quantity = 2
        session.commit()

    orders = {"orders": []}
    _patch_common(monkeypatch, Session, orders, adapter_overrides={
        "set_inventory_quantity": AsyncMock(side_effect=EbayApiError("boom")),
    })

    script.main([])  # must not raise

    with Session() as session:
        link = session.scalar(select(EbayListingLink))
        assert "boom" in link.last_error


# --- matching a sale with no EbayListingLink (a manually-uploaded eBay CSV) --

def test_a_sale_for_a_csv_uploaded_single_decrements_it_with_no_link_at_all(monkeypatch, Session):
    """export_ebay_bulk_listing.py's CustomLabel/sku *is* the ShopListing
    id -- this must resolve and decrement even though no EbayListingLink
    row exists, since one never gets created for a manually-uploaded CSV."""
    _seed_unlinked_listing(Session, listing_id="listing-1", quantity=3)
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "listing-1", "quantity": 2}]}]}
    _patch_common(monkeypatch, Session, orders)

    result = script.main([])
    assert result == 0

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.quantity == 1
        record = session.scalar(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == "ord-1"))
        assert record.applied_at is not None
        assert record.shop_listing_id == "listing-1"


def test_a_sale_for_a_csv_uploaded_bulk_lot_decrements_and_ends_it_at_zero(monkeypatch, Session):
    """A Bulk Master lot sold via the same manually-uploaded CSV -- its
    sku is the BulkLot id directly, and BulkLot has no shop_listing_id
    slot to record (that FK only fits a real ShopListing)."""
    _seed_unlinked_lot(Session, lot_id="lot-1", quantity=1)
    orders = {"orders": [{"orderId": "ord-2", "lineItems": [{"sku": "lot-1", "quantity": 1}]}]}
    _patch_common(monkeypatch, Session, orders)

    result = script.main([])
    assert result == 0

    with Session() as session:
        lot = session.get(BulkLot, "lot-1")
        assert lot.quantity == 0
        assert lot.status == ShopListingStatus.SOLD_OUT
        record = session.scalar(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == "ord-2"))
        assert record.applied_at is not None
        assert record.shop_listing_id is None


def test_an_ebaylistinglink_match_is_still_preferred_over_a_direct_id_match(monkeypatch, Session):
    """If a listing has *both* a link (published via the API) and happens
    to share its id with a coincidental sku lookup, the link -- the
    authoritative source for an API-published listing -- must win."""
    _seed_listing(Session, listing_id="listing-1", quantity=3)
    orders = {"orders": [{"orderId": "ord-1", "lineItems": [{"sku": "listing-1", "quantity": 1}]}]}
    _patch_common(monkeypatch, Session, orders)

    script.main([])

    with Session() as session:
        link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
        assert link.last_pushed_quantity == 2  # the linked path's own bookkeeping ran
