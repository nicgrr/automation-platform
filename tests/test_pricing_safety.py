from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import (
    CardPrice,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    ShopListing,
    ShopListingStatus,
)
from automation_control.pricing_safety import assess_listing_price


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'pricing-safety.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _listing(session: Session, *, price: str = "36.36") -> ShopListing:
    session.add(CatalogItem(id="loki", item_type=CatalogItemType.TCG_CARD, name="Loki (Alternate Art)"))
    session.add(InventoryItem(
        id="loki-stock", catalog_item_id="loki", variant=CardVariant.HOLO,
        condition="Near Mint", quantity=2,
    ))
    listing = ShopListing(
        id="loki-listing", catalog_item_id="loki", inventory_item_id="loki-stock",
        title="Loki (Alternate Art) #OP17-119", price=Decimal(price),
        currency="AUD", quantity=2, status=ShopListingStatus.ACTIVE,
    )
    session.add(listing)
    session.commit()
    return listing


def test_lowest_ask_cannot_override_a_materially_higher_recent_median(session):
    listing = _listing(session)
    session.add_all([
        CardPrice(catalog_item_id="loki", source="ebay_active_low", price=Decimal("36.36"), currency="AUD"),
        CardPrice(catalog_item_id="loki", source="ebay_active_median", price=Decimal("90.00"), currency="AUD"),
    ])
    session.commit()

    assessment = assess_listing_price(session, listing)

    assert assessment.blocked
    assert assessment.review_floor == Decimal("67.50")
    assert "proposed AUD $36.36" in assessment.message()
    assert "eBay active median is AUD $90.00" in assessment.message()


def test_price_at_the_review_floor_is_allowed(session):
    listing = _listing(session, price="67.50")
    session.add(CardPrice(
        catalog_item_id="loki", source="ebay_active_median",
        price=Decimal("90.00"), currency="AUD",
    ))
    session.commit()

    assert not assess_listing_price(session, listing).blocked


def test_old_or_low_value_evidence_does_not_create_a_false_hold(session):
    listing = _listing(session, price="5.00")
    now = datetime.now(UTC)
    session.add_all([
        CardPrice(
            catalog_item_id="loki", source="ebay_active_median", price=Decimal("100.00"),
            currency="AUD", fetched_at=now - timedelta(days=31),
        ),
        CardPrice(
            catalog_item_id="loki", source="manual", price=Decimal("10.00"),
            currency="AUD", fetched_at=now,
        ),
    ])
    session.commit()

    assessment = assess_listing_price(session, listing, now=now)

    assert not assessment.blocked
    assert assessment.review_floor is None


def test_a_different_physical_variant_is_not_used_as_evidence(session):
    listing = _listing(session, price="25.00")
    session.add(CardPrice(
        catalog_item_id="loki", variant=CardVariant.MASTERBALL_PATTERN,
        source="manual", price=Decimal("200.00"), currency="AUD",
    ))
    session.commit()

    assert not assess_listing_price(session, listing).blocked


def test_exact_manual_review_overrides_a_generic_mixed_printing_median(session):
    listing = _listing(session, price="30.25")
    session.add_all([
        CardPrice(
            catalog_item_id="loki", source="ebay_active_median",
            price=Decimal("288.88"), currency="AUD",
        ),
        CardPrice(
            catalog_item_id="loki", variant=CardVariant.HOLO, source="manual",
            price=Decimal("27.50"), currency="AUD",
        ),
    ])
    session.commit()

    assessment = assess_listing_price(session, listing)

    assert not assessment.blocked
    assert assessment.review_floor == Decimal("20.63")
    assert assessment.strongest.source == "manual"


def test_listing_without_inventory_variant_uses_the_newest_generic_observation(session):
    listing = _listing(session, price="6.99")
    listing.inventory_item_id = None
    now = datetime.now(UTC)
    session.add_all([
        CardPrice(
            catalog_item_id="loki", source="ebay_active_median",
            price=Decimal("20.00"), currency="AUD", fetched_at=now - timedelta(days=10),
        ),
        CardPrice(
            catalog_item_id="loki", source="ebay_active_median",
            price=Decimal("7.99"), currency="AUD", fetched_at=now,
        ),
    ])
    session.commit()

    assessment = assess_listing_price(session, listing, now=now)

    assert not assessment.blocked
    assert assessment.review_floor is None
