from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import BulkLot, CardSet, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus, TcgCard
from scripts.export_ebay_bulk_listing import build_lot_rows, build_single_rows, lot_shipping_policy, single_image_url


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _item(session, **kwargs):
    item = CatalogItem(id="item-1", name="Test Card", item_type=CatalogItemType.TCG_CARD, **kwargs)
    session.add(item)
    session.commit()
    return item


# --- shipping is by card count, not price -----------------------------

def test_lot_max_cards_boundary_is_inclusive():
    assert lot_shipping_policy(20, 20, "Letter", "Parcel") == "Letter"
    assert lot_shipping_policy(21, 20, "Letter", "Parcel") == "Parcel"


def test_a_single_card_listing_is_always_letter_regardless_of_price(session):
    """One card is one card whatever it's worth -- price never enters
    into which shipping policy a single-card listing gets."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(ShopListing(catalog_item_id=item.id, title="Cheap Card", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.add(ShopListing(catalog_item_id=item.id, title="Pricey Card", price=Decimal("500"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter Mail")
    assert {r["shipping_policy"] for r in rows} == {"Letter Mail"}


def test_lot_weight_scales_with_card_count_for_calculated_shipping(session):
    session.add(BulkLot(title="Small Lot", card_count=10, price=Decimal("8"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.add(BulkLot(title="Huge Lot", card_count=185, price=Decimal("150"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter", parcel_policy="Parcel")
    by_title = {r["title"]: r["weight_grams"] for r in rows}
    assert by_title["Small Lot"] == 10 * 3 + 80
    assert by_title["Huge Lot"] == 185 * 3 + 80


def test_bulk_lots_are_tiered_by_card_count(session):
    session.add(BulkLot(title="Thin Lot", card_count=18, price=Decimal("15"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.add(BulkLot(title="Big Lot", card_count=140, price=Decimal("95"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter Mail", parcel_policy="Tracked Parcel")
    by_title = {r["title"]: r["shipping_policy"] for r in rows}
    assert by_title["Thin Lot"] == "Letter Mail"
    assert by_title["Big Lot"] == "Tracked Parcel"


# --- image resolution ---------------------------------------------------

def test_single_image_url_prefers_the_listings_own_hosted_image(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="https://example.test/listing.jpg")
    assert single_image_url(session, listing, item) == "https://example.test/listing.jpg"


def test_single_image_url_falls_back_to_the_catalog_items_image(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"), image_url=None)
    assert single_image_url(session, listing, item) == "https://example.test/item.jpg"


def test_a_local_cache_path_is_never_treated_as_a_usable_image_url(session):
    """Regression guard for the exact bug storefront.common._is_web_url exists
    to catch: a local scan-ingest cache path is not something eBay's own
    servers could ever fetch (this app is Tailscale-only)."""
    item = _item(session, image_url="scan_ingest_data/cache/xy1/4.png")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="scan_ingest_data/cache/xy1/4-local.png")
    assert single_image_url(session, listing, item) is None


def test_a_lot_with_a_local_image_path_is_skipped_not_broken(session):
    session.add(BulkLot(title="No Photo Lot", card_count=10, price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                   letter_policy="Letter", parcel_policy="Parcel")
    assert rows == []
    assert "No Photo Lot" in skipped[0]


# --- row building skips what it can't safely export ---------------------

def test_build_single_rows_skips_listings_with_no_usable_image(session):
    item = _item(session, image_url=None)
    session.add(ShopListing(catalog_item_id=item.id, title="No Photo Card", price=Decimal("5"), image_url=None,
                            status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "No Photo Card" in skipped[0]


def test_build_single_rows_only_exports_the_requested_status(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(ShopListing(catalog_item_id=item.id, title="Active Card", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.add(ShopListing(catalog_item_id=item.id, title="Draft Card", price=Decimal("5"), status=ShopListingStatus.DRAFT))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Active Card"]


def test_single_row_set_name_comes_from_the_cards_own_set(session):
    session.add(CardSet(id="xy11", name="Steam Siege"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["set_name"] == "Steam Siege"


def test_lot_row_set_name_comes_from_the_lots_own_card_set(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(BulkLot(title="Obsidian Flames — bulk lot (21 cards)", set_id="sv3", card_count=21,
                        price=Decimal("15"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter", parcel_policy="Parcel")
    assert rows[0]["set_name"] == "Obsidian Flames"
