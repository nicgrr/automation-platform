import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CardSet, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus, TcgCard,
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


def add_card(session, name, *, game="pokemon", set_id="sv1", set_name="Scarlet & Violet", price="10.00"):
    session.merge(CardSet(id=set_id, name=set_name))
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name,
                            image_url="https://images.pokemontcg.io/x/1.png"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number="4"))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=f"{name} #4",
                            condition="Near Mint", price=Decimal(price), currency="AUD",
                            quantity=1, status=ShopListingStatus.ACTIVE,
                            image_url="https://images.pokemontcg.io/x/1.png"))
    session.commit()
    return listing_id


def add_bulk_lot(session, *, game="pokemon", title="Set One bulk", price="20.00"):
    lot = BulkLot(set_id="sv1", game=game, title=title, card_count=50,
                 price=Decimal(price), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
                 image_url="https://images.pokemontcg.io/sv1/logo.png")
    session.add(lot)
    session.commit()
    return lot.id


def _jsonld_blocks(body: str) -> list[dict]:
    blocks = []
    marker = "<script type='application/ld+json'>"
    pos = 0
    while True:
        start = body.find(marker, pos)
        if start == -1:
            break
        start += len(marker)
        end = body.find("</script>", start)
        blocks.append(json.loads(body[start:end]))
        pos = end
    return blocks


# --- favicon / share image / llms.txt ---------------------------------------

def test_favicon_ico_is_a_real_image(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 100


def test_favicon_svg_is_served(client):
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers["content-type"]
    assert "<svg" in response.text


def test_apple_touch_icon_is_a_real_image(client):
    response = client.get("/apple-touch-icon.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_social_share_image_is_a_real_image(client):
    response = client.get("/social-share.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 1000


def test_home_page_links_all_three_icon_variants(client):
    body = client.get("/").text
    assert "href='/favicon.svg'" in body
    assert "href='/favicon.ico'" in body
    assert "href='/apple-touch-icon.png'" in body


def test_llms_txt_describes_the_shop_and_links_key_pages(client):
    body = client.get("/llms.txt").text
    assert "NL Vault" in body
    assert "/browse" in body
    assert "/bulk" in body
    assert "/contact" in body


# --- canonical + robots ------------------------------------------------------

def test_home_page_has_a_canonical_tag(client):
    body = client.get("/").text
    assert "<link rel='canonical' href='http://testserver/'>" in body


def test_department_page_has_a_canonical_tag_matching_its_own_url(client, session):
    add_card(session, "Charizard")
    body = client.get("/shop/game-pokemon").text
    assert "<link rel='canonical' href='http://testserver/shop/game-pokemon'>" in body


def test_cart_page_is_noindex(client):
    body = client.get("/cart").text
    assert "<meta name='robots' content='noindex,follow'>" in body


def test_checkout_form_is_noindex(client, session):
    listing_id = add_card(session, "Charizard")
    client.post("/cart/add", data={"listing_id": listing_id})
    body = client.get("/checkout").text
    assert "<meta name='robots' content='noindex,follow'>" in body


def test_privacy_and_terms_are_noindex_until_facts_are_filled_in(client):
    assert "<meta name='robots' content='noindex,follow'>" in client.get("/privacy").text
    assert "<meta name='robots' content='noindex,follow'>" in client.get("/terms").text


def test_a_normal_content_page_is_not_noindexed(client, session):
    add_card(session, "Charizard")
    body = client.get("/shop/game-pokemon").text
    assert "name='robots'" not in body


def test_404_page_is_noindex(client):
    body = client.get("/totally/bogus").text
    assert "<meta name='robots' content='noindex,follow'>" in body


# --- breadcrumbs --------------------------------------------------------

def test_department_page_shows_a_breadcrumb_trail(client, session):
    add_card(session, "Charizard")
    body = client.get("/shop/game-pokemon").text
    crumb = body.split("class='breadcrumbs'")[1].split("</nav>")[0]
    assert "href='/'" in crumb
    assert "Pokémon" in crumb


def test_item_page_breadcrumb_includes_its_department(client, session):
    listing_id = add_card(session, "Charizard")
    body = client.get(f"/item/{listing_id}").text
    crumb = body.split("class='breadcrumbs'")[1].split("</nav>")[0]
    assert "href='/shop/game-pokemon'" in crumb
    assert "Charizard #4" in crumb


def test_breadcrumb_last_item_is_never_a_link(client, session):
    add_card(session, "Charizard")
    body = client.get("/shop/game-pokemon").text
    crumb = body.split("class='breadcrumbs'")[1].split("</nav>")[0]
    # "Pokémon" is the current page -- it must render as text, not <a>
    assert "<a href='/shop/game-pokemon'>Pokémon</a>" not in crumb


def test_home_page_has_no_breadcrumb(client):
    assert "class='breadcrumbs'" not in client.get("/").text


# --- structured data ----------------------------------------------------

def test_every_page_carries_an_organization_json_ld_block(client):
    blocks = _jsonld_blocks(client.get("/").text)
    org = next(b for b in blocks if b.get("@type") == "OnlineStore")
    assert org["name"] == "NL Vault"
    assert org["url"] == "http://testserver"


def test_breadcrumb_json_ld_matches_the_visible_trail(client, session):
    add_card(session, "Charizard")
    blocks = _jsonld_blocks(client.get("/shop/game-pokemon").text)
    crumbs = next(b for b in blocks if b.get("@type") == "BreadcrumbList")
    names = [item["name"] for item in crumbs["itemListElement"]]
    assert names == ["Home", "Pokémon"]
    assert crumbs["itemListElement"][0]["item"] == "http://testserver/"


def test_item_page_carries_product_json_ld(client, session):
    listing_id = add_card(session, "Charizard", price="42.50")
    blocks = _jsonld_blocks(client.get(f"/item/{listing_id}").text)
    product = next(b for b in blocks if b.get("@type") == "Product")
    assert product["name"] == "Charizard #4"
    assert product["offers"]["price"] == "42.50"
    assert product["offers"]["priceCurrency"] == "AUD"
    assert product["offers"]["availability"] == "https://schema.org/InStock"


def test_bulk_lot_page_carries_product_json_ld(client, session):
    lot_id = add_bulk_lot(session, price="15.00")
    blocks = _jsonld_blocks(client.get(f"/bulk/{lot_id}").text)
    product = next(b for b in blocks if b.get("@type") == "Product")
    assert product["offers"]["price"] == "15.00"


def test_social_link_appears_in_organization_same_as(client, session):
    from automation_control.models import SocialLink

    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=True))
    session.commit()
    blocks = _jsonld_blocks(client.get("/").text)
    org = next(b for b in blocks if b.get("@type") == "OnlineStore")
    assert org["sameAs"] == ["https://instagram.com/nlvault"]


# --- og:image -------------------------------------------------------------

def test_home_page_falls_back_to_the_default_share_image(client):
    body = client.get("/").text
    assert "og:image' content='http://testserver/social-share.png'" in body


def test_item_page_uses_the_listings_own_image_as_the_share_image(client, session):
    listing_id = add_card(session, "Charizard")
    body = client.get(f"/item/{listing_id}").text
    assert "og:image' content='https://images.pokemontcg.io/x/1.png'" in body


# --- sitemap --------------------------------------------------------------

def test_sitemap_includes_a_stocked_department_page(client, session):
    add_card(session, "Charizard")
    body = client.get("/sitemap.xml").text
    assert "/shop/game-pokemon</loc>" in body


def test_sitemap_includes_contact_but_not_the_draft_legal_pages(client):
    body = client.get("/sitemap.xml").text
    assert "/contact</loc>" in body
    assert "/privacy</loc>" not in body
    assert "/terms</loc>" not in body


# --- alt text ---------------------------------------------------------------

def test_bulk_lot_card_has_meaningful_alt_text_not_blank(client, session):
    add_bulk_lot(session, title="Scarlet & Violet bulk")
    body = client.get("/bulk").text
    assert "alt='Scarlet &amp; Violet bulk'" in body
    assert "alt=''" not in body
