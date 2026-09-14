from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardSet, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus, TcgCard
from scripts.export_cards_by_set_review import build_rows


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _card(session, sku, title, number, set_id, price="2.00", rarity=None):
    item = CatalogItem(id=f"item-{sku}", item_type=CatalogItemType.TCG_CARD, name=title)
    session.add(item)
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id=set_id, number=number, rarity=rarity))
    session.add(ShopListing(id=sku, catalog_item_id=item.id, title=title, price=Decimal(price),
                            status=ShopListingStatus.ACTIVE))


def test_rows_are_grouped_and_sorted_by_set_then_numeric_number(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(CardSet(id="sv4", name="Paradox Rift"))
    _card(session, "sku-b", "Zoroark #100", "100", "sv3")
    _card(session, "sku-a", "Abra #9", "9", "sv3")
    _card(session, "sku-c", "Bulbasaur #1", "1", "sv4")
    session.commit()

    rows = build_rows(session, ShopListingStatus.ACTIVE)
    assert [(r["set_name"], r["number"]) for r in rows] == [
        ("Obsidian Flames", "9"), ("Obsidian Flames", "100"), ("Paradox Rift", "1"),
    ]


def test_a_row_carries_the_fields_needed_to_spot_a_misfiled_card(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames", printed_total=197))
    _card(session, "sku-a", "Charizard ex #125", "125", "sv3", price="45.00", rarity="Double Rare")
    session.commit()

    rows = build_rows(session, ShopListingStatus.ACTIVE)
    assert rows[0] == {
        "set_name": "Obsidian Flames", "set_total": "197", "number": "125", "card_name": "Charizard ex",
        "title": "Charizard ex #125", "price": "45.00", "condition": "", "rarity": "Double Rare", "sku": "sku-a",
    }


def test_a_non_tcg_listing_is_skipped_not_a_blank_row(session):
    item = CatalogItem(id="item-figure", item_type=CatalogItemType.COLLECTIBLE, name="Sonny Angel")
    session.add(item)
    session.add(ShopListing(id="sku-figure", catalog_item_id=item.id, title="Sonny Angel",
                            price=Decimal("10.00"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows = build_rows(session, ShopListingStatus.ACTIVE)
    assert rows == []


def test_only_the_requested_status_is_included(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    _card(session, "sku-a", "Abra #9", "9", "sv3")
    session.commit()
    session.get(ShopListing, "sku-a").status = ShopListingStatus.DRAFT
    session.commit()

    rows = build_rows(session, ShopListingStatus.ACTIVE)
    assert rows == []
