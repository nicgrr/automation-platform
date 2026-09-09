from decimal import Decimal

from automation_control.models import CatalogItem, CatalogItemType, ShopListing, ShopListingStatus
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
