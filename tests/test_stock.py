from decimal import Decimal

from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus,
)
from automation_control.stock import decrement_listing_quantity


def _make_listing(session, quantity, *, status=ShopListingStatus.ACTIVE, inventory_item_id=None):
    if session.get(CatalogItem, "item-1") is None:
        session.add(CatalogItem(id="item-1", item_type=CatalogItemType.TCG_CARD, name="Test"))
        session.flush()
    listing = ShopListing(catalog_item_id="item-1", title="Test #1", price=Decimal("5.00"),
                          currency="AUD", quantity=quantity, status=status,
                          inventory_item_id=inventory_item_id)
    session.add(listing)
    session.flush()
    return listing


def _make_inventory_item(session, quantity, *, status=InventoryStatus.AVAILABLE):
    if session.get(CatalogItem, "item-1") is None:
        session.add(CatalogItem(id="item-1", item_type=CatalogItemType.TCG_CARD, name="Test"))
        session.flush()
    item = InventoryItem(catalog_item_id="item-1", quantity=quantity, status=status)
    session.add(item)
    session.flush()
    return item


def test_reduces_quantity_and_returns_the_new_value(session):
    listing = _make_listing(session, 3)
    assert decrement_listing_quantity(session, listing, 1) == 2
    assert listing.quantity == 2
    assert listing.status == ShopListingStatus.ACTIVE


def test_flips_to_sold_out_at_zero(session):
    listing = _make_listing(session, 1)
    assert decrement_listing_quantity(session, listing, 1) == 0
    assert listing.status == ShopListingStatus.SOLD_OUT


def test_never_goes_negative(session):
    listing = _make_listing(session, 1)
    assert decrement_listing_quantity(session, listing, 5) == 0
    assert listing.quantity == 0


def test_returns_none_for_an_already_sold_out_listing(session):
    listing = _make_listing(session, 0, status=ShopListingStatus.SOLD_OUT)
    assert decrement_listing_quantity(session, listing, 1) is None


def test_a_bulk_lot_decrements_the_same_way_a_shop_listing_does():
    """A Bulk Master lot shares the exact same quantity/status shape as a
    ShopListing (same ShopListingStatus enum) -- this helper must work on
    either, not just the one it happened to be written for first."""
    lot = BulkLot(title="Test lot", card_count=30, price=Decimal("20"), currency="AUD",
                  quantity=1, status=ShopListingStatus.ACTIVE)
    assert decrement_listing_quantity(None, lot, 1) == 0
    assert lot.status == ShopListingStatus.SOLD_OUT


# --- the real fix: the physical InventoryItem must move too ------------

def test_a_sale_also_reduces_the_linked_inventory_items_quantity(session):
    """Real incident, 2026-09-28: this used to only ever touch the
    listing. A card sold through checkout or an eBay order stayed marked
    AVAILABLE in inventory forever, with nothing to say it was gone."""
    item = _make_inventory_item(session, 3)
    listing = _make_listing(session, 3, inventory_item_id=item.id)

    decrement_listing_quantity(session, listing, 1)

    assert item.quantity == 2
    assert item.status == InventoryStatus.AVAILABLE


def test_selling_the_last_one_marks_the_inventory_item_sold(session):
    item = _make_inventory_item(session, 1)
    listing = _make_listing(session, 1, inventory_item_id=item.id)

    decrement_listing_quantity(session, listing, 1)

    assert item.quantity == 0
    assert item.status == InventoryStatus.SOLD


def test_a_listing_with_no_inventory_item_id_is_unaffected(session):
    """A manually-created listing with no physical holding behind it
    (inventory_item_id=None) must not error -- nothing to update."""
    listing = _make_listing(session, 1, inventory_item_id=None)
    assert decrement_listing_quantity(session, listing, 1) == 0


def test_an_already_sold_inventory_item_is_not_decremented_again(session):
    """Two listings sharing one physical InventoryItem (a real,
    separately-tracked data bug) must not push the same item's quantity
    negative just because a second listing also claims a sale against it."""
    item = _make_inventory_item(session, 1, status=InventoryStatus.SOLD)
    item.quantity = 0
    listing = _make_listing(session, 1, inventory_item_id=item.id)

    decrement_listing_quantity(session, listing, 1)

    assert item.quantity == 0
    assert item.status == InventoryStatus.SOLD


def test_a_bulk_lot_never_touches_any_inventory_item(session):
    """A lot bundles many cards under one listing with no single
    InventoryItem behind it -- the inventory-side branch must never fire
    for one, even though it shares ShopListingStatus with ShopListing."""
    lot = BulkLot(title="Test lot", card_count=30, price=Decimal("20"), currency="AUD",
                  quantity=1, status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.flush()
    decrement_listing_quantity(session, lot, 1)
    assert lot.status == ShopListingStatus.SOLD_OUT
