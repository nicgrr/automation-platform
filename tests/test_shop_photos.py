from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.config import get_settings
from automation_control.database import Base, get_session
from automation_control.models import (
    CatalogItem, CatalogItemType, InventoryItem, ShopListing, ShopListingStatus,
)
from storefront.app import create_app
from storefront.photos import has_own_photo, safe_source


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


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Point the media directory at a temp dir and hand back a real image."""
    from PIL import Image

    root = tmp_path / "media"
    root.mkdir()
    photo = root / "scan.jpg"
    Image.new("RGB", (750, 1050), (120, 90, 60)).save(photo)

    settings = get_settings()
    monkeypatch.setattr(settings, "scan_media_dir", str(root))
    get_settings.cache_clear()
    monkeypatch.setattr("storefront.photos.get_settings", lambda: settings)
    return root, photo


def make_listing(session, photo_path, *, status=ShopListingStatus.ACTIVE):
    item = CatalogItem(id="ci1", item_type=CatalogItemType.TCG_CARD, name="Gastrodon",
                       image_url="https://images.pokemontcg.io/x/1.png")
    inv = InventoryItem(id="inv1", catalog_item_id="ci1", quantity=1,
                        scan_image_path=str(photo_path) if photo_path else None)
    listing = ShopListing(id="l1", catalog_item_id="ci1", inventory_item_id="inv1",
                          title="Gastrodon #29", price=Decimal("12.00"), currency="AUD",
                          quantity=1, status=status,
                          image_url="https://images.pokemontcg.io/x/1.png")
    session.add_all([item, inv, listing])
    session.commit()
    return listing


def test_photo_route_serves_the_sellers_own_scan(client, session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    response = client.get("/photo/l1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert len(response.content) > 0


def test_photo_is_refused_for_a_listing_that_is_not_active(client, session, media_root):
    """The inventory table covers the whole collection. Without the ACTIVE
    check this route would let anyone enumerate every card you own, one id
    at a time -- listing something publicly is what grants access."""
    _, photo = media_root
    make_listing(session, photo, status=ShopListingStatus.DRAFT)
    assert client.get("/photo/l1").status_code == 404


def test_photo_is_a_404_for_an_unknown_listing(client, media_root):
    assert client.get("/photo/does-not-exist").status_code == 404


def test_a_path_outside_the_media_root_is_refused(session, media_root):
    """A database row rewritten to point at a system file must read as a
    404, not disclose the file."""
    assert safe_source("/etc/passwd") is None
    assert safe_source("../../etc/passwd") is None
    assert safe_source(None) is None


def test_a_traversal_path_inside_a_listing_is_refused(client, session, media_root):
    root, _ = media_root
    make_listing(session, f"{root}/../../../etc/passwd")
    assert client.get("/photo/l1").status_code == 404


def test_a_missing_file_is_refused_not_a_crash(client, session, media_root):
    root, _ = media_root
    make_listing(session, root / "gone.jpg")
    assert client.get("/photo/l1").status_code == 404


def test_has_own_photo_reflects_reality(session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    assert has_own_photo(session, "l1") is True
    assert has_own_photo(session, "nope") is False


def test_listing_without_a_scan_falls_back_to_reference_art(client, session, media_root):
    make_listing(session, None)
    body = client.get("/shop/tcg").text
    assert "images.pokemontcg.io" in body
    assert "Stock image" in body
    assert "/photo/l1" not in body


def test_a_photographed_listing_shows_the_real_card_and_says_so(client, session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    body = client.get("/shop/tcg").text
    assert "/photo/l1" in body
    assert "Actual card" in body
    # the stock URL must not also be rendered -- two images would be shown
    assert "images.pokemontcg.io" not in body


def test_item_page_uses_the_real_photo_too(client, session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    body = client.get("/item/l1").text
    assert "/photo/l1" in body and "Actual card" in body
