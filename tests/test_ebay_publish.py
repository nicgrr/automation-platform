from types import SimpleNamespace
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
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
    Approval, CardPrice, CardSet, CardVariant, CatalogItem, CatalogItemType, EbayCategoryMapping, EbayCredential,
    EbayListing, EbayListingLink, EbayListingLinkStatus, InventoryItem, ShopListing, ShopListingStatus, TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, monkeypatch):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: Settings(ebay_bulk_upload_only=False))
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
                          quantity=1, status=status,
                          # This fixture represents an operator-owned photo,
                          # not CatalogItem reference art. Direct publication
                          # now deliberately requires that distinction.
                          ebay_photo_paths=["https://example.test/card.jpg"])
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
        ebay_bulk_upload_only=False,
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="ful-1",
        ebay_return_policy_id="ret-1", ebay_merchant_location_key="loc-1",
    )


def test_bulk_only_mode_redirects_preview_and_post_without_writing(client, session, monkeypatch):
    make_listing(session)
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: Settings(ebay_bulk_upload_only=True))

    preview = client.get("/shop-listings/listing-1/ebay-preview", follow_redirects=False)
    publish = client.post("/shop-listings/listing-1/publish-to-ebay", follow_redirects=False)

    assert preview.status_code == 303
    assert preview.headers["location"].startswith("/ebay-export")
    assert publish.status_code == 303
    assert publish.headers["location"].startswith("/ebay-export")
    assert session.query(Approval).count() == 0


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


def test_one_piece_listing_is_ready_with_verified_au_category(client, session, monkeypatch):
    make_listing(session, game="one_piece")
    add_category_mapping(session, game="one_piece")
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "READY" in resp.text
    assert "category 183454" in resp.text


@pytest.mark.parametrize(
    "game",
    [
        "dragon_ball_fusion_world",
        "super_dragon_ball_heroes",
        "dragon_ball_super_divers",
        "dragon_ball_super",
        "dragon_ball_carddass",
    ],
)
def test_verified_dragon_ball_games_are_ready_for_au_category(client, session, monkeypatch, game):
    make_listing(session, game=game)
    add_category_mapping(session, game=game)
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "READY" in resp.text
    assert "category 183454" in resp.text


def test_unofficial_card_is_blocked_from_ebay_writes(session):
    listing = make_listing(session, game="super_dragon_ball_heroes")
    add_category_mapping(session, game="super_dragon_ball_heroes")
    card = session.get(TcgCard, listing.catalog_item_id)
    card.rarity = "Unofficial Art Card"
    session.commit()

    check = ebay_publish.publication_preflight(session, listing, _configured_settings())

    assert check.ready is False
    assert any("classified as unofficial" in error for error in check.errors)
    assert any("unauthorised trading-card reprints" in error for error in check.errors)


def test_post_without_confirmation_cannot_create_an_approval(client, session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.post("/shop-listings/listing-1/publish-to-ebay")

    assert resp.status_code == 400
    assert "review and confirm" in resp.json()["detail"]
    assert session.scalars(select(Approval)).all() == []


def test_reference_art_alone_is_blocked_for_an_ungraded_card(client, session, monkeypatch):
    listing = make_listing(session)
    listing.ebay_photo_paths = None
    add_category_mapping(session)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "BLOCKED" in resp.text
    assert "actual card" in resp.text
    assert "Reference art" in resp.text


def test_listing_studio_saves_channel_specific_copy_without_changing_storefront_title(client, session):
    listing = make_listing(session)
    response = client.post(
        "/shop-listings/listing-1/ebay-studio",
        data={
            "ebay_title": "Test Card 001 Scarlet Violet Rare Pokémon TCG English Near Mint",
            "ebay_description": "A detailed and accurate eBay-only description.",
            "price": "12.50", "quantity": "1", "condition": "Near Mint · Normal",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    saved = session.get(ShopListing, listing.id)
    assert saved.title == "Test Card #1"
    assert saved.ebay_title.startswith("Test Card 001")
    assert saved.ebay_description == "A detailed and accurate eBay-only description."
    assert saved.price == Decimal("12.50")


def test_active_direct_listing_preview_offers_revision_not_duplicate_publish(client, session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id="listing-1", sku="listing-1", marketplace_id="EBAY_AU",
        ebay_offer_id="offer-1", ebay_listing_id="ebay-1", status=EbayListingLinkStatus.ACTIVE,
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    body = client.get("/shop-listings/listing-1/ebay-preview").text

    assert "Update title, description, price &amp; photos on eBay" in body
    assert "value='revise'" in body
    assert "Publish live to eBay" not in body


def test_listing_studio_phone_ui_offers_camera_and_photo_library(client, session, monkeypatch):
    make_listing(session)
    add_category_mapping(session)
    settings = _configured_settings().model_copy(update={"anthropic_api_key": "test-key"})
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: settings)

    body = client.get("/shop-listings/listing-1/ebay-preview").text

    assert "Take new photo" in body
    assert "capture='environment'" in body
    assert "Choose photos" in body
    assert "Suggest title &amp; description" in body


def test_bulk_confirmed_listing_cannot_be_duplicated_through_direct_publish(client, session, monkeypatch):
    from automation_control.models import EbayExportBatch, EbayExportItem, EbayExportItemStatus

    make_listing(session)
    add_category_mapping(session)
    batch = EbayExportBatch(filename="bulk.csv", row_count=1, selected_count=1, created_by="test")
    session.add(batch)
    session.flush()
    session.add(EbayExportItem(
        batch_id=batch.id, sku="listing-1", kind="single", status=EbayExportItemStatus.CONFIRMED,
        ebay_listing_id="legacy-1",
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    body = client.get("/shop-listings/listing-1/ebay-preview").text

    assert "BLOCKED" in body
    assert "already live through Bulk Upload" in body
    assert "Publish live to eBay" not in body


def test_active_bulk_uploaded_single_preview_offers_safe_revision(client, session, monkeypatch):
    from automation_control.models import EbayExportBatch, EbayExportItem, EbayExportItemStatus

    make_listing(session)
    add_category_mapping(session)
    batch = EbayExportBatch(filename="bulk.csv", row_count=1, selected_count=1, created_by="test")
    session.add(batch)
    session.flush()
    session.add(EbayExportItem(
        batch_id=batch.id, sku="listing-1", kind="single", status=EbayExportItemStatus.CONFIRMED,
        ebay_listing_id="legacy-active-1",
    ))
    session.add(EbayListing(
        ebay_listing_id="legacy-active-1", title="Old title", currency="AUD",
        current_price_minor=999, listing_status="Active", quantity=1, raw_snapshot={},
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    body = client.get("/shop-listings/listing-1/ebay-preview").text

    assert "Existing eBay listing revision" in body
    assert "Update title, description, price &amp; photos on eBay" in body
    assert "value='revise'" in body
    assert "Publish live to eBay" not in body


def test_listing_studio_accepts_and_orders_actual_card_photos(client, session, monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    listing = make_listing(session)
    listing.ebay_photo_paths = None
    session.commit()
    monkeypatch.setattr(
        ebay_publish, "get_settings",
        lambda: Settings(ebay_bulk_upload_only=False, scan_media_dir=str(tmp_path)),
    )
    files = []
    for name, colour in (("front.jpg", "red"), ("back.png", "blue")):
        buffer = BytesIO()
        Image.new("RGB", (800, 1100), colour).save(buffer, "PNG" if name.endswith("png") else "JPEG")
        files.append(("photos", (name, buffer.getvalue(), "image/png" if name.endswith("png") else "image/jpeg")))

    response = client.post(f"/shop-listings/{listing.id}/ebay-photos", files=files, follow_redirects=False)

    assert response.status_code == 303
    session.expire_all()
    paths = session.get(ShopListing, listing.id).ebay_photo_paths
    assert len(paths) == 2
    assert all(Path(path).is_file() for path in paths)
    preview = client.get(f"/shop-listings/{listing.id}/ebay-preview").text
    assert "Main photo" in preview
    assert "Photo 2" in preview
    assert "Make main" in preview
    assert "Move later" in preview
    assert preview.count("Remove</button>") == 2
    assert "/ebay-photo/0?v=" in preview

    first_photo_url = preview.split("/ebay-photo/0?v=", 1)[1].split("'", 1)[0]
    client.post(f"/shop-listings/{listing.id}/ebay-photos/0/delete")
    shifted_preview = client.get(f"/shop-listings/{listing.id}/ebay-preview").text
    shifted_photo_url = shifted_preview.split("/ebay-photo/0?v=", 1)[1].split("'", 1)[0]
    assert shifted_photo_url != first_photo_url
    photo_response = client.get(f"/shop-listings/{listing.id}/ebay-photo/0")
    assert photo_response.headers["cache-control"] == "private, max-age=0"

    # Restore the two-photo order for the movement assertions below.
    listing = session.get(ShopListing, listing.id)
    listing.ebay_photo_paths = paths
    session.commit()

    client.post(f"/shop-listings/{listing.id}/ebay-photos/1/move", data={"direction": "left"})
    session.expire_all()
    assert session.get(ShopListing, listing.id).ebay_photo_paths == [paths[1], paths[0]]

    client.post(f"/shop-listings/{listing.id}/ebay-photos/1/move", data={"direction": "first"})
    session.expire_all()
    assert session.get(ShopListing, listing.id).ebay_photo_paths == paths


def test_listing_studio_accepts_iphone_heic_and_converts_it_to_jpeg(client, session, monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    listing = make_listing(session)
    listing.ebay_photo_paths = None
    session.commit()
    monkeypatch.setattr(
        ebay_publish, "get_settings",
        lambda: Settings(ebay_bulk_upload_only=False, scan_media_dir=str(tmp_path)),
    )
    buffer = BytesIO()
    Image.new("RGB", (800, 1100), "purple").save(buffer, "HEIF")

    response = client.post(
        f"/shop-listings/{listing.id}/ebay-photos",
        files={"photos": ("iphone-photo.heic", buffer.getvalue(), "image/heic")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    paths = session.get(ShopListing, listing.id).ebay_photo_paths
    assert len(paths) == 1
    assert paths[0].endswith(".jpg")
    with Image.open(paths[0]) as stored:
        assert stored.format == "JPEG"
        assert stored.size == (800, 1100)


def test_removing_all_custom_photos_does_not_resurrect_old_scan_on_next_upload(
    client, session, monkeypatch, tmp_path,
):
    from io import BytesIO
    from PIL import Image

    listing = make_listing(session)
    old_scan = tmp_path / "old-scan.jpg"
    Image.new("RGB", (800, 1100), "grey").save(old_scan, "JPEG")
    inventory = InventoryItem(
        catalog_item_id=listing.catalog_item_id,
        scan_image_path=str(old_scan),
    )
    session.add(inventory)
    session.flush()
    listing.inventory_item_id = inventory.id
    listing.ebay_photo_paths = [str(old_scan)]
    session.commit()
    monkeypatch.setattr(
        ebay_publish, "get_settings",
        lambda: Settings(ebay_bulk_upload_only=False, scan_media_dir=str(tmp_path)),
    )

    client.post(f"/shop-listings/{listing.id}/ebay-photos/0/delete")
    session.expire_all()
    assert session.get(ShopListing, listing.id).ebay_photo_paths == []

    buffer = BytesIO()
    Image.new("RGB", (800, 1100), "green").save(buffer, "JPEG")
    response = client.post(
        f"/shop-listings/{listing.id}/ebay-photos",
        files={"photos": ("new-front.jpg", buffer.getvalue(), "image/jpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    paths = session.get(ShopListing, listing.id).ebay_photo_paths
    assert len(paths) == 1
    assert paths[0] != str(old_scan)


def test_listing_studio_rejects_an_undersized_photo(client, session, monkeypatch, tmp_path):
    from io import BytesIO
    from PIL import Image

    listing = make_listing(session)
    listing.ebay_photo_paths = None
    session.commit()
    monkeypatch.setattr(
        ebay_publish, "get_settings",
        lambda: Settings(ebay_bulk_upload_only=False, scan_media_dir=str(tmp_path)),
    )
    buffer = BytesIO()
    Image.new("RGB", (200, 300), "white").save(buffer, "JPEG")

    response = client.post(
        f"/shop-listings/{listing.id}/ebay-photos",
        files={"photos": ("small.jpg", buffer.getvalue(), "image/jpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    session.expire_all()
    assert session.get(ShopListing, listing.id).ebay_photo_paths is None


def test_ai_suggestion_uses_local_photo_and_saves_reviewable_draft(client, session, monkeypatch, tmp_path):
    from PIL import Image
    from automation_control.card_recognition import EbayListingCopySuggestion

    listing = make_listing(session)
    photo = tmp_path / "actual-card.jpg"
    Image.new("RGB", (800, 1100), "navy").save(photo, "JPEG")
    listing.ebay_photo_paths = [str(photo)]
    session.commit()
    settings = _configured_settings().model_copy(update={"anthropic_api_key": "test-key"})
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: settings)
    seen = {}

    def fake_suggestion(paths, *, api_key, known_facts):
        seen.update(paths=paths, api_key=api_key, facts=known_facts)
        return EbayListingCopySuggestion(
            title="Test Card 001 Scarlet Violet Rare Pokemon TCG English Near Mint",
            description="The actual card shown is the card you will receive. Please review the photographs for condition.",
        )

    monkeypatch.setattr(ebay_publish, "suggest_ebay_listing_copy", fake_suggestion)

    response = client.post(f"/shop-listings/{listing.id}/ebay-ai-suggest", follow_redirects=False)

    assert response.status_code == 303
    assert "AI%20suggestion%20saved" in response.headers["location"]
    session.expire_all()
    saved = session.get(ShopListing, listing.id)
    assert saved.ebay_title.startswith("Test Card 001")
    assert "actual card shown" in saved.ebay_description
    assert seen["paths"] == [photo]
    assert seen["facts"]["card number"] == "1"
    assert session.scalars(select(Approval)).all() == []


def test_cleanup_queue_prioritises_short_live_titles_and_links_to_studio(client, session):
    listing = make_listing(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-1", ebay_listing_id="267000000001",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="267000000001", title="Test Card #1", currency="AUD",
        current_price_minor=999, listing_status="Active", quantity=1,
        raw_snapshot={"image_urls": ["https://example.test/card.jpg"]},
    ))
    session.commit()

    body = client.get("/ebay-listing-cleanup").text

    assert "1</strong><span>active on eBay" in body
    assert "Short title (12/80)" in body
    assert "Description not cached/verified" in body
    assert f"/shop-listings/{listing.id}/ebay-preview" in body
    assert "nothing on this page writes to eBay" in body


def test_cleanup_treats_complete_your_set_as_title_only(client, session):
    session.add(EbayListing(
        ebay_listing_id="267000000002",
        title="151 - Complete Your Master Set",
        currency="AUD", current_price_minor=240, listing_status="Active", quantity=100,
        raw_snapshot={},
    ))
    session.commit()

    body = client.get("/ebay-listing-cleanup").text

    assert "1</strong><span>bulk groups · title only" in body
    assert "Title-only bulk review" in body
    assert "photos intentionally ignored for this bulk group" in body
    assert "Description not cached/verified" not in body
    assert "No local actual-card photo" not in body


def test_cleanup_prioritises_actual_photos_for_card_singles_from_fifty_dollars(client, session):
    listing = make_listing(session)
    listing.ebay_photo_paths = None
    listing.price = Decimal("75.00")
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-2", ebay_listing_id="267000000003",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="267000000003",
        title="Test Card 001 Scarlet Violet Pokemon TCG English Near Mint",
        currency="AUD", current_price_minor=7500, listing_status="Active", quantity=1,
        raw_snapshot={"description": "A single card."},
    ))
    session.commit()

    body = client.get("/ebay-listing-cleanup").text

    assert "1</strong><span>card singles AU$50+" in body
    assert "1</strong><span>priority photo work" in body
    assert "High-value single (AU $75.00)" in body
    assert "eBay photos not cached/verified" in body
    assert "No local actual-card photo" in body


def test_cleanup_review_can_be_cleared_and_reappears_after_a_change(client, session):
    session.add(EbayListing(
        ebay_listing_id="267000000004", title="Pikachu PSA 9", currency="AUD",
        current_price_minor=29900, listing_status="Active", quantity=1,
        raw_snapshot={"description": "Graded card", "image_urls": ["https://example.test/pika.jpg"]},
    ))
    session.commit()

    body = client.get("/ebay-listing-cleanup").text
    marker = "name='fingerprint' value='"
    fingerprint = body.split(marker, 1)[1].split("'", 1)[0]
    response = client.post(
        "/ebay-listing-cleanup/267000000004/dismiss",
        data={"fingerprint": fingerprint}, follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Pikachu PSA 9" not in client.get("/ebay-listing-cleanup").text
    assert "Pikachu PSA 9" in client.get("/ebay-listing-cleanup?show=all").text

    remote = session.query(EbayListing).filter_by(ebay_listing_id="267000000004").one()
    remote.title = "Pikachu PSA 9 relisted"
    session.commit()
    assert "Pikachu PSA 9 relisted" in client.get("/ebay-listing-cleanup").text


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


def test_preflight_offers_an_explicit_override_for_a_materially_underpriced_card(client, session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("36.36")
    session.add(CardPrice(
        catalog_item_id=listing.catalog_item_id, source="ebay_active_median",
        price=Decimal("90.00"), currency="AUD",
    ))
    add_category_mapping(session)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get("/shop-listings/listing-1/ebay-preview")

    assert resp.status_code == 200
    assert "OVERRIDE AVAILABLE" in resp.text
    assert "Pricing safety hold" in resp.text
    assert "AUD $67.50" in resp.text
    assert "name='pricing_override'" in resp.text
    assert "value='listing-1' required" in resp.text
    assert "Publish live to eBay" in resp.text


def test_revision_allows_copy_and_photos_when_live_price_is_not_reduced(client, session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("55.00")
    session.add(CardPrice(
        catalog_item_id=listing.catalog_item_id, source="ebay_active_median",
        price=Decimal("93.28"), currency="AUD",
    ))
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-live-price", ebay_listing_id="ebay-live-price",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="ebay-live-price", title="Old title", currency="AUD",
        current_price_minor=5500, listing_status="Active", quantity=1, raw_snapshot={},
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get(f"/shop-listings/{listing.id}/ebay-preview")

    assert resp.status_code == 200
    assert "READY" in resp.text
    assert "does not reduce the current live eBay price" in resp.text
    assert "Update title, description, price &amp; photos on eBay" in resp.text


def test_revision_allows_a_modest_explicit_reduction_from_the_live_price(client, session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("9.15")
    session.add(CardPrice(
        catalog_item_id=listing.catalog_item_id, source="ebay_active_median",
        price=Decimal("23.64"), currency="AUD",
    ))
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-modest-reduction", ebay_listing_id="ebay-modest-reduction",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="ebay-modest-reduction", title="Old title", currency="AUD",
        current_price_minor=1034, listing_status="Active", quantity=1, raw_snapshot={},
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get(f"/shop-listings/{listing.id}/ebay-preview")

    assert resp.status_code == 200
    assert "READY" in resp.text
    assert "reduces by only AU $1.19" in resp.text
    assert "Update title, description, price &amp; photos on eBay" in resp.text


def test_revision_offers_an_override_for_a_large_reduction_from_an_underpriced_live_item(client, session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("7.00")
    session.add(CardPrice(
        catalog_item_id=listing.catalog_item_id, source="ebay_active_median",
        price=Decimal("23.64"), currency="AUD",
    ))
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-large-reduction", ebay_listing_id="ebay-large-reduction",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="ebay-large-reduction", title="Old title", currency="AUD",
        current_price_minor=1034, listing_status="Active", quantity=1, raw_snapshot={},
    ))
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)

    resp = client.get(f"/shop-listings/{listing.id}/ebay-preview")

    assert resp.status_code == 200
    assert "OVERRIDE AVAILABLE" in resp.text
    assert "Pricing safety hold" in resp.text
    assert "name='pricing_override'" in resp.text
    assert "Update title, description, price &amp; photos on eBay" in resp.text


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
async def test_a_second_simultaneous_publish_is_rejected_before_any_write(session, monkeypatch):
    make_listing(session)
    monkeypatch.setattr(ebay_publish, "get_settings", lambda: Settings(ebay_bulk_upload_only=False))
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


@pytest.mark.parametrize(("condition", "descriptor"), (
    ("Near Mint", "400010"),
    ("Lightly Played", "400015"),
    ("Moderately Played", "400016"),
    ("Heavily Played", "400017"),
))
def test_inventory_payload_uses_verified_ungraded_card_condition_descriptor(
    session, condition, descriptor,
):
    listing = make_listing(session)
    listing.condition = condition
    session.commit()
    catalog_item = session.get(CatalogItem, listing.catalog_item_id)
    tcg_card = session.get(TcgCard, listing.catalog_item_id)
    card_set = session.get(CardSet, tcg_card.set_id)

    payload = ebay_publish._build_inventory_item_payload(
        listing, catalog_item, tcg_card, card_set, None,
        ["https://example.test/card.jpg"], "Test Card", "Actual card shown.",
    )

    assert payload["condition"] == "USED_VERY_GOOD"
    assert payload["conditionDescriptors"] == [{"name": "40001", "values": [descriptor]}]


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
    assert result.headers["location"] == "/shop-listings"

    inventory_payload = adapter.upsert_inventory_item.await_args.args[1]
    assert inventory_payload["condition"] == "USED_VERY_GOOD"
    assert inventory_payload["conditionDescriptors"] == [{"name": "40001", "values": ["400010"]}]
    assert inventory_payload["product"]["title"] == "Test Card 1 Scarlet & Violet Pokémon TCG English"
    assert inventory_payload["product"]["aspects"]["Game"] == ["Pokémon TCG"]
    assert inventory_payload["product"]["imageUrls"] == ["https://example.test/card.jpg"]
    offer_payload = adapter.create_offer.await_args.args[0]
    assert offer_payload["listingDuration"] == "GTC"
    assert offer_payload["includeCatalogProductDetails"] is False

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == "listing-1"))
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.ebay_offer_id == "offer-123"
    assert link.ebay_listing_id == "ebay-999"
    assert link.last_pushed_quantity == 1

    approval = session.get(Approval, link.approval_id)
    assert approval.action == "ebay.publish_listing"
    assert approval.status.value == "approved"


@pytest.mark.asyncio
async def test_operator_price_override_changes_only_ebay_offer_price(session, monkeypatch):
    listing = make_listing(session)
    add_category_mapping(session)

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.upsert_inventory_item = AsyncMock(return_value=None)
    adapter.get_offers = AsyncMock(return_value=[])
    adapter.create_offer = AsyncMock(return_value="offer-override")
    adapter.publish_offer = AsyncMock(return_value="ebay-override")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_publish.publish_listing_to_ebay(
        listing.id,
        confirmation="publish",
        user="testuser",
        session=session,
        price_override=Decimal("10.99"),
    )

    offer_payload = adapter.create_offer.await_args.args[0]
    assert offer_payload["pricingSummary"]["price"]["value"] == "10.99"
    assert session.get(ShopListing, listing.id).price == Decimal("9.99")
    approval = session.scalar(select(Approval).where(Approval.action == "ebay.publish_listing"))
    assert approval.arguments["ebay_price"] == "10.99"
    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    assert link.ebay_listing_id == "ebay-override"


@pytest.mark.asyncio
async def test_explicit_pricing_safety_override_is_published_and_audited(session, monkeypatch):
    listing = make_listing(session)
    listing.price = Decimal("6.99")
    session.add(CardPrice(
        catalog_item_id=listing.catalog_item_id, source="ebay_active_median",
        price=Decimal("20.00"), currency="AUD",
    ))
    add_category_mapping(session)

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.upsert_inventory_item = AsyncMock(return_value=None)
    adapter.get_offers = AsyncMock(return_value=[])
    adapter.create_offer = AsyncMock(return_value="offer-safety-override")
    adapter.publish_offer = AsyncMock(return_value="ebay-safety-override")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_publish.publish_listing_to_ebay(
        listing.id,
        confirmation="publish",
        user="testuser",
        session=session,
        allow_pricing_safety_override=True,
    )

    approval = session.scalar(select(Approval).where(Approval.action == "ebay.publish_listing"))
    assert approval.arguments["pricing_safety_override"] is True
    offer_payload = adapter.create_offer.await_args.args[0]
    assert offer_payload["pricingSummary"]["price"]["value"] == "6.99"


@pytest.mark.asyncio
async def test_approved_revision_updates_owned_inventory_item_and_offer(session, monkeypatch):
    listing = make_listing(session)
    listing.ebay_title = "Professional Test Card 001 Scarlet Violet Pokémon TCG English Near Mint"
    listing.ebay_description = "The actual card is shown. Please review both photographs for condition details."
    listing.ebay_photo_paths = [
        "https://example.test/front.jpg", "https://example.test/back.jpg",
    ]
    add_category_mapping(session)
    link = EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-active", ebay_listing_id="ebay-active",
        status=EbayListingLinkStatus.ACTIVE,
    )
    session.add(link)
    session.commit()

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.get_inventory_item = AsyncMock(return_value={
        "product": {"title": "Old title", "imageUrls": ["https://example.test/old.jpg"]},
    })
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    response = await ebay_publish.revise_listing_on_ebay(
        listing.id, confirmation="revise", user="testuser", session=session,
    )

    assert response.status_code == 303
    payload = adapter.upsert_inventory_item.await_args.args[1]
    assert payload["product"]["title"] == listing.ebay_title
    assert payload["product"]["description"] == listing.ebay_description
    assert payload["product"]["imageUrls"] == listing.ebay_photo_paths
    adapter.update_offer.assert_awaited_once()
    approval = session.scalar(select(Approval).where(Approval.action == "ebay.revise_listing"))
    assert approval.status.value == "approved"
    assert link.status == EbayListingLinkStatus.ACTIVE
    assert link.last_error is None


@pytest.mark.asyncio
async def test_revision_retries_transient_availability_and_returns_to_studio(session, monkeypatch):
    from automation_control.adapters.ebay import EbayApiError

    listing = make_listing(session)
    add_category_mapping(session)
    link = EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-active", ebay_listing_id="ebay-active",
        status=EbayListingLinkStatus.ACTIVE,
    )
    session.add(link)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.get_inventory_item = AsyncMock(side_effect=[
        {"product": {"title": "Old title"}},
        {"availability": {"shipToLocationAvailability": {"quantity": 1}}},
    ])
    adapter.update_offer = AsyncMock(side_effect=[
        EbayApiError("errorId 25604: Availability not found"), None,
    ])
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    response = await ebay_publish.revise_listing_on_ebay(
        listing.id, confirmation="revise", user="testuser", session=session,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/shop-listings/{listing.id}/ebay-preview?notice=")
    assert adapter.get_inventory_item.await_count == 2
    assert adapter.update_offer.await_count == 2
    assert link.last_error is None


@pytest.mark.asyncio
async def test_failed_revision_redirects_back_to_listing_studio(session, monkeypatch):
    from automation_control.adapters.ebay import EbayApiError

    listing = make_listing(session)
    add_category_mapping(session)
    link = EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-active", ebay_listing_id="ebay-active",
        status=EbayListingLinkStatus.ACTIVE,
    )
    session.add(link)
    session.commit()
    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.get_inventory_item = AsyncMock(return_value={"product": {"title": "Old title"}})
    adapter.update_offer = AsyncMock(side_effect=EbayApiError("different eBay failure"))
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    response = await ebay_publish.revise_listing_on_ebay(
        listing.id, confirmation="revise", user="testuser", session=session,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/shop-listings/{listing.id}/ebay-preview?error=")
    assert "different%20eBay%20failure" in response.headers["location"]
    assert "different eBay failure" in link.last_error


@pytest.mark.asyncio
async def test_approved_legacy_single_revision_uses_trading_api_and_refreshes_mirror(session, monkeypatch):
    from automation_control.models import EbayExportBatch, EbayExportItem, EbayExportItemStatus

    listing = make_listing(session)
    listing.ebay_title = "Professional Test Card 001 Scarlet Violet Pokemon TCG English Near Mint"
    listing.ebay_description = "The actual card is shown. Please review both photographs for condition details."
    listing.ebay_photo_paths = ["https://example.test/front.jpg", "https://example.test/back.jpg"]
    add_category_mapping(session)
    batch = EbayExportBatch(filename="bulk.csv", row_count=1, selected_count=1, created_by="test")
    session.add(batch)
    session.flush()
    session.add(EbayExportItem(
        batch_id=batch.id, sku=listing.id, kind="single", status=EbayExportItemStatus.CONFIRMED,
        ebay_listing_id="legacy-active-2",
    ))
    remote = EbayListing(
        ebay_listing_id="legacy-active-2", title="Old legacy title", currency="AUD",
        current_price_minor=999, listing_status="Active", quantity=1,
        raw_snapshot={"image_urls": ["https://example.test/old.jpg"]},
    )
    session.add(remote)
    session.commit()

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.client = AsyncMock()
    monkeypatch.setattr(ebay_publish, "EbayTradingListingAdapter", lambda *a, **k: adapter)

    response = await ebay_publish.revise_listing_on_ebay(
        listing.id, confirmation="revise", user="testuser", session=session,
    )

    assert response.status_code == 303
    adapter.revise_copy_and_photos.assert_awaited_once_with(
        "legacy-active-2", title=listing.ebay_title, description=listing.ebay_description,
        picture_urls=listing.ebay_photo_paths, price=listing.price,
        condition_descriptor="400010",
    )
    adapter.client.aclose.assert_awaited_once()
    session.expire_all()
    refreshed = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == "legacy-active-2"))
    assert refreshed.title == listing.ebay_title
    assert refreshed.raw_snapshot["image_urls"] == listing.ebay_photo_paths
    approval = session.scalar(select(Approval).where(Approval.action == "ebay.revise_listing"))
    assert approval.arguments["revision_api"] == "trading"


@pytest.mark.asyncio
async def test_imported_live_link_without_inventory_offer_uses_trading_revision(session, monkeypatch):
    listing = make_listing(session)
    listing.ebay_title = "Reviewed Test Card 001 Pokemon TCG English Near Mint"
    listing.ebay_description = "Actual front and back photographs show the card offered for sale."
    add_category_mapping(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_listing_id="imported-live-1", ebay_offer_id=None,
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.add(EbayListing(
        ebay_listing_id="imported-live-1", title="Old imported title", currency="AUD",
        current_price_minor=999, listing_status="Active", quantity=1, raw_snapshot={},
    ))
    session.commit()

    monkeypatch.setattr(ebay_publish, "get_settings", _configured_settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.client = AsyncMock()
    monkeypatch.setattr(ebay_publish, "EbayTradingListingAdapter", lambda *a, **k: adapter)

    response = await ebay_publish.revise_listing_on_ebay(
        listing.id, confirmation="revise", user="testuser", session=session,
    )

    assert response.status_code == 303
    adapter.revise_copy_and_photos.assert_awaited_once()
    approval = session.scalar(select(Approval).where(Approval.action == "ebay.revise_listing"))
    assert approval.arguments["revision_api"] == "trading"


@pytest.mark.asyncio
async def test_revision_without_confirmation_cannot_write_or_create_approval(session, monkeypatch):
    listing = make_listing(session)
    session.add(EbayListingLink(
        shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        ebay_offer_id="offer-active", ebay_listing_id="ebay-active",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.commit()

    with pytest.raises(Exception) as exc_info:
        await ebay_publish.revise_listing_on_ebay(
            listing.id, confirmation="", user="testuser", session=session,
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert session.scalars(select(Approval)).all() == []


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
    listing = make_listing(session, status=ShopListingStatus.DRAFT)
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

    session.refresh(listing)
    assert listing.status == ShopListingStatus.ACTIVE
    assert listing.price == Decimal("9.99")
    assert listing.quantity == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("previous,expected", [
    ("/shop-listings?status=all&ebay=unpublished&category=pokemon&q=", "/shop-listings?status=all&ebay=unpublished&category=pokemon&q="),
    ("https://evil.example/shop-listings", "/shop-listings"),
    ("/ebay-listings?x=1", "/shop-listings"),
    (None, "/shop-listings"),
])
async def test_publish_returns_to_the_filtered_list_it_came_from(session, monkeypatch, previous, expected):
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
    params = {"_return_to": "/shop-listings/listing-1/ebay-preview"}
    if previous is not None:
        params["_return_prev"] = previous
    request = SimpleNamespace(query_params=params)

    result = await ebay_publish.publish_to_ebay(
        "listing-1", confirmation="publish", user="testuser", session=session, request=request,
    )
    assert result.status_code == 303
    assert result.headers["location"] == expected


def test_publish_route_over_http_keeps_the_list_filters(client, session, monkeypatch):
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
    filtered = "/shop-listings?status=all&ebay=unpublished&category=pokemon&q="

    resp = client.post("/shop-listings/listing-1/publish-to-ebay",
                       params={"_return_to": "/shop-listings/listing-1/ebay-preview", "_return_prev": filtered},
                       data={"confirmation": "publish"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == filtered
