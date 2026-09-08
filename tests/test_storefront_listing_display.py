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
    hero_section = body.split("class='dept-hero'")[1][:600]
    assert "<img" in hero_section
    assert "images.pokemontcg.io" in hero_section


def test_hero_photo_is_the_departments_priciest_item(client, session):
    add_card(session, "Cheap", set_id="sv2", set_name="Cheap Set", price="5.00")
    add_card(session, "Charizard", set_id="sv1", set_name="Scarlet & Violet", price="200.00")
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1][:800]
    assert "alt='Charizard #4'" in hero_section
    assert "alt='Cheap #4'" not in hero_section


def test_a_department_with_only_bulk_lots_falls_back_to_lot_art(client, session):
    session.merge(CardSet(id="sv1", name="Scarlet & Violet", logo_url="https://images.pokemontcg.io/sv1/logo.png"))
    session.add(BulkLot(set_id="sv1", game="pokemon", title="Scarlet & Violet bulk", card_count=50,
                        price=Decimal("20.00"), currency="AUD", quantity=1,
                        status=ShopListingStatus.ACTIVE,
                        image_url="https://images.pokemontcg.io/sv1/logo.png"))
    session.commit()
    body = client.get("/").text
    assert "images.pokemontcg.io/sv1/logo.png" in body


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


def test_pokemon_department_leads_with_pikachu_not_the_priciest_item(client, session):
    """A shopper recognises Pikachu; they don't recognise whatever
    happens to be the priciest card in stock today."""
    from automation_control.models import CatalogCard

    session.merge(CardSet(id="sm10", name="Unbroken Bonds"))
    session.add(CatalogCard(id="sm10-54", set_id="sm10", number="54", name="Pikachu",
                            image_url="https://images.pokemontcg.io/sm10/54.png"))
    session.commit()
    add_card(session, "SomeRareCard", price="500.00")  # far pricier, must lose to the mascot

    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1][:400]
    assert "images.pokemontcg.io/sm10/54.png" in hero_section
    assert "alt='Pikachu'" in hero_section


def test_falls_back_to_the_priciest_item_when_no_mascot_card_is_cached(client, session):
    """No cached Pikachu card at all -- must not crash, and must fall
    back to the existing priciest-item logic."""
    add_card(session, "Charizard", price="80.00")
    body = client.get("/").text
    hero_section = body.split("class='dept-hero'")[1][:400]
    assert "<img" in hero_section
    assert "images.pokemontcg.io/x/1.png" in hero_section
