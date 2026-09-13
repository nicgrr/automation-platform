from decimal import Decimal

from automation_control.models import BulkLot, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus
from automation_control.stock import decrement_listing_quantity


def _make_listing(session, quantity, *, status=ShopListingStatus.ACTIVE):
    session.add(CatalogItem(id="item-1", item_type=CatalogItemType.TCG_CARD, name="Test"))
    session.flush()
    listing = ShopListing(catalog_item_id="item-1", title="Test #1", price=Decimal("5.00"),
                          currency="AUD", quantity=quantity, status=status)
    session.add(listing)
    session.flush()
    return listing


def test_reduces_quantity_and_returns_the_new_value(session):
    listing = _make_listing(session, 3)
    assert decrement_listing_quantity(listing, 1) == 2
    assert listing.quantity == 2
    assert listing.status == ShopListingStatus.ACTIVE


def test_flips_to_sold_out_at_zero(session):
    listing = _make_listing(session, 1)
    assert decrement_listing_quantity(listing, 1) == 0
    assert listing.status == ShopListingStatus.SOLD_OUT


def test_never_goes_negative(session):
    listing = _make_listing(session, 1)
    assert decrement_listing_quantity(listing, 5) == 0
    assert listing.quantity == 0


def test_returns_none_for_an_already_sold_out_listing():
    listing = ShopListing(catalog_item_id="item-1", title="Test #1", price=Decimal("5.00"),
                          currency="AUD", quantity=0, status=ShopListingStatus.SOLD_OUT)
    assert decrement_listing_quantity(listing, 1) is None


def test_a_bulk_lot_decrements_the_same_way_a_shop_listing_does():
    """A Bulk Master lot shares the exact same quantity/status shape as a
    ShopListing (same ShopListingStatus enum) -- this helper must work on
    either, not just the one it happened to be written for first."""
    lot = BulkLot(title="Test lot", card_count=30, price=Decimal("20"), currency="AUD",
                  quantity=1, status=ShopListingStatus.ACTIVE)
    assert decrement_listing_quantity(lot, 1) == 0
    assert lot.status == ShopListingStatus.SOLD_OUT
