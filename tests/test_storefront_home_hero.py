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
    session.add(CatalogItem(id="filler", item_type=CatalogItemType.TCG_CARD, name="Filler Card"))
    session.add(TcgCard(catalog_item_id="filler", game="pokemon", number="1"))
    session.add(InventoryItem(id="inv-filler", card_id=None, catalog_item_id="filler",
                               variant=CardVariant.NORMAL, quantity=1))
    session.add(ShopListing(id="listing-filler", catalog_item_id="filler", title="Filler Card", price="1.00",
                             status=ShopListingStatus.ACTIVE, quantity=1))
    session.commit()


def test_home_shows_the_banner_artwork(client, session):
    _stock_something(session)
    body = _body_only(client.get("/").text)
    assert "class='hero-art'" in body
    assert "/assets/nl-vault-banner.jpg" in body
    assert "/assets/nl-vault-banner-900.jpg" in body  # phone-sized source


def test_banner_shows_even_with_no_stock(client, session):
    body = _body_only(client.get("/").text)
    assert "class='hero-art'" in body and "Nothing listed yet" in body


def test_banner_assets_are_served(client):
    for name in ("nl-vault-banner.jpg", "nl-vault-banner-900.jpg"):
        resp = client.get(f"/assets/{name}")
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"


def test_banner_only_appears_on_the_homepage(client, session):
    assert "hero-art'" not in _body_only(client.get("/shipping").text)


def test_hero_links_to_each_marketplace_with_a_url(client, session):
    from automation_control.models import SocialLink

    session.add_all([
        SocialLink(platform="ebay", label="eBay", url="https://www.ebay.com.au/str/nlvault", is_active=True, sort_order=0),
        SocialLink(platform="vinted", label="Vinted", url="https://www.vinted.com.au/member/1", is_active=True, sort_order=1),
        SocialLink(platform="whatnot", label="Whatnot", url="", is_active=True, sort_order=2),  # blank: not shown
    ])
    session.commit()
    body = _body_only(client.get("/").text)
    hero = body.split("class='hero-art'")[1].split("</main>")[0]
    assert "ebay.com.au/str/nlvault" in hero and "vinted.com.au/member/1" in hero
    assert "whatnot" not in hero.lower()
