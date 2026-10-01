import csv
import io
from dataclasses import dataclass
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.adapters.r2 import R2UploadError
from automation_control.database import Base
from automation_control.models import (
    BulkLot, CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType, InventoryItem, ShopListing,
    ShopListingStatus, TcgCard,
)
from scripts.export_ebay_bulk_listing import (
    EBAY_MIN_FIXED_PRICE, FIELDNAMES, _ebay_safe_image_url, _enforce_ebay_min_price, build_lot_rows,
    build_set_grouped_rows, build_single_rows, build_variation_rows, lot_shipping_policy, r2_is_configured,
    single_image_url, single_set_logo_url,
)


@dataclass
class FakeR2Settings:
    r2_endpoint_url: str = "https://r2.example.com"
    r2_access_key_id: str = "key"
    r2_secret_access_key: str = "secret"
    r2_bucket_name: str = "bucket"


def _fake_pipeline_config():
    from automation_control.listing_pipeline.config import EbayFieldsConfig, ImagesConfig, PipelineConfig, PricingConfig, TitleConfig

    return PipelineConfig(
        ebay=EbayFieldsConfig(category_id="1", condition_id="4000", item_location="x",
                              shipping_profile_name="x", return_profile_name="x", payment_profile_name="x",
                              custom_label_prefix="NIC"),
        images=ImagesConfig(base_url="https://pub.example.com", filename_pattern="{index}.jpg"),
        pricing=PricingConfig(auto_accept_pct="0.9", floor_price_pct=Decimal("0.70")),
        title=TitleConfig(term_order=["character"]),
    )


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
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
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


# --- a lot with no image_url of its own falls back to R2, via one of its --
# own member cards' scanned photos (real gap: sets built entirely by hand,
# with no tcgplayer/cardotaku-sourced image anywhere in the set at all) ----

def test_a_lot_with_no_image_falls_back_to_a_member_cards_scan_via_r2(session):
    session.add(CardSet(id="dbz-misc", name="Dragon Ball Z (Uncoded)"))
    session.add(CatalogCard(id="dbz-misc-1", set_id="dbz-misc", number="1", name="Card One",
                            local_image_path="/fake/dbz-misc-1.jpg"))
    session.add(BulkLot(title="Dragon Ball Z lot", set_id="dbz-misc", card_count=12,
                        price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    with patch("scripts.export_ebay_bulk_listing.get_settings", return_value=FakeR2Settings()), \
         patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.load_pipeline_config", return_value=_fake_pipeline_config()), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded", return_value="https://pub.example.com/lot-cover/x.jpg") as mock_upload:
        rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                       letter_policy="Letter", parcel_policy="Parcel")

    assert skipped == []
    assert rows[0]["image_url"] == "https://pub.example.com/lot-cover/x.jpg"
    assert mock_upload.call_args.args[1].startswith("lot-cover/")


def test_a_lot_with_its_own_real_image_never_touches_r2(session):
    session.add(BulkLot(title="Has A Logo", set_id="sv3", card_count=12, price=Decimal("8"),
                        image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    with patch("scripts.export_ebay_bulk_listing.ensure_uploaded") as mock_upload:
        rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                 letter_policy="Letter", parcel_policy="Parcel")

    assert rows[0]["image_url"] == "https://example.test/logo.png"
    mock_upload.assert_not_called()


def test_a_lot_whose_set_has_no_member_card_scan_either_is_still_skipped(session):
    session.add(CardSet(id="dbz-misc", name="Dragon Ball Z (Uncoded)"))
    session.add(CatalogCard(id="dbz-misc-1", set_id="dbz-misc", number="1", name="Card One", local_image_path=None))
    session.add(BulkLot(title="No Photo Anywhere", set_id="dbz-misc", card_count=12,
                        price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    with patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded") as mock_upload:
        rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                       letter_policy="Letter", parcel_policy="Parcel")

    assert rows == []
    assert "No Photo Anywhere" in skipped[0]
    mock_upload.assert_not_called()


def test_a_lot_cover_upload_failure_skips_the_lot_rather_than_raising(session):
    session.add(CardSet(id="dbz-misc", name="Dragon Ball Z (Uncoded)"))
    session.add(CatalogCard(id="dbz-misc-1", set_id="dbz-misc", number="1", name="Card One",
                            local_image_path="/fake/dbz-misc-1.jpg"))
    session.add(BulkLot(title="Dragon Ball Z lot", set_id="dbz-misc", card_count=12,
                        price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    with patch("scripts.export_ebay_bulk_listing.get_settings", return_value=FakeR2Settings()), \
         patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.load_pipeline_config", return_value=_fake_pipeline_config()), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded", side_effect=R2UploadError("boom")):
        rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                       letter_policy="Letter", parcel_policy="Parcel")

    assert rows == []
    assert "Dragon Ball Z lot" in skipped[0]


def test_a_lot_cover_permission_error_skips_the_lot_rather_than_crashing_the_page(session):
    """Same real incident as single_image_url's own permission-error
    test: build_lot_rows must survive a PermissionError from .exists()
    on one lot's cover-photo candidate rather than take down the whole
    /ebay-export page for every other lot too."""
    session.add(CardSet(id="dbz-misc", name="Dragon Ball Z (Uncoded)"))
    session.add(CatalogCard(id="dbz-misc-1", set_id="dbz-misc", number="1", name="Card One",
                            local_image_path="/fake/dbz-misc-1.jpg"))
    session.add(BulkLot(title="Dragon Ball Z lot", set_id="dbz-misc", card_count=12,
                        price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    with patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", side_effect=PermissionError(13, "Permission denied")):
        rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                       letter_policy="Letter", parcel_policy="Parcel")

    assert rows == []
    assert "Dragon Ball Z lot" in skipped[0]


# --- File Exchange rejects .webp/.avif/.heic/.tiff/.svg by extension,
# regardless of what the URL actually serves -- confirmed 2026-09-25 when
# a real Dragon Ball listing failed (ErrorCode 37, "PictureURL is invalid
# or missing") on 4 .webp-named URLs that each served a perfectly good,
# correctly sized PNG. ------------------------------------------------

def test_ebay_safe_image_url_accepts_ordinary_formats():
    for ext in ("jpg", "jpeg", "png", "gif", "bmp"):
        url = f"https://example.test/card.{ext}"
        assert _ebay_safe_image_url(url) == url


def test_ebay_safe_image_url_rejects_webp_even_though_its_a_real_web_url():
    assert _ebay_safe_image_url("https://cdn.example.test/card.webp") is None


def test_ebay_safe_image_url_rejects_other_formats_file_exchange_does_not_accept():
    for ext in ("avif", "heic", "tif", "tiff", "svg"):
        assert _ebay_safe_image_url(f"https://example.test/card.{ext}") is None


def test_ebay_safe_image_url_ignores_a_query_string_when_checking_the_extension():
    # The real failing URLs looked exactly like this: a CDN cache-busting
    # ?v=... after the extension.
    assert _ebay_safe_image_url("https://cdn.example.test/card.webp?v=123") is None
    assert _ebay_safe_image_url("https://cdn.example.test/card.png?v=123") == "https://cdn.example.test/card.png?v=123"


def test_ebay_safe_image_url_still_rejects_a_local_cache_path():
    assert _ebay_safe_image_url("scan_ingest_data/cache/xy1/4.png") is None


def test_ebay_safe_image_url_rejects_none():
    assert _ebay_safe_image_url(None) is None


def test_ebay_safe_image_url_accepts_an_extensionless_url():
    """Real regression (2026-09-26): an allowlist of known-good extensions
    also rejected every images.scrydex.com URL (Mega Evolution-era Pokemon
    cards' image host), which serves real JPGs at extensionless paths
    like ".../me5-62/small" -- 661 real cached images, all silently
    excluded from every eBay export. A denylist of the confirmed-bad
    extensions lets these through instead: "no extension" isn't the same
    claim as "confirmed webp/avif/heic/tiff/svg"."""
    url = "https://images.scrydex.com/pokemon/me5-62/small"
    assert _ebay_safe_image_url(url) == url


def test_single_image_url_falls_through_a_webp_listing_image_to_a_safe_catalog_image(session):
    """The bug this closes: a webp URL used to be returned immediately as
    "the" image, with no attempt at the fallback chain that might have a
    safe one. Now it's treated exactly like no URL at all."""
    item = _item(session, image_url="https://example.test/item.jpg")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="https://cdn.example.test/listing.webp")
    assert single_image_url(session, listing, item) == "https://example.test/item.jpg"


def test_single_image_url_returns_none_when_every_candidate_is_unsafe(session):
    item = _item(session, image_url="https://cdn.example.test/item.webp")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="https://cdn.example.test/listing.webp")
    assert single_image_url(session, listing, item) is None


# --- last resort: the seller's own scanned photo, via R2 -----------------
#
# Reached only once every stock-photo source above comes up empty. Every
# test here mocks get_settings/r2_is_configured/ensure_uploaded rather than
# touching real R2 -- these are unit tests for the fallback wiring, not an
# integration test of the adapter itself (see test_adapters_r2.py for that).

def _unpriced_item(session):
    """A catalog item with no usable stock photo anywhere -- exactly the
    shape that used to leave a listing skipped outright."""
    return _item(session, image_url=None)


def test_single_image_url_falls_back_to_r2_when_nothing_else_is_usable(session):
    item = _unpriced_item(session)
    session.add(InventoryItem(id="inv-1", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path="/fake/scan.jpg"))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-1")

    with patch("scripts.export_ebay_bulk_listing.get_settings", return_value=FakeR2Settings()), \
         patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.load_pipeline_config", return_value=_fake_pipeline_config()), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded", return_value="https://pub.example.com/scan/inv-1.jpg") as mock_upload:
        assert single_image_url(session, listing, item) == "https://pub.example.com/scan/inv-1.jpg"

    mock_upload.assert_called_once()
    assert mock_upload.call_args.args[1] == "scan/inv-1.jpg"


def test_single_image_url_never_touches_r2_when_a_stock_photo_already_works(session):
    """The whole fallback chain above this one exists precisely so R2 is
    the rare case, not the default -- a real stock photo must win without
    an upload attempt."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(InventoryItem(id="inv-2", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path="/fake/scan.jpg"))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-2")

    with patch("scripts.export_ebay_bulk_listing.ensure_uploaded") as mock_upload:
        assert single_image_url(session, listing, item) == "https://example.test/item.jpg"

    mock_upload.assert_not_called()


def test_single_image_url_skips_r2_entirely_when_it_is_not_configured(session):
    item = _unpriced_item(session)
    session.add(InventoryItem(id="inv-3", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path="/fake/scan.jpg"))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-3")

    with patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=False), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded") as mock_upload:
        assert single_image_url(session, listing, item) is None

    mock_upload.assert_not_called()


def test_single_image_url_skips_r2_when_there_is_no_scan_photo_on_file(session):
    item = _unpriced_item(session)
    session.add(InventoryItem(id="inv-4", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path=None))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-4")

    with patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded") as mock_upload:
        assert single_image_url(session, listing, item) is None

    mock_upload.assert_not_called()


def test_single_image_url_returns_none_rather_than_raising_when_the_upload_fails(session):
    """A build must not die partway through over one bad photo -- the row
    just ends up skipped with the usual reason, same as any other missing
    image."""
    item = _unpriced_item(session)
    session.add(InventoryItem(id="inv-5", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path="/fake/scan.jpg"))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-5")

    with patch("scripts.export_ebay_bulk_listing.get_settings", return_value=FakeR2Settings()), \
         patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.load_pipeline_config", return_value=_fake_pipeline_config()), \
         patch("scripts.export_ebay_bulk_listing.ensure_uploaded", side_effect=R2UploadError("boom")):
        assert single_image_url(session, listing, item) is None


def test_single_image_url_returns_none_rather_than_raising_on_a_permission_error(session):
    """Real production incident (2026-09-26): a scan's directory had no
    group-read/execute permission for the account ezbay.service runs as.
    Path.exists() itself raised PermissionError, which sat *outside* the
    old try/except R2UploadError block -- crashing the entire
    /ebay-export page (every listing, not just this one) rather than
    just skipping this one photo. PermissionError is a subclass of
    OSError, which the fix now catches around the .exists() call too."""
    item = _unpriced_item(session)
    session.add(InventoryItem(id="inv-6", card_id=None, variant=CardVariant.NORMAL, condition="Near Mint",
                              scan_image_path="/fake/scan.jpg"))
    session.commit()
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url=None, inventory_item_id="inv-6")

    with patch("scripts.export_ebay_bulk_listing.r2_is_configured", return_value=True), \
         patch("scripts.export_ebay_bulk_listing.Path.exists", side_effect=PermissionError(13, "Permission denied")):
        assert single_image_url(session, listing, item) is None


def test_build_single_rows_skips_a_listing_whose_only_image_is_webp(session):
    """Same skip path and same message as no image at all -- a listing
    File Exchange will reject anyway must never reach the CSV."""
    item = _item(session, image_url="https://cdn.example.test/item.webp")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="1"))
    session.add(ShopListing(catalog_item_id=item.id, title="Webp Card", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "Webp Card" in skipped[0]
    assert "no publicly-hosted image on file" in skipped[0]


def test_single_set_logo_url_rejects_a_webp_logo(session):
    session.add(CardSet(id="xy11", name="Steam Siege", logo_url="https://example.test/xy11-logo.webp"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    session.commit()

    assert single_set_logo_url(session, item) == ""


def test_a_lot_with_a_webp_image_is_skipped_not_shipped_broken(session):
    session.add(BulkLot(title="Webp Lot", card_count=10, price=Decimal("8"),
                        image_url="https://cdn.example.test/lot.webp", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                   letter_policy="Letter", parcel_policy="Parcel")
    assert rows == []
    assert "Webp Lot" in skipped[0]


# --- row building skips what it can't safely export ---------------------

def test_build_single_rows_skips_listings_with_no_usable_image(session):
    item = _item(session, image_url=None)
    session.add(ShopListing(catalog_item_id=item.id, title="No Photo Card", price=Decimal("5"), image_url=None,
                            status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "No Photo Card" in skipped[0]


def test_build_single_rows_includes_a_different_games_card(session):
    """Fixed 2026-09-22 (Nic explicitly asked for this once Dragon Ball
    listings existed): any TCG card qualifies, not just Pokemon -- a
    Dragon Ball card is included, carrying its own "game" so
    convert_row's GAME_ITEM_SPECIFICS can label it correctly instead of
    the old hardcoded Franchise=Pokémon on every row."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="dragon_ball_super_divers"))
    session.add(ShopListing(catalog_item_id=item.id, title="Son Goku", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert skipped == []
    assert rows[0]["game"] == "dragon_ball_super_divers"


def test_build_single_rows_excludes_an_item_with_no_tcgcard_row_at_all(session):
    """A Collectible/Accessory/etc. has no TcgCard row at all -- still
    excluded, since this export is card-shaped, not catalogue-wide,
    regardless of which game a real card would have belonged to."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(ShopListing(catalog_item_id=item.id, title="Ultra Pro Sleeves", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "not a TCG card" in skipped[0]


# --- every row a builder can produce must actually write to the CSV -----
# Real production bug (2026-09-26): build_single_rows/build_lot_rows both
# add a "game" key to every row (needed downstream by
# convert_to_ebay_template.py's GAME_ITEM_SPECIFICS), but FIELDNAMES was
# never updated to include it -- csv.DictWriter.writerows raised
# ValueError on every single real export, "Nic" reported this live as
# "still having issues with uploading the results." A dict with an extra
# key is the one shape a hand-maintained skip/exclude test would never
# catch, since nothing here was ever skipped -- only main()'s actual
# csv.writer call.

def _assert_csv_writable(rows: list[dict]) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)  # raises ValueError if any row has a key FIELDNAMES doesn't


def test_build_single_rows_output_is_csv_writable(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="dragon_ball_super"))
    session.add(ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows  # a row with no rows at all would pass writerows trivially and prove nothing
    _assert_csv_writable(rows)


def test_build_lot_rows_output_is_csv_writable(session):
    session.add(BulkLot(title="Test Lot", card_count=10, price=Decimal("8"), game="pokemon",
                        image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter", parcel_policy="Parcel")
    assert rows
    _assert_csv_writable(rows)


def test_build_variation_rows_output_is_csv_writable(session):
    session.add(CardSet(id="xy11", name="Steam Siege"))
    session.add(BulkLot(set_id="xy11", title="Steam Siege lot", card_count=10, price=Decimal("8"),
                        image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11"))
    session.add(ShopListing(catalog_item_id=item.id, title="Cheap Card", price=Decimal("1"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows
    _assert_csv_writable(rows)


def test_build_set_grouped_rows_output_is_csv_writable(session):
    session.add(CardSet(id="xy11", name="Steam Siege"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11"))
    session.add(ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    singles, variation_groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    all_rows = singles + [row for rows in variation_groups.values() for row in rows]
    assert all_rows
    _assert_csv_writable(all_rows)


# --- eBay's absolute $1 FixedPrice minimum -------------------------------
# Real rejection (2026-09-26, ErrorCode 73): "The price in the listing is
# either invalid or below the minimum price of 1.0." Two real cards, both
# genuinely priced from real market data, landed at $0.26 and $0.54 AUD --
# under list_for_sale.py's own profit floor via its documented 2x-market
# cap tension, not a bug in that script, just a case its floor doesn't
# reach. This is a separate, absolute platform constraint.

def test_enforce_ebay_min_price_raises_a_row_below_the_minimum():
    rows = [{"price": "0.26"}]
    raised = _enforce_ebay_min_price(rows)
    assert raised == 1
    assert rows[0]["price"] == f"{EBAY_MIN_FIXED_PRICE:.2f}"


def test_enforce_ebay_min_price_leaves_a_row_at_or_above_the_minimum_alone():
    rows = [{"price": "1.00"}, {"price": "5.00"}]
    raised = _enforce_ebay_min_price(rows)
    assert raised == 0
    assert [r["price"] for r in rows] == ["1.00", "5.00"]


def test_enforce_ebay_min_price_reports_the_count_raised():
    rows = [{"price": "0.10"}, {"price": "0.20"}, {"price": "5.00"}]
    assert _enforce_ebay_min_price(rows) == 2


def test_a_variation_child_priced_under_a_dollar_is_raised_to_the_minimum(session):
    """The real failure mode: a set-grouped variation listing whose child
    card is genuinely worth well under $1 after its own real pricing."""
    session.add(CardSet(id="fb08", name="Saiyan's Pride"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="dragon_ball_fusion_world", set_id="fb08"))
    session.add(ShopListing(catalog_item_id=item.id, title="Cheap Card", price=Decimal("0.26"),
                            status=ShopListingStatus.ACTIVE))
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    all_rows = singles + [row for rows in groups.values() for row in rows]
    for r in all_rows:
        assert Decimal(r["price"]) < EBAY_MIN_FIXED_PRICE  # not yet enforced -- that's main()'s/the route's own job

    _enforce_ebay_min_price(all_rows)
    for r in all_rows:
        assert Decimal(r["price"]) >= EBAY_MIN_FIXED_PRICE


def test_build_single_rows_only_exports_the_requested_status(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
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


# --- excluding singles already covered by an active bulk lot -----------

def _set_with_lot(session, set_id="sv3", set_name="Obsidian Flames"):
    session.add(CardSet(id=set_id, name=set_name))
    session.add(BulkLot(title=f"{set_name} — bulk lot (30 cards)", set_id=set_id, card_count=30,
                        price=Decimal("20"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))


def test_a_cheap_single_from_a_lot_covered_set_is_excluded(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "already covered by an active bulk lot" in skipped[0]


def test_a_single_above_the_lot_threshold_from_a_lot_covered_set_is_still_exported(session):
    """A card priced above the bulk lot's own price ceiling was never
    eligible for the lot in the first place, so it isn't the same
    physical card and is safe to sell individually."""
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="199"))
    session.add(ShopListing(catalog_item_id=item.id, title="Rare #199", price=Decimal("50"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Rare #199"]


def test_a_cheap_single_from_a_set_with_no_lot_is_still_exported(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="no-lot-set", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Common #4"]


def test_passing_none_disables_the_lot_overlap_check(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter",
                                lot_overlap_max_price=None)
    assert [r["title"] for r in rows] == ["Common #4"]


# --- build_variation_rows: the exact population build_single_rows excludes --

def test_variation_rows_is_exactly_what_single_rows_excludes(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    singles, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert singles == []
    assert [r["title"] for r in variations] == ["Common #4"]


def test_variation_rows_discounts_the_normal_printing_only(session):
    """Per the user's request (2026-09-21): individual market pricing
    stays, but a Normal (non-foil) printing -- the least desirable, bulk
    of a set's filler -- undercuts a bit. Reverse Holo/Holo/pattern
    printings of the same card are never touched."""
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    normal_inv = InventoryItem(card_id=item.id, variant=CardVariant.NORMAL, condition="Near Mint", quantity=1)
    rh_inv = InventoryItem(card_id=item.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=1)
    session.add_all([normal_inv, rh_inv])
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4 Normal", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=normal_inv.id))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4 RH", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=rh_inv.id))
    session.commit()

    rows, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter",
                                   normal_discount_pct=Decimal("40"))
    by_title = {r["title"]: r["price"] for r in rows}
    assert by_title["Common #4 Normal"] == "1.20"  # 40% off $2.00
    assert by_title["Common #4 RH"] == "2.00"  # untouched


def test_variation_rows_discount_defaults_to_forty_percent(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    inv = InventoryItem(card_id=item.id, variant=CardVariant.NORMAL, condition="Near Mint", quantity=1)
    session.add(inv)
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=inv.id))
    session.commit()

    rows, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["price"] == "1.20"


def test_variation_rows_ignores_a_card_above_max_price_even_in_a_lot_covered_set(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="199"))
    session.add(ShopListing(catalog_item_id=item.id, title="Rare #199", price=Decimal("50"), status=ShopListingStatus.ACTIVE))
    session.commit()

    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert variations == []


def test_variation_rows_ignores_a_cheap_card_from_a_set_with_no_lot(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="no-lot-set", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert variations == []


# --- variant and set logo, for variation-listing labels/pictures --------

def test_single_variant_comes_from_the_listings_own_inventory_item(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    inv = InventoryItem(card_id=item.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=1)
    session.add(inv)
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=inv.id))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["variant"] == "Reverse Holo"


def test_single_variant_is_blank_with_no_linked_inventory_item(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["variant"] == ""


def test_single_set_logo_url_comes_from_the_cards_set(session):
    session.add(CardSet(id="xy11", name="Steam Siege", logo_url="https://example.test/xy11-logo.png"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["set_logo_url"] == "https://example.test/xy11-logo.png"


# --- build_set_grouped_rows: one variation listing per set at min_set_size,
# no price ceiling, no Bulk Master lot dependency -- Nic's own request
# 2026-09-22 ("every set becomes one listing"), independent of build_variation_rows
# above (still cheap-commons-in-an-active-lot only, for whatever else uses it) --

def _card(session, item_id: str, *, set_id: str, number: str, title: str, price: str, game: str = "pokemon",
         rarity: str | None = "Common", language: str = "English"):
    # rarity defaults to "Common" -- the eBay-fee-markup tests below rely
    # on that default meaning "not marked up" for a plain Pokemon card;
    # the discount/no-discount tests above this section don't care about
    # rarity at all, so the default keeps them unaffected by the markup
    # feature added after they were written. language likewise defaults to
    # the ordinary case (matching TcgCard.language's own column default)
    # so every existing call site is unaffected by the field's addition.
    session.add(CatalogItem(id=item_id, name=title, item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number=number, rarity=rarity, language=language))
    session.add(ShopListing(catalog_item_id=item_id, title=title, price=Decimal(price), status=ShopListingStatus.ACTIVE))


def test_set_grouped_rows_groups_a_set_at_or_above_min_size(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    _card(session, "c1", set_id="sv3", number="1", title="Common #1", price="0.50")
    _card(session, "c2", set_id="sv3", number="2", title="Common #2", price="0.50")
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert singles == []
    assert {r["title"] for r in groups["Obsidian Flames"]} == {"Common #1", "Common #2"}


def test_set_grouped_rows_leaves_a_smaller_set_as_singles(session):
    session.add(CardSet(id="sv4", name="Paldean Fates"))
    _card(session, "c1", set_id="sv4", number="1", title="Rare #1", price="50.00")
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert groups == {}
    assert [r["title"] for r in singles] == ["Rare #1"]


def test_set_grouped_rows_applies_no_price_discount(session):
    """Unlike build_variation_rows, every card keeps its own real price --
    Nic's explicit choice (2026-09-22) once expensive cards join the
    grouping too, not just the cheap commons that function discounts."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    _card(session, "c1", set_id="sv3", number="1", title="Expensive Card", price="120.00")
    _card(session, "c2", set_id="sv3", number="2", title="Another Card", price="80.00")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    by_title = {r["title"]: r["price"] for r in groups["Obsidian Flames"]}
    assert by_title == {"Expensive Card": "120.00", "Another Card": "80.00"}


def test_set_grouped_rows_ignores_bulk_lot_status_entirely(session):
    """No active Bulk Master lot needed -- purely a set-size cutoff,
    unlike build_variation_rows's lot-gating."""
    session.add(CardSet(id="sv5", name="No Lot Set"))
    _card(session, "c1", set_id="sv5", number="1", title="Card A", price="5.00")
    _card(session, "c2", set_id="sv5", number="2", title="Card B", price="5.00")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert len(groups["No Lot Set"]) == 2


# --- eBay fee markup: Dragon Ball (any rarity) + rare-or-better Pokemon --
# Nic's request 2026-09-22, added to build_set_grouped_rows only --
# build_single_rows itself keeps returning the real, unmodified price
# (see the test right above this section, and build_single_rows' own
# tests elsewhere in this file).

def test_set_grouped_rows_marks_up_a_dragon_ball_card_regardless_of_rarity(session):
    session.add(CardSet(id="dbsd-sdv11", name="Dragon Ball Super Divers SDV11"))
    _card(session, "c1", set_id="dbsd-sdv11", number="1", title="Card A", price="4.02", game="dragon_ball_super_divers")
    _card(session, "c2", set_id="dbsd-sdv11", number="2", title="Card B", price="3.08", game="dragon_ball_super_divers")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    by_title = {r["title"]: r["price"] for r in groups["Dragon Ball Super Divers SDV11"]}
    assert by_title == {"Card A": "4.54", "Card B": "3.48"}  # +13%, rounded


def test_set_grouped_rows_marks_up_a_rare_pokemon_card(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(TcgCard(catalog_item_id="c1", game="pokemon", set_id="sv3", number="1", rarity="Rare"))
    session.add(CatalogItem(id="c1", name="Rare Card", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(ShopListing(catalog_item_id="c1", title="Rare Card", price=Decimal("10.00"), status=ShopListingStatus.ACTIVE))
    session.add(TcgCard(catalog_item_id="c2", game="pokemon", set_id="sv3", number="2", rarity="Rare"))
    session.add(CatalogItem(id="c2", name="Rare Card 2", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(ShopListing(catalog_item_id="c2", title="Rare Card 2", price=Decimal("10.00"), status=ShopListingStatus.ACTIVE))
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert groups["Obsidian Flames"][0]["price"] == "11.30"  # +13% of $10.00


def test_set_grouped_rows_does_not_mark_up_common_or_uncommon_pokemon(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    for i, rarity in enumerate(["Common", "Uncommon"], start=1):
        session.add(TcgCard(catalog_item_id=f"c{i}", game="pokemon", set_id="sv3", number=str(i), rarity=rarity))
        session.add(CatalogItem(id=f"c{i}", name=f"Card {i}", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
        session.add(ShopListing(catalog_item_id=f"c{i}", title=f"Card {i}", price=Decimal("2.00"), status=ShopListingStatus.ACTIVE))
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert {r["price"] for r in groups["Obsidian Flames"]} == {"2.00"}


def test_markup_also_applies_to_a_single_not_a_group(session):
    """A Dragon Ball card whose set has only 1 active listing (stays a
    single, not a variation group) still gets the markup -- it's applied
    to build_set_grouped_rows' whole row pool before the set-size split."""
    session.add(CardSet(id="dbsd-sdv8", name="Dragon Ball Super Divers SDV8"))
    _card(session, "c1", set_id="dbsd-sdv8", number="1", title="Lone Card", price="10.00", game="dragon_ball_super_divers")
    session.commit()

    singles, _groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert singles[0]["price"] == "11.30"


def test_set_grouped_rows_includes_a_non_pokemon_set(session):
    session.add(CardSet(id="dbsd-sdv11", name="Dragon Ball Super Divers SDV11"))
    _card(session, "c1", set_id="dbsd-sdv11", number="SDV11-PUR1", title="Son Goku: BR", price="4.02", game="dragon_ball_super_divers")
    _card(session, "c2", set_id="dbsd-sdv11", number="SDV11-PUR2", title="Son Gohan: SH", price="3.08", game="dragon_ball_super_divers")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    rows = groups["Dragon Ball Super Divers SDV11"]
    assert {r["game"] for r in rows} == {"dragon_ball_super_divers"}


# --- language: real incident 2026-09-27 -- a set holding both English and
# Japanese prints of the same card numbers was one listing, silently
# showing the Japanese copy under the English stock photo. A set is now
# split by language before it's split into a group at all. ---------------

def test_set_grouped_rows_splits_a_set_that_mixes_languages(session):
    session.add(CardSet(id="fb10", name="Cross Force"))
    _card(session, "c1", set_id="fb10", number="1", title="Card One", price="5", game="dragon_ball_fusion_world", language="English")
    _card(session, "c2", set_id="fb10", number="2", title="Card Two", price="5", game="dragon_ball_fusion_world", language="English")
    _card(session, "c3", set_id="fb10", number="3", title="Card Three (JP)", price="5", game="dragon_ball_fusion_world", language="Japanese")
    _card(session, "c4", set_id="fb10", number="4", title="Card Four (JP)", price="5", game="dragon_ball_fusion_world", language="Japanese")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert set(groups) == {"Cross Force", "Cross Force (Japanese)"}
    assert {r["title"] for r in groups["Cross Force"]} == {"Card One", "Card Two"}
    assert {r["title"] for r in groups["Cross Force (Japanese)"]} == {"Card Three (JP)", "Card Four (JP)"}


def test_set_grouped_rows_bare_english_key_carries_no_suffix(session):
    """The overwhelming majority of this catalogue: a set that's only ever
    English must produce the exact same group key it always has, or every
    existing sku/title downstream (and every test asserting on one) would
    break for no reason."""
    session.add(CardSet(id="sv9", name="Journey Together"))
    _card(session, "c1", set_id="sv9", number="1", title="Card One", price="5")
    _card(session, "c2", set_id="sv9", number="2", title="Card Two", price="5")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert set(groups) == {"Journey Together"}


def test_set_grouped_rows_a_lone_japanese_card_falls_out_as_a_single(session):
    """One Japanese card alone in an otherwise-English set doesn't reach
    min_set_size on its own -- it becomes a single, not a mislabeled
    addition to the English group."""
    session.add(CardSet(id="fb03", name="Ultra Limit"))
    _card(session, "c1", set_id="fb03", number="1", title="Card One", price="5", game="dragon_ball_fusion_world", language="English")
    _card(session, "c2", set_id="fb03", number="2", title="Card Two", price="5", game="dragon_ball_fusion_world", language="English")
    _card(session, "c3", set_id="fb03", number="3", title="Jiren (JP)", price="5", game="dragon_ball_fusion_world", language="Japanese")
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert set(groups) == {"Ultra Limit"}
    assert {r["title"] for r in singles} == {"Jiren (JP)"}


def test_single_language_reads_the_cards_own_language(session):
    from scripts.export_ebay_bulk_listing import single_language
    from automation_control.models import CatalogItem, TcgCard

    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="dragon_ball_fusion_world", language="Japanese"))
    session.commit()
    assert single_language(session, item) == "Japanese"


def test_single_language_defaults_to_english_with_no_tcg_card(session):
    from scripts.export_ebay_bulk_listing import single_language

    item = _item(session, image_url="https://example.test/item.jpg")
    session.commit()
    assert single_language(session, item) == "English"
