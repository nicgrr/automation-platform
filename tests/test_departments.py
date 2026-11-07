from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, CollectibleProduct, SealedProduct,
    ShopListing, ShopListingStatus, TcgCard,
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


def add_card(session, name, *, game="pokemon", set_id="s1", set_name="Set One", price="10.00", quantity=1):
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number="1"))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=name,
                            price=Decimal(price), currency="AUD", quantity=quantity,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    return listing_id


def add_sealed(session, name, *, game="pokemon", price="45.00"):
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.SEALED_PRODUCT, name=name))
    session.add(SealedProduct(catalog_item_id=item_id, game=game))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=name,
                            price=Decimal(price), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    return listing_id


def add_collectible(session, name, *, series="Fruit Series", price="20.00"):
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(catalog_item_id=item_id, brand="Sonny Angel", series=series))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=name,
                            price=Decimal(price), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    return listing_id


def add_bulk_lot(session, *, game="pokemon", title="Set One bulk", price="20.00"):
    lot = BulkLot(set_id="s1", game=game, title=title, card_count=50,
                 price=Decimal(price), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.commit()
    return lot.id


# --- dynamic per-game departments -------------------------------------------

def test_a_game_with_no_stock_has_no_department(session):
    assert departments.find(session, "game-pokemon") is None


def test_a_game_with_stock_gets_its_own_department(session):
    add_card(session, "Charizard", game="pokemon")
    department = departments.find(session, "game-pokemon")
    assert department is not None
    assert department.name == "Pokémon"
    assert department.game == "pokemon"


def test_two_games_get_two_separate_departments(session):
    add_card(session, "Charizard", game="pokemon")
    add_card(session, "Luffy", game="one_piece")
    slugs = {d.slug for d, _ in departments.in_stock(session)}
    assert {"game-pokemon", "game-one_piece"} <= slugs


def test_an_unlabelled_game_code_still_gets_a_readable_name(session):
    add_card(session, "Something", game="digimon")
    department = departments.find(session, "game-digimon")
    assert department.name == "Digimon"  # title-cased fallback, not the raw code


def test_find_is_forgiving_about_case_and_whitespace(session):
    add_card(session, "Charizard", game="pokemon")
    assert departments.find(session, " GAME-POKEMON ") is not None


def test_find_returns_none_for_nonsense(session):
    assert departments.find(session, "nonsense") is None


def test_collectibles_and_apparel_are_not_game_departments(session):
    add_collectible(session, "Hamburger")
    department = departments.find(session, "collectibles")
    assert department.game is None


def test_no_item_type_lands_in_two_departments(session):
    """The split has to be a partition -- a TCG card showing up under two
    game departments (or under Collectibles) would make counts lie."""
    add_card(session, "Charizard", game="pokemon")
    add_card(session, "Luffy", game="one_piece")
    add_collectible(session, "Hamburger")
    add_sealed(session, "Booster Box", game="pokemon")
    counts = departments.counts(session)
    assert sum(counts.values()) == 4
    assert counts["sealed"] == 1 and counts["game-pokemon"] == 1


def test_bulk_lots_count_toward_their_own_games_department_only(session):
    add_card(session, "Charizard", game="pokemon")
    add_bulk_lot(session, game="pokemon")
    add_bulk_lot(session, game="one_piece", title="One Piece bulk")
    counts = departments.counts(session)
    # the one_piece bulk lot has no singles, but still earns the game its
    # own department entirely on the strength of the lot
    assert counts["game-pokemon"] == 2  # 1 single + 1 lot
    assert counts["game-one_piece"] == 1  # the lot alone


def test_empty_departments_stay_out_of_navigation(session):
    add_card(session, "Charizard", game="pokemon")
    slugs = [d.slug for d, _ in departments.in_stock(session)]
    assert "apparel" not in slugs
    assert "collectibles" not in slugs


def test_department_page_shows_only_its_own_game(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_card(session, "Luffy", game="one_piece")

    pokemon_body = client.get("/shop/game-pokemon").text
    assert "Charizard" in pokemon_body
    assert "Luffy" not in pokemon_body

    one_piece_body = client.get("/shop/game-one_piece").text
    assert "Luffy" in one_piece_body
    assert "Charizard" not in one_piece_body


def test_unknown_department_is_a_404(client):
    assert client.get("/shop/jewellery").status_code == 404


def test_home_lists_one_tile_per_game_plus_collectibles(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_card(session, "Luffy", game="one_piece")
    add_collectible(session, "Hamburger")

    body = client.get("/").text
    assert "href='/shop/game-pokemon'" in body
    assert "href='/shop/game-one_piece'" in body
    assert "href='/shop/collectibles'" in body
    assert "Pokémon" in body and "One Piece" in body


def test_home_without_stock_does_not_advertise_departments(client):
    body = client.get("/").text
    assert "Nothing listed yet" in body
    # The stylesheet targets .dept-card[href='/shop/...'], so look for an
    # actual tile link rather than the bare href text.
    assert "<a class='dept-card'" not in body


def test_browse_can_filter_by_a_game_department(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_card(session, "Luffy", game="one_piece")

    filtered = client.get("/browse?dept=game-pokemon").text
    assert "Charizard" in filtered
    assert "Luffy" not in filtered


def test_a_listing_with_no_catalogue_row_is_not_filed_under_a_department(client, session):
    add_card(session, "Charizard", game="pokemon")  # gives game-pokemon a real department to check
    session.add(ShopListing(
        id="orphan", catalog_item_id="missing", title="Orphan listing",
        price=Decimal("5.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()
    body = client.get("/shop/game-pokemon").text
    assert "Orphan listing" not in body


def test_tcg_department_points_at_bulk_master_for_its_own_game(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_bulk_lot(session, game="pokemon")
    add_bulk_lot(session, game="one_piece", title="One Piece bulk")

    body = client.get("/shop/game-pokemon").text
    assert "/bulk?game=pokemon" in body
    assert "1 bulk lot(s)" in body  # not the one_piece lot


# --- segments: singles / premium / sealed -----------------------------------

def test_premium_segment_only_shows_expensive_singles(client, session):
    add_card(session, "Cheap", game="pokemon", price="2.00")
    add_card(session, "Dear", game="pokemon", price="80.00")

    body = client.get("/shop/game-pokemon?seg=premium").text
    assert "Dear" in body
    assert "Cheap" not in body


def test_premium_pill_is_hidden_when_nothing_qualifies(client, session):
    add_card(session, "Cheap", game="pokemon", price="2.00")
    body = client.get("/shop/game-pokemon").text
    assert "Premium" not in body


def test_premium_pill_appears_once_something_qualifies(client, session):
    add_card(session, "Dear", game="pokemon", price="80.00")
    body = client.get("/shop/game-pokemon").text
    assert "Premium" in body


def test_sealed_segment_shows_only_sealed_product(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_sealed(session, "Booster Box", game="pokemon")

    body = client.get("/shop/game-pokemon?seg=sealed").text
    assert "Booster Box" in body
    assert "Charizard" not in body


def test_sealed_pill_hidden_with_no_sealed_stock(client, session):
    add_card(session, "Charizard", game="pokemon")
    assert "Sealed product" not in client.get("/shop/game-pokemon").text


def test_singles_segment_excludes_sealed_product(client, session):
    add_card(session, "Charizard", game="pokemon")
    add_sealed(session, "Booster Box", game="pokemon")

    body = client.get("/shop/game-pokemon?seg=singles").text
    assert "Charizard" in body
    assert "Booster Box" not in body


def test_an_unknown_segment_falls_back_to_all(client, session):
    add_card(session, "Charizard", game="pokemon")
    body = client.get("/shop/game-pokemon?seg=nonsense").text
    assert "Charizard" in body


def test_segment_pills_do_not_appear_on_non_game_departments(client, session):
    # assert on the rendered element, not the string -- the stylesheet
    # always defines .seg-tab, whether or not the page renders one
    add_collectible(session, "Hamburger")
    body = client.get("/shop/collectibles").text
    assert "<a class='seg-tab" not in body
    assert "class='seg-tabs'" not in body


# --- Bulk Master game filter --------------------------------------------------

def test_bulk_master_can_be_filtered_by_game(client, session):
    add_bulk_lot(session, game="pokemon", title="Pokemon bulk")
    add_bulk_lot(session, game="one_piece", title="One Piece bulk")

    body = client.get("/bulk?game=pokemon").text
    assert "Pokemon bulk" in body
    assert "One Piece bulk" not in body


def test_bulk_master_heading_reflects_the_game_filter(client, session):
    add_bulk_lot(session, game="pokemon")
    body = client.get("/bulk?game=pokemon").text
    assert "Bulk Master — Pokémon" in body


def test_bulk_master_shows_everything_with_no_game_filter(client, session):
    add_bulk_lot(session, game="pokemon", title="Pokemon bulk")
    add_bulk_lot(session, game="one_piece", title="One Piece bulk")
    body = client.get("/bulk").text
    assert "Pokemon bulk" in body and "One Piece bulk" in body
