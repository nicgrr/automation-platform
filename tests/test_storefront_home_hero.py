import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    CardVariant, CatalogCard, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
    ShopListing, ShopListingStatus, TcgCard,
)
from storefront import photos
from storefront.app import create_app


def _body_only(html: str) -> str:
    """Slices off the <head>/<style> block -- CSS selector text like
    '.hero-pane-tcg{...}' matches naive substring splits on 'hero-pane-tcg'
    just as readily as the real <a class='hero-pane hero-pane-tcg'> markup
    does, and it always comes first in the document."""
    return html.split("</style>", 1)[1]


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, tmp_path, monkeypatch):
    # storefront/photos.py only serves a photo whose path resolves inside
    # scan_media_dir/captured_cards_dir -- point both at tmp_path so a
    # test fixture's own photo file counts as real, servable stock.
    monkeypatch.setattr(photos, "get_settings",
                         lambda: Settings(scan_media_dir=str(tmp_path), captured_cards_dir=str(tmp_path)))

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _stock_something(session):
    """The homepage only renders its department grid with real stock --
    unrelated to the hero, but every test here needs the page to not take
    the empty-catalogue branch."""
    session.add(CatalogItem(id="filler", item_type=CatalogItemType.TCG_CARD, name="Filler Card"))
    session.add(TcgCard(catalog_item_id="filler", game="pokemon", number="1"))
    session.add(InventoryItem(id="inv-filler", card_id=None, catalog_item_id="filler",
                               variant=CardVariant.NORMAL, quantity=1))
    session.add(ShopListing(id="listing-filler", catalog_item_id="filler", title="Filler Card", price="1.00",
                             status=ShopListingStatus.ACTIVE, quantity=1))
    session.commit()


def test_home_renders_a_split_hero(client, session):
    _stock_something(session)
    body = client.get("/").text
    assert "class='hero-split'" in body
    assert "class='hero-pane hero-pane-tcg" in body
    assert "class='hero-pane hero-pane-collect" in body


def test_hero_split_is_skipped_entirely_with_no_stock_at_all(client, session):
    """Matches the rest of the homepage's own policy (see
    test_departments.py::test_home_without_stock_does_not_advertise_departments)
    -- an empty catalogue must never link out to a department page with
    nothing on it, and that includes the hero's own two panes."""
    response = client.get("/")
    assert response.status_code == 200
    assert "class='hero-split'" not in _body_only(response.text)
    assert "href='/shop/" not in response.text


def test_original_hero_inner_block_is_unchanged(client, session):
    """Regression guard: the split hero must be *appended* after the
    existing hero-inner block, never restructured into it -- other tests
    (test_storefront_contact.py) parse hero-inner by its exact
    '</div></div>' closing sequence."""
    _stock_something(session)
    body = client.get("/").text
    hero = body.split("class='hero-inner'")[1].split("</div></div>")[0]
    assert "class='hero-brand'" in hero
    assert "hero-split" not in hero


def test_tcg_pane_uses_real_catalog_art_when_available(client, session):
    session.add(CatalogCard(id="sv4pt5-18", set_id="sv4pt5", number="18", name="Pikachu",
                             image_url="https://images.pokemontcg.io/sv4pt5/18.png"))
    session.add(CatalogItem(id="one-piece-op01-024", item_type=CatalogItemType.TCG_CARD, name="Monkey.D.Luffy",
                             image_url="https://tcgplayer-cdn.example/luffy.jpg"))
    session.add(TcgCard(catalog_item_id="one-piece-op01-024", game="one_piece", set_code="OP01", number="024"))
    _stock_something(session)

    body = client.get("/").text
    assert "https://images.pokemontcg.io/sv4pt5/18.png" in body
    assert "https://tcgplayer-cdn.example/luffy.jpg" in body
    assert "hero-pane-fallback" not in body.split("hero-pane-tcg")[1].split("</a>")[0]


def test_tcg_pane_ignores_a_local_cache_path_and_falls_back_to_catalog_card(client, session):
    """The bug this guards: CatalogItem.image_url holds a local
    scan-ingest cache path for the vast majority of Pokemon rows -- the
    real publisher URL lives on the sibling CatalogCard row instead."""
    session.add(CatalogItem(id="sv4pt5-18", item_type=CatalogItemType.TCG_CARD, name="Pikachu",
                             image_url="scan_ingest_data/cache/sv4pt5/18.png"))
    session.add(CatalogCard(id="sv4pt5-18", set_id="sv4pt5", number="18", name="Pikachu",
                             image_url="https://images.pokemontcg.io/sv4pt5/18.png"))
    _stock_something(session)

    body = client.get("/").text
    assert "https://images.pokemontcg.io/sv4pt5/18.png" in body
    assert "scan_ingest_data/cache" not in body


def test_tcg_pane_falls_back_gracefully_with_no_catalog_art_at_all(client, session):
    _stock_something(session)
    body = _body_only(client.get("/").text)
    tcg_pane = body.split("hero-pane-tcg")[1].split("</a>")[0]
    assert "hero-pane-fallback" in tcg_pane
    assert "<img" not in tcg_pane


def test_collectible_pane_shows_the_priciest_in_stock_item_with_a_real_photo(client, session, tmp_path):
    photo = tmp_path / "figure.jpg"
    photo.write_bytes(b"fake-jpeg-bytes")

    session.add(CatalogItem(id="cheap-figure", item_type=CatalogItemType.COLLECTIBLE, name="Cheap Figure"))
    session.add(CollectibleProduct(catalog_item_id="cheap-figure", brand="Sonny Angel"))
    session.add(InventoryItem(id="inv-cheap", card_id=None, catalog_item_id="cheap-figure",
                               variant=CardVariant.NORMAL, quantity=1, scan_image_path=str(photo)))
    session.add(ShopListing(id="listing-cheap", catalog_item_id="cheap-figure", inventory_item_id="inv-cheap",
                             title="Cheap Figure", price="5.00", status=ShopListingStatus.ACTIVE, quantity=1))

    session.add(CatalogItem(id="pricey-figure", item_type=CatalogItemType.COLLECTIBLE, name="Pricey Figure"))
    session.add(CollectibleProduct(catalog_item_id="pricey-figure", brand="Sonny Angel"))
    session.add(InventoryItem(id="inv-pricey", card_id=None, catalog_item_id="pricey-figure",
                               variant=CardVariant.NORMAL, quantity=1, scan_image_path=str(photo)))
    session.add(ShopListing(id="listing-pricey", catalog_item_id="pricey-figure", inventory_item_id="inv-pricey",
                             title="Pricey Figure", price="99.00", status=ShopListingStatus.ACTIVE, quantity=1))
    _stock_something(session)

    body = _body_only(client.get("/").text)
    collect_pane = body.split("hero-pane-collect")[1].split("</a>")[0]
    assert "/photo/listing-pricey" in collect_pane
    assert "/photo/listing-cheap" not in collect_pane


def test_collectible_pane_falls_back_gracefully_with_no_photographed_stock(client, session):
    session.add(CatalogItem(id="no-photo-figure", item_type=CatalogItemType.COLLECTIBLE, name="No Photo Figure"))
    session.add(CollectibleProduct(catalog_item_id="no-photo-figure", brand="Sonny Angel"))
    session.add(InventoryItem(id="inv-nophoto", card_id=None, catalog_item_id="no-photo-figure",
                               variant=CardVariant.NORMAL, quantity=1))
    session.add(ShopListing(id="listing-nophoto", catalog_item_id="no-photo-figure", inventory_item_id="inv-nophoto",
                             title="No Photo Figure", price="10.00", status=ShopListingStatus.ACTIVE, quantity=1))
    _stock_something(session)

    body = _body_only(client.get("/").text)
    collect_pane = body.split("hero-pane-collect")[1].split("</a>")[0]
    assert "hero-pane-fallback" in collect_pane
    assert "<img" not in collect_pane


def test_hero_panes_link_to_the_right_sections(client, session):
    _stock_something(session)
    body = _body_only(client.get("/").text)
    assert "class='hero-pane hero-pane-tcg" in body
    assert "href='/browse'" in body.split("hero-pane-tcg")[1].split(">")[0]
    assert "href='/shop/collectibles'" in body.split("hero-pane-collect")[1].split(">")[0]


def test_gsap_animates_the_hero_panes_too(client, session):
    _stock_something(session)
    body = client.get("/").text
    assert "gsap.from(panes" in body


def test_home_renders_an_ambient_backdrop_collage(client, session):
    session.add(CatalogCard(id="swsh4-25", set_id="swsh4", number="25", name="Charizard",
                             image_url="https://images.pokemontcg.io/swsh4/25.png"))
    _stock_something(session)

    body = _body_only(client.get("/").text)
    assert "class='home-backdrop'" in body
    assert "https://images.pokemontcg.io/swsh4/25.png" in body


def test_backdrop_is_skipped_entirely_with_no_stock_at_all(client, session):
    response = client.get("/")
    assert response.status_code == 200
    assert "class='home-backdrop'" not in _body_only(response.text)


def test_backdrop_only_appears_on_the_homepage(client, session):
    """A fixed, page-wide element belongs only where it was actually
    designed in -- other storefront pages must not inherit it."""
    _stock_something(session)
    other_page = _body_only(client.get("/browse").text)
    assert "class='home-backdrop'" not in other_page
