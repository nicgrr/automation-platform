"""Collectables show what is live on Vinted; old listings and sold figures stay off the site."""
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PIL import Image

from automation_control.config import get_settings
from automation_control.models import PlatformConfig, VintedListing, VintedListingStatus
from storefront import departments, photos

from tests.test_departments import add_card, add_collectible, client, session  # noqa: F401

URL = "https://www.vinted.com.au/items/4567891234"


@pytest.fixture(autouse=True)
def collectables_soon(monkeypatch):
    patched = get_settings().model_copy(update={"storefront_coming_soon": "collectibles"})
    monkeypatch.setattr(departments, "get_settings", lambda: patched)


@pytest.fixture
def media(tmp_path, monkeypatch):
    patched = get_settings().model_copy(update={"scan_media_dir": str(tmp_path), "captured_cards_dir": str(tmp_path)})
    monkeypatch.setattr(photos, "get_settings", lambda: patched)
    path = tmp_path / "figure.jpg"
    Image.new("RGB", (800, 1000), "white").save(path)
    return path


def add_vinted(session, *, listing_id="v1", title="Sonny Angel Owl", price="24.99", url=URL,
               status=VintedListingStatus.ACTIVE, paths=()):
    session.add(VintedListing(id=listing_id, vinted_item_id=listing_id, url=url, title=title, price=Decimal(price),
                              currency="AUD", status=status, posted_at=datetime.now(UTC), source="manual"))
    session.add(PlatformConfig(key=f"vinted_fallback_source:{listing_id}",
                               value={"condition": "Opened / used", "paths": [str(p) for p in paths]}))
    session.commit()


def test_the_page_shows_live_figures_linking_to_vinted(client, session):
    add_vinted(session)
    body = client.get("/shop/collectibles").text
    assert "Sonny Angel Owl" in body and "$24.99" in body and "Buy on Vinted" in body
    assert f"href='{URL}'" in body and "rel='noopener noreferrer'" in body
    assert "Coming soon" not in body and "noindex" not in body


def test_old_listings_and_their_prices_stay_hidden(client, session):
    add_card(session, "Charizard")
    add_collectible(session, "Old Duck", price="38.13")
    add_vinted(session)
    for url in ("/shop/collectibles", "/browse", "/browse?q=Duck"):
        assert "Old Duck" not in client.get(url).text
    assert "38.13" not in client.get("/shop/collectibles").text
    item_page = client.get("/item/l-Old Duck").text
    assert "Old Duck" not in item_page and "38.13" not in item_page


@pytest.mark.parametrize("status", [VintedListingStatus.SOLD, VintedListingStatus.REMOVED])
def test_sold_and_removed_figures_drop_off(client, session, status):
    add_vinted(session, status=status)
    body = client.get("/shop/collectibles").text
    assert "Sonny Angel Owl" not in body and "Coming soon" in body


def test_with_nothing_live_it_is_still_coming_soon(client, session):
    body = client.get("/shop/collectibles").text
    assert "Coming soon" in body and "noindex" in body


def test_home_and_menu_count_live_figures(client, session):
    add_card(session, "Charizard")
    add_vinted(session)
    add_vinted(session, listing_id="v2", title="Sonny Angel Duck", url=URL + "5")
    body = client.get("/").text
    assert "2 listings to explore" in body
    assert "Coming soon" not in body


def test_a_live_figure_serves_its_photo(client, session, media):
    add_vinted(session, paths=[media])
    assert "src='/vinted-photo/v1'" in client.get("/shop/collectibles").text
    response = client.get("/vinted-photo/v1")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"


def test_a_sold_figures_photo_is_no_longer_served(client, session, media):
    add_vinted(session, paths=[media], status=VintedListingStatus.SOLD)
    assert client.get("/vinted-photo/v1").status_code == 404


def test_a_photo_outside_the_media_folder_is_refused(client, session, media):
    add_vinted(session, paths=["/etc/passwd"])
    assert client.get("/vinted-photo/v1").status_code == 404
    assert "/vinted-photo/" not in client.get("/shop/collectibles").text


def test_a_non_https_link_is_never_shown(client, session):
    add_vinted(session, url="javascript:alert(1)")
    assert "javascript:alert" not in client.get("/shop/collectibles").text
