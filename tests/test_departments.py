from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, CollectibleProduct, ShopListing, ShopListingStatus,
)
from storefront import departments
from storefront.app import create_app


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_listing(session, name, item_type, price="10.00", title=None):
    item = CatalogItem(id=f"i-{name}", item_type=item_type, name=name)
    session.add(item)
    session.add(ShopListing(
        id=f"l-{name}", catalog_item_id=item.id, title=title or name,
        price=Decimal(price), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    return item


def test_every_department_slug_is_unique_and_resolvable():
    slugs = [d.slug for d in departments.DEPARTMENTS]
    assert len(slugs) == len(set(slugs))
    for slug in slugs:
        assert departments.find(slug) is not None


def test_no_item_type_lands_in_two_departments():
    """The split has to be a partition -- an item showing up under both
    Cards and Collectibles would make the counts lie."""
    seen = set()
    for department in departments.DEPARTMENTS:
        for item_type in department.item_types:
            assert item_type not in seen, item_type
            seen.add(item_type)


def test_find_is_forgiving_about_case_and_whitespace():
    assert departments.find(" TCG ") is departments.find("tcg")
    assert departments.find("nonsense") is None


def test_counts_separate_cards_from_collectibles(session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD)
    add_listing(session, "booster", CatalogItemType.SEALED_PRODUCT)
    add_listing(session, "hamburger", CatalogItemType.COLLECTIBLE)
    session.commit()

    totals = departments.counts(session)
    assert totals["tcg"] == 2
    assert totals["collectibles"] == 1
    assert totals["apparel"] == 0


def test_bulk_lots_count_toward_tcg_only(session):
    session.add(BulkLot(
        id="b1", set_id=None, game="pokemon", title="Base bulk", card_count=50,
        price=Decimal("20.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()
    totals = departments.counts(session)
    assert totals["tcg"] == 1
    assert totals["collectibles"] == 0


def test_empty_departments_stay_out_of_navigation(session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD)
    session.commit()
    slugs = [d.slug for d, _ in departments.in_stock(session)]
    assert slugs == ["tcg"]  # apparel has no stock and must not be advertised


def test_department_page_shows_only_its_own_stock(client, session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD, title="Charizard Holo")
    add_listing(session, "hamburger", CatalogItemType.COLLECTIBLE, title="Sonny Angel Hamburger")
    session.commit()

    cards = client.get("/shop/tcg").text
    assert "Charizard Holo" in cards
    assert "Sonny Angel Hamburger" not in cards

    collectibles = client.get("/shop/collectibles").text
    assert "Sonny Angel Hamburger" in collectibles
    assert "Charizard Holo" not in collectibles


def test_unknown_department_is_a_404(client):
    assert client.get("/shop/jewellery").status_code == 404


def test_apparel_department_exists_but_is_empty(client):
    response = client.get("/shop/apparel")
    assert response.status_code == 200
    assert "Nothing here yet" in response.text


def test_home_lists_departments_with_counts(client, session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD)
    add_listing(session, "hamburger", CatalogItemType.COLLECTIBLE)
    session.commit()

    body = client.get("/").text
    assert "href='/shop/tcg'" in body
    assert "href='/shop/collectibles'" in body
    assert "Trading cards" in body and "Collectibles" in body


def test_home_without_stock_does_not_advertise_departments(client):
    body = client.get("/").text
    assert "Nothing listed yet" in body
    assert "href='/shop/tcg'" not in body


def test_browse_can_filter_by_department(client, session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD, title="Charizard Holo")
    add_listing(session, "hamburger", CatalogItemType.COLLECTIBLE, title="Sonny Angel Hamburger")
    session.commit()

    filtered = client.get("/browse?dept=collectibles").text
    assert "Sonny Angel Hamburger" in filtered
    assert "Charizard Holo" not in filtered

    everything = client.get("/browse").text
    assert "Sonny Angel Hamburger" in everything and "Charizard Holo" in everything


def test_a_listing_with_no_catalogue_row_is_not_filed_under_a_department(client, session):
    # would otherwise default into whichever department the join fell into
    session.add(ShopListing(
        id="orphan", catalog_item_id="missing", title="Orphan listing",
        price=Decimal("5.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()
    for slug in ["tcg", "collectibles", "apparel"]:
        assert "Orphan listing" not in client.get(f"/shop/{slug}").text, slug


def test_tcg_department_points_at_bulk_master(client, session):
    add_listing(session, "charizard", CatalogItemType.TCG_CARD)
    session.add(BulkLot(
        id="b1", set_id=None, game="pokemon", title="Base bulk", card_count=50,
        price=Decimal("20.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()
    body = client.get("/shop/tcg").text
    assert "/bulk" in body and "Bulk Master" in body


def test_sonny_angel_secrets_are_their_own_catalogue_rows(session):
    """The secret/regular split is what makes the pricing meaningful --
    secrets run several times the price of a regular figure."""
    for name, secret in [("Fruit Series blind box", False), ("Fruit Series secret", True)]:
        item = CatalogItem(id=f"i-{name}", item_type=CatalogItemType.COLLECTIBLE, name=name)
        session.add(item)
        session.add(CollectibleProduct(
            catalog_item_id=item.id, brand="Sonny Angel", series="Fruit Series",
            is_secret=secret, blind_box_series="Fruit Series",
        ))
    session.commit()
    assert departments.counts(session)["collectibles"] == 0  # catalogued, not listed
