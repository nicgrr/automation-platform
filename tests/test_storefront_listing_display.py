from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CardSet, CatalogItem, CatalogItemType, CollectibleProduct,
    ShopListing, ShopListingStatus, TcgCard,
)
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


def add_card(session, name, *, game="pokemon", set_id="sv1", set_name="Scarlet & Violet",
            price="10.00", quantity=1):
    session.merge(CardSet(id=set_id, name=set_name))
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name,
                            image_url="https://images.pokemontcg.io/x/1.png"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number="4"))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=f"{name} #4",
                            condition="Near Mint", price=Decimal(price), currency="AUD",
                            quantity=quantity, status=ShopListingStatus.ACTIVE,
                            image_url="https://images.pokemontcg.io/x/1.png"))
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


# --- set name on listings ---------------------------------------------------

def test_set_name_shown_on_the_department_grid(client, session):
    add_card(session, "Charizard", set_name="Scarlet & Violet")
    body = client.get("/shop/game-pokemon").text
    # HTML-escaped: the literal & becomes &amp;
    assert "Scarlet &amp; Violet · Near Mint" in body


def test_set_name_shown_on_the_item_detail_page(client, session):
    listing_id = add_card(session, "Charizard", set_name="Scarlet & Violet")
    body = client.get(f"/item/{listing_id}").text
    assert "<li><span>Set</span><span>Scarlet &amp; Violet</span></li>" in body


def test_no_set_row_when_the_catalogue_has_no_set_on_file(client, session):
    """A card with no TcgCard.set_id shouldn't show a blank or broken Set
    row -- it just doesn't show one."""
    item_id = "i-loose"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name="Loose"))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id=None))
    listing_id = "l-loose"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title="Loose",
                            price=Decimal("5.00"), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    body = client.get(f"/item/{listing_id}").text
    assert "<span>Set</span>" not in body


def test_collectibles_show_no_set_row_they_have_no_set(client, session):
    """Set is a TCG concept -- a Sonny Angel has a series, not a set, and
    the department page already isn't the place that shows series."""
    listing_id = add_collectible(session, "Hamburger")
    body = client.get(f"/item/{listing_id}").text
    assert "<span>Set</span>" not in body


def test_the_stored_listing_title_is_never_rewritten(client, session):
    """Set name is resolved at render time, never baked into the stored
    title -- an old order must keep reading correctly even if a set gets
    renamed later."""
    from sqlalchemy import select

    listing_id = add_card(session, "Charizard", set_name="Scarlet & Violet")
    listing = session.get(ShopListing, listing_id)
    assert listing.title == "Charizard #4"  # no set name folded in


# --- home page department tiles ---------------------------------------------

def test_home_no_longer_shows_a_price_range_on_department_tiles(client, session):
    add_card(session, "Charizard", price="10.00")
    add_card(session, "Pikachu", price="80.00")
    body = client.get("/").text
    # scope to the dept-card block specifically -- the "Fresh in the
    # vault" grid legitimately shows real prices, just not the tile
    dept_block = body.split("dept-card-body")[1].split("</a>")[0]
    assert "$10.00" not in dept_block
    assert "$80.00" not in dept_block
    assert "–" not in dept_block


def test_home_department_tile_shows_a_hero_photo(client, session):
    add_card(session, "Charizard", price="80.00")
    body = client.get("/").text
    assert "class='dept-hero'" in body
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "<img" in hero_section


def test_pokemon_department_tile_shows_the_official_game_logo(client, session):
    """A category tile is a brand signpost, not a specific product -- it
    shows the real Pokémon TCG logo (a checked-in asset, see
    storefront/assets.py), not any particular card, however priced."""
    add_card(session, "Cheap", set_id="sv2", set_name="Cheap Set", price="5.00")
    add_card(session, "Charizard", set_id="sv1", set_name="Scarlet & Violet", price="200.00")
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "/assets/pokemon-tcg-logo.svg" in hero_section
    assert "alt='Charizard #4'" not in hero_section
    assert "alt='Cheap #4'" not in hero_section


def test_a_department_with_only_bulk_lots_falls_back_to_lot_art(client, session):
    """A game with no logo asset (see assets.GAME_LOGOS) genuinely falls
    back to lot art -- Pokémon always shows its logo regardless of what's
    in stock (see the logo tests above)."""
    session.merge(CardSet(id="op1", name="Romance Dawn"))
    session.add(BulkLot(set_id="op1", game="digimon", title="Romance Dawn bulk", card_count=50,
                        price=Decimal("20.00"), currency="AUD", quantity=1,
                        status=ShopListingStatus.ACTIVE,
                        image_url="https://images.pokemontcg.io/op1/logo.png"))
    session.commit()
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "images.pokemontcg.io/op1/logo.png" in hero_section


def test_item_count_no_longer_shows_on_the_tile(client, session):
    """A raw count ("50 item(s)") read as unexplained clutter -- dropped
    per direct feedback."""
    add_card(session, "Charizard")
    add_card(session, "Growlithe")
    body = client.get("/").text
    dept_block = body.split("dept-card-body")[1].split("</a>")[0]
    assert "item(s)" not in dept_block


def test_dept_hero_carries_no_authenticity_tag(client, session):
    """The tag makes an "actual card"/"stock image" claim about a specific
    product -- a category tile isn't standing in for one, so the claim
    has nothing to attach to."""
    add_card(session, "Growlithe")
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "art-tag" not in hero_section


def test_pokemon_department_tile_shows_the_logo_even_with_a_cached_mascot(client, session):
    """The logo wins over the mascot card too, for the tile specifically
    -- the mascot card is what the hero cast strip leads with instead
    (see test_hero_cast_shows_the_department_mascot)."""
    from automation_control.models import CatalogCard

    session.merge(CardSet(id="sm10", name="Unbroken Bonds"))
    session.add(CatalogCard(id="sm10-54", set_id="sm10", number="54", name="Pikachu",
                            image_url="https://images.pokemontcg.io/sm10/54.png"))
    session.commit()
    add_card(session, "SomeRareCard", price="500.00")

    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "/assets/pokemon-tcg-logo.svg" in hero_section
    assert "sm10/54.png" not in hero_section


def test_falls_back_to_the_priciest_item_for_a_game_with_no_logo_asset(client, session):
    """A game with no logo asset (see assets.GAME_LOGOS) -- its department
    tile must not crash, and falls back to the existing priciest-item
    logic instead of a logo."""
    add_card(session, "Agumon", game="digimon", set_id="op1", set_name="Romance Dawn", price="80.00")
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1].split("</div>")[0]
    assert "<img" in hero_section
    assert "images.pokemontcg.io/x/1.png" in hero_section


def test_dept_hero_uses_contain_not_cover(client, session):
    """A full card image (portrait, includes name/HP/attack text) cropped
    with object-fit:cover in a 16:9 box shows an arbitrary horizontal
    slice of text -- not the artwork. object-fit:contain shows the whole
    card instead, which is at least never nonsensical."""
    import re

    from storefront.ui import STYLE

    match = re.search(r"\.dept-hero img\{[^}]*\}", STYLE)
    assert match is not None
    assert "object-fit:contain" in match.group(0)
    assert "object-fit:cover" not in match.group(0)


# --- set filter + sort ------------------------------------------------------

def test_department_page_offers_a_set_filter_when_multiple_sets_are_stocked(client, session):
    add_card(session, "Charizard", set_id="sv1", set_name="Scarlet & Violet")
    add_card(session, "Pikachu", set_id="sv2", set_name="Paldea Evolved")
    body = client.get("/shop/game-pokemon").text
    assert "name='set'" in body
    assert "Scarlet &amp; Violet" in body
    assert "Paldea Evolved" in body


def test_department_page_set_filter_narrows_the_grid(client, session):
    add_card(session, "Charizard", set_id="sv1", set_name="Scarlet & Violet")
    add_card(session, "Pikachu", set_id="sv2", set_name="Paldea Evolved")
    body = client.get("/shop/game-pokemon?set=sv1").text
    grid = body.split("class='grid'")[1]
    assert "Charizard" in grid
    assert "Pikachu" not in grid


def test_department_page_sorts_by_price_ascending(client, session):
    add_card(session, "Cheap", price="2.00")
    add_card(session, "Pricey", price="200.00")
    body = client.get("/shop/game-pokemon?sort=price_asc").text
    grid = body.split("class='grid'")[1]
    assert grid.index("Cheap") < grid.index("Pricey")


def test_department_page_defaults_to_price_descending(client, session):
    add_card(session, "Cheap", price="2.00")
    add_card(session, "Pricey", price="200.00")
    body = client.get("/shop/game-pokemon").text
    grid = body.split("class='grid'")[1]
    assert grid.index("Pricey") < grid.index("Cheap")


def test_browse_page_has_a_sort_dropdown_and_respects_it(client, session):
    add_card(session, "Cheap", price="2.00")
    add_card(session, "Pricey", price="200.00")
    body = client.get("/browse?sort=price_asc").text
    assert "name='sort'" in body
    grid = body.split("class='grid'")[1]
    assert grid.index("Cheap") < grid.index("Pricey")


def test_grid_is_two_columns_by_default(client, session):
    """Two tiles per row on a phone -- the base rule, widened via a
    min-width media query only once there's room for more."""
    import re

    from storefront.ui import STYLE

    base_rule = re.search(r"\.grid\{[^}]*\}", STYLE)
    assert base_rule is not None
    assert "repeat(2,1fr)" in base_rule.group(0)
    assert "min-width:640px" in STYLE.split(base_rule.group(0), 1)[1][:60]


@pytest.mark.parametrize("game,logo", [
    ("one_piece", "/assets/one-piece-logo.svg"),
    ("dragon_ball_super", "/assets/dragon-ball-logo.png"),
])
def test_one_piece_and_dragon_ball_tiles_show_their_logo(client, session, game, logo):
    add_card(session, "Somebody", game=game, price="80.00")
    hero_section = client.get("/").text.split("class='dept-hero'")[1].split("</div>")[0]
    assert logo in hero_section
    assert client.get(logo).status_code == 200


def test_collectables_tile_shows_its_banner(client, session):
    from tests.test_departments import add_collectible

    add_collectible(session, "Sheep")
    body = client.get("/").text
    tile = body.split("class='dept-card' href='/shop/collectibles'")[1].split("</a>")[0]
    assert "/assets/collectables-banner.jpg" in tile
    assert client.get("/assets/collectables-banner.jpg").status_code == 200
