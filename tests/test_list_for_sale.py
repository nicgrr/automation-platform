from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import (
    CardPrice, CatalogItem, CatalogItemType, InventoryItem, ShippingRate, ShippingZone,
    ShopCostConfig, ShopListing, ShopListingStatus,
)
from scripts.list_for_sale import main


@pytest.fixture
def session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(ShippingRate(zone=ShippingZone.AU, label="Standard", price=Decimal("9.95"),
                        free_over=Decimal("150.00"), is_default=True))
    db.add(ShopCostConfig(
        label="Test", postage_cost_au=Decimal("9.95"), postage_cost_intl=Decimal("29.95"),
        packaging_cost=Decimal("0.50"), payment_pct=Decimal("1.75"), payment_fixed=Decimal("0.30"),
        target_margin_pct=Decimal("25.00"), is_default=True,
    ))
    db.commit()
    monkeypatch.setattr("scripts.list_for_sale.SessionLocal", lambda: db)
    yield db
    db.close()


def add_inventory(session, name, *, price="1.00", source="ebay_active_low", quantity=1):
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(InventoryItem(id=f"inv-{name}", catalog_item_id=item_id, quantity=quantity))
    session.add(CardPrice(card_id=item_id, variant=None, source=source, price=Decimal(price), currency="AUD"))
    session.commit()
    return item_id


# --- the profit floor -------------------------------------------------------

def test_a_cheap_item_is_raised_to_the_profit_floor(session):
    add_inventory(session, "Common", price="1.00")
    main(["--min-price", "0", "--activate"])

    listing = session.scalars(select(ShopListing)).one()
    # this fixture's costs -> break-even $1.00, +25% margin -> floor $1.24
    assert listing.price == Decimal("1.24")
    assert listing.price > Decimal("1.00")  # priced above break-even, not at it


def test_an_expensive_item_is_not_touched_by_the_floor(session):
    add_inventory(session, "Rare", price="80.00")
    main(["--min-price", "0", "--activate"])

    listing = session.scalars(select(ShopListing)).one()
    assert listing.price == Decimal("80.00")


def test_the_floor_applies_after_the_margin_flag_not_instead_of_it(session):
    add_inventory(session, "Rare", price="80.00")
    main(["--min-price", "0", "--margin", "10", "--activate"])

    listing = session.scalars(select(ShopListing)).one()
    assert listing.price == Decimal("88.00")  # +10%, well above the floor either way


def test_a_very_cheap_item_is_capped_at_2x_market_instead_of_the_flat_floor(session):
    """The profit floor is a flat dollar amount from shop costs, not scaled
    to the item's own value -- a genuinely tiny market value could
    otherwise be raised many multiples over it (confirmed live 2026-09-11:
    bulk commons worth ~$1 listed at $3.99-$14.99 under an earlier, higher
    cost config). Capped at 2x market instead, even though that leaves
    this specific listing below the shop's own computed break-even."""
    add_inventory(session, "Common", price="0.50")
    main(["--min-price", "0", "--activate"])

    listing = session.scalars(select(ShopListing)).one()
    assert listing.price == Decimal("1.00")  # 2x market, not the $1.24 floor


def test_the_cap_does_not_touch_an_item_the_floor_already_clears(session):
    add_inventory(session, "Common", price="1.00")
    main(["--min-price", "0", "--activate"])

    listing = session.scalars(select(ShopListing)).one()
    assert listing.price == Decimal("1.24")  # the pre-existing floor case, unaffected by the cap


def test_missing_cost_config_still_produces_a_safe_floor(tmp_path, monkeypatch):
    """default_costs() has to fall back cleanly -- pricing must never
    silently fail because the config row is missing."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't2.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = Session(engine)
    monkeypatch.setattr("scripts.list_for_sale.SessionLocal", lambda: db)

    add_inventory(db, "Common", price="0.50")
    main(["--min-price", "0", "--activate"])

    listing = db.scalars(select(ShopListing)).one()
    assert listing.price > Decimal("0.50")
    db.close()


# --- existing behaviour, unchanged --------------------------------------------

def test_an_unpriced_item_is_skipped_not_guessed(session, capsys):
    item_id = "i-nopriced"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name="Mystery"))
    session.add(InventoryItem(id="inv-nopriced", catalog_item_id=item_id, quantity=1))
    session.commit()

    main(["--min-price", "0", "--activate"])
    assert session.scalars(select(ShopListing)).all() == []


def test_already_listed_items_are_not_duplicated(session):
    item_id = add_inventory(session, "Common", price="20.00")
    session.add(ShopListing(catalog_item_id=item_id, title="Already listed",
                            price=Decimal("20.00"), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()

    main(["--min-price", "0", "--activate"])
    assert session.scalars(select(ShopListing)).all()[0].title == "Already listed"


def test_dry_run_writes_nothing(session):
    add_inventory(session, "Common", price="1.00")
    main(["--min-price", "0", "--dry-run"])
    assert session.scalars(select(ShopListing)).all() == []


def test_draft_by_default_active_only_when_requested(session):
    add_inventory(session, "Common", price="20.00")
    main(["--min-price", "0"])
    assert session.scalars(select(ShopListing)).one().status == ShopListingStatus.DRAFT
