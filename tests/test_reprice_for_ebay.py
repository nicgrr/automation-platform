from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from automation_control.database import Base
from automation_control.models import (
    CardPrice, CardVariant, CatalogItem, CatalogItemType, ShopCostConfig, ShopListing, ShopListingStatus,
)
from scripts.reprice_for_ebay import main, target_price


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(Session, *, price=Decimal("5.00"), market=Decimal("2.00"), item_type=CatalogItemType.TCG_CARD):
    with Session() as session:
        session.add(ShopCostConfig(label="Default", is_default=True, postage_cost_au=Decimal("0.50"),
                                   postage_cost_intl=Decimal("15.00"), packaging_cost=Decimal("0.20"),
                                   payment_pct=Decimal("1.75"), payment_fixed=Decimal("0.30"),
                                   target_margin_pct=Decimal("0")))
        session.add(CatalogItem(id="item-1", item_type=item_type, name="Test Card"))
        session.add(CardPrice(card_id="item-1", variant=CardVariant.NORMAL, source="ebay_active_low",
                              price=market, currency="AUD"))
        session.add(ShopListing(id="listing-1", catalog_item_id="item-1", title="Test Card",
                                price=price, currency="AUD", status=ShopListingStatus.ACTIVE))
        session.commit()


def test_target_price_grosses_up_for_the_ebay_fee_on_top_of_the_markup():
    # $2 market, 20% markup -> $2.40, grossed up for a 13% fee -> $2.40 / 0.87
    assert target_price(Decimal("2.00"), Decimal("20"), Decimal("13")) == Decimal("2.76")


def test_target_price_rejects_a_fee_of_100_percent_or_more():
    with pytest.raises(ValueError):
        target_price(Decimal("2.00"), Decimal("20"), Decimal("100"))


def test_a_listing_priced_above_the_new_target_is_repriced_down(monkeypatch, Session):
    _seed(Session, price=Decimal("5.00"), market=Decimal("2.00"))
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    result = main([])
    assert result == 0

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.price == Decimal("2.76")


def test_a_listing_priced_below_the_new_target_is_repriced_up(monkeypatch, Session):
    _seed(Session, price=Decimal("1.00"), market=Decimal("2.00"))
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main([])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.price == Decimal("2.76")


def test_a_listing_already_at_target_is_left_alone(monkeypatch, Session, capsys):
    _seed(Session, price=Decimal("2.76"), market=Decimal("2.00"))
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main([])

    out = capsys.readouterr().out
    assert "1 already at target" in out


def test_dry_run_writes_nothing(monkeypatch, Session):
    _seed(Session, price=Decimal("5.00"), market=Decimal("2.00"))
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main(["--dry-run"])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.price == Decimal("5.00")


def test_a_listing_with_no_market_signal_is_skipped_not_priced_at_zero(monkeypatch, Session):
    with Session() as session:
        session.add(ShopCostConfig(label="Default", is_default=True, postage_cost_au=Decimal("4.00"),
                                   postage_cost_intl=Decimal("15.00"), packaging_cost=Decimal("0.20"),
                                   payment_pct=Decimal("1.75"), payment_fixed=Decimal("0.30"),
                                   target_margin_pct=Decimal("0")))
        session.add(CatalogItem(id="item-1", item_type=CatalogItemType.TCG_CARD, name="Test Card"))
        session.add(ShopListing(id="listing-1", catalog_item_id="item-1", title="Test Card",
                                price=Decimal("5.00"), currency="AUD", status=ShopListingStatus.ACTIVE))
        session.commit()
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main([])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.price == Decimal("5.00")


def test_the_packaging_floor_still_applies_below_the_market_based_target(monkeypatch, Session):
    """A very cheap market signal must never produce a price under real
    packaging material cost, even though 20%-over-market alone would
    allow it. This is deliberately NOT the website's full break-even
    floor (postage/payment fees) -- see the module docstring for why
    that floor is wrong for an eBay listing, where postage is buyer-paid
    separately and eBay's own cut is already grossed up in the formula."""
    _seed(Session, price=Decimal("5.00"), market=Decimal("0.10"))
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main([])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        # 0.10 * 1.20 / 0.87 = 0.14, under the $0.20 packaging-cost floor
        assert listing.price == Decimal("0.20")


def test_a_non_card_item_type_is_never_touched(monkeypatch, Session):
    _seed(Session, price=Decimal("5.00"), market=Decimal("2.00"), item_type=CatalogItemType.COLLECTIBLE)
    monkeypatch.setattr("scripts.reprice_for_ebay.SessionLocal", Session)

    main([])

    with Session() as session:
        listing = session.get(ShopListing, "listing-1")
        assert listing.price == Decimal("5.00")
