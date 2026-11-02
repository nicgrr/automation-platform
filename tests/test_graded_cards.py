"""A slab may only reach eBay with a reviewed price, measured parcel and tracked policy."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import automation_control.ebay_publish as ebay_publish
from automation_control.config import Settings
from automation_control.database import Base
from automation_control.ebay_oauth import INVENTORY_WRITE_SCOPE
from automation_control.graded_cards import (
    POLICY_KEY, confirm_price, graded_condition, identity, review_data, save_parcel, save_review_data,
)
from automation_control.listing_quality import generated_ebay_title
from automation_control.models import (
    CardSet, CatalogItem, CatalogItemType, EbayCategoryMapping, EbayCredential, EbayListingLink,
    InventoryItem, InventoryStatus, PlatformConfig, ShopListing, ShopListingStatus, TcgCard,
)

PHOTOS = ["https://example.test/front.jpg", "https://example.test/back.jpg"]


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def settings():
    return Settings(
        ebay_bulk_upload_only=False,
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key="k", app_public_base_url="https://example.test",
        ebay_payment_policy_id="pay-1", ebay_fulfillment_policy_id="letter-1",
        ebay_return_policy_id="ret-1", ebay_merchant_location_key="loc-1",
    )


def make_slab(session, *, reviewed=True):
    session.add(CardSet(id="bt18", name="Dawn of the Z-Legends"))
    session.add(CatalogItem(id="slab", item_type=CatalogItemType.GRADED_CARD, name="Shroom & Salsa"))
    session.add(TcgCard(catalog_item_id="slab", game="dragon_ball_super", set_id="bt18", number="BT18-133"))
    stock = InventoryItem(catalog_item_id="slab", condition="Graded", quantity=1, status=InventoryStatus.AVAILABLE,
                          grading_company="CGC", grade="Pristine 10", certification_number="4245735015")
    session.add(stock)
    session.flush()
    listing = ShopListing(id="listing-1", catalog_item_id="slab", inventory_item_id=stock.id, title="Slab",
                          condition="Graded", quantity=1, price=Decimal("34.99"), currency="AUD",
                          status=ShopListingStatus.DRAFT, ebay_photo_paths=list(PHOTOS))
    session.add(listing)
    session.add(EbayCategoryMapping(
        marketplace_id="EBAY_AU", catalog_item_type="tcg_card", game="dragon_ball_super", ebay_category_id="183454",
    ))
    session.add(EbayCredential(
        id=1, environment="sandbox", encrypted_access_token=b"a", encrypted_refresh_token=b"r",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1), scopes=INVENTORY_WRITE_SCOPE,
    ))
    session.flush()
    if reviewed:
        save_review_data(session, listing, {
            "confirmed_price": "34.99", "price_confirmed_at": "2026-11-03T00:00:00+00:00",
            "reviewed_identity": identity(stock),
            "package": {"weight_g": "120", "length_cm": "19.2", "width_cm": "14", "height_cm": "3",
                        "measured_at": "2026-11-03T00:00:00+00:00"},
        })
        add_policy(session)
    session.commit()
    return listing


def add_policy(session):
    session.add(PlatformConfig(key=POLICY_KEY, value={
        "environment": "sandbox", "marketplace_id": settings().ebay_marketplace_id, "service": "AU_Regular",
        "shipping_cost_type": "CALCULATED", "verified_at": "2026-11-03", "fulfillment_policy_id": "tracked-1",
    }))


def errors_for(session, listing):
    return ebay_publish.publication_preflight(session, listing, settings()).errors


@pytest.mark.parametrize("grade,value", [("Pristine 10", "275020"), ("Mint 9", "275022")])
def test_condition_uses_verified_descriptors(grade, value):
    fields = graded_condition(InventoryItem(grading_company="CGC", grade=grade, certification_number="4245735015"))
    assert fields["condition"] == "LIKE_NEW"
    assert fields["conditionDescriptors"] == [
        {"name": "27501", "values": ["275015"]},
        {"name": "27502", "values": [value]},
        {"name": "27503", "additionalInfo": "4245735015"},
    ]


@pytest.mark.parametrize("stock", [
    {"grading_company": "PSA", "grade": "Mint 9", "certification_number": "1"},
    {"grading_company": "CGC", "grade": "Excellent 5", "certification_number": "1"},
    {"grading_company": "CGC", "grade": "Mint 9", "certification_number": "x" * 21},
    {"grading_company": "CGC", "grade": "Mint 9", "certification_number": ""},
])
def test_unverified_condition_fails_closed(stock):
    with pytest.raises(ValueError):
        graded_condition(InventoryItem(**stock))


def test_fully_reviewed_slab_passes_preflight(session):
    assert errors_for(session, make_slab(session)) == ()


def test_unreviewed_slab_is_blocked_on_price_parcel_and_policy(session):
    errors = " ".join(errors_for(session, make_slab(session, reviewed=False)))
    assert "Confirm this exact price" in errors
    assert "packed slab weight" in errors
    assert "tracked-parcel policy" in errors


def test_changing_the_price_after_review_blocks_publishing(session):
    listing = make_slab(session)
    listing.price = Decimal("99.00")
    assert any("Confirm this exact price" in e for e in errors_for(session, listing))


def test_changing_the_certificate_after_review_blocks_publishing(session):
    listing = make_slab(session)
    session.get(InventoryItem, listing.inventory_item_id).certification_number = "999"
    assert any("Confirm this exact price" in e for e in errors_for(session, listing))


def test_second_stock_record_for_a_certificate_blocks_publishing(session):
    listing = make_slab(session)
    session.add(CatalogItem(id="slab-2", item_type=CatalogItemType.GRADED_CARD, name="Duplicate slab"))
    session.add(InventoryItem(catalog_item_id="slab-2", condition="Graded", quantity=1, status=InventoryStatus.AVAILABLE,
                              grading_company="cgc", grade="Pristine 10", certification_number="4245735015"))
    session.commit()
    assert any("grading certificate" in e for e in errors_for(session, listing))


def test_policy_for_another_environment_is_rejected(session):
    listing = make_slab(session)
    session.get(PlatformConfig, POLICY_KEY).value = {
        **session.get(PlatformConfig, POLICY_KEY).value, "environment": "production",
    }
    session.commit()
    assert any("tracked-parcel policy" in e for e in errors_for(session, listing))


def test_title_keeps_the_exact_label_grade(session):
    listing = make_slab(session)
    title = generated_ebay_title(
        listing, session.get(CatalogItem, "slab"), session.get(TcgCard, "slab"), session.get(CardSet, "bt18"),
        session.get(InventoryItem, listing.inventory_item_id),
    )
    assert title.startswith("CGC Pristine 10 ")
    assert len(title) <= 80


def test_review_forms_store_values_without_touching_marketplace(session):
    listing = make_slab(session, reviewed=False)
    confirm_price("listing-1", price="29.50", user="nic", session=session)
    save_parcel("listing-1", weight_g="120", length_cm="19", width_cm="14", height_cm="3", user="nic", session=session)
    session.commit()
    data = review_data(session, listing)
    assert listing.price == Decimal("29.50") and listing.status == ShopListingStatus.DRAFT
    assert data["confirmed_price"] == "29.50" and data["package"]["measured_by"] == "nic"


@pytest.mark.asyncio
async def test_publish_sends_slab_parcel_and_tracked_policy(session, monkeypatch):
    make_slab(session)
    monkeypatch.setattr(ebay_publish, "get_settings", settings)
    monkeypatch.setattr(ebay_publish, "ebay_services", lambda s: (object(), object()))
    monkeypatch.setattr(ebay_publish, "valid_user_access_token", AsyncMock(return_value="tok"))
    adapter = AsyncMock()
    adapter.get_offers = AsyncMock(return_value=[])
    adapter.create_offer = AsyncMock(return_value="offer-1")
    adapter.publish_offer = AsyncMock(return_value="ebay-1")
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    await ebay_publish.publish_to_ebay("listing-1", confirmation="publish", user="nic", session=session)

    inventory = adapter.upsert_inventory_item.await_args.args[1]
    assert inventory["condition"] == "LIKE_NEW"
    assert inventory["packageWeightAndSize"]["weight"] == {"value": 0.12, "unit": "KILOGRAM"}
    assert inventory["packageWeightAndSize"]["dimensions"] == {"length": 20, "width": 14, "height": 3, "unit": "CENTIMETER"}
    assert inventory["product"]["aspects"]["Certification Number"] == ["4245735015"]
    assert inventory["product"]["imageUrls"] == PHOTOS
    assert adapter.create_offer.await_args.args[0]["listingPolicies"]["fulfillmentPolicyId"] == "tracked-1"


@pytest.mark.asyncio
async def test_incomplete_review_never_reaches_ebay_or_creates_a_link(session, monkeypatch):
    make_slab(session, reviewed=False)
    monkeypatch.setattr(ebay_publish, "get_settings", settings)
    adapter = AsyncMock()
    monkeypatch.setattr(ebay_publish, "EbayWriteAdapter", lambda *a, **k: adapter)

    with pytest.raises(Exception):
        await ebay_publish.publish_to_ebay("listing-1", confirmation="publish", user="nic", session=session)

    adapter.upsert_inventory_item.assert_not_awaited()
    assert session.scalar(select(EbayListingLink)) is None


def test_slab_resolves_the_card_category_without_a_graded_mapping(session):
    listing = make_slab(session)
    assert ebay_publish._resolve_category(
        session, session.get(CatalogItem, "slab"), session.get(TcgCard, "slab"), "EBAY_AU",
    ) == "183454"
    assert ebay_publish.publication_preflight(session, listing, settings()).category_id == "183454"


def test_raw_cards_keep_their_existing_postage_policy(session):
    assert ebay_publish._graded_publish_fields(
        session, ShopListing(id="raw"), CatalogItem(id="raw", item_type=CatalogItemType.TCG_CARD, name="r"), settings(),
    ) == (None, None)
