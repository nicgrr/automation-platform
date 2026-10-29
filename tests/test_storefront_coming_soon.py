"""STOREFRONT_COMING_SOON: a department keeps its tile and page, but its
items are hidden everywhere until it's switched back on."""

import pytest

from automation_control.config import get_settings
from storefront import departments

from tests.test_departments import add_card, add_collectible, client, session  # noqa: F401


@pytest.fixture
def collectables_soon(monkeypatch):
    patched = get_settings().model_copy(update={"storefront_coming_soon": "collectibles"})
    monkeypatch.setattr(departments, "get_settings", lambda: patched)


def test_home_tile_says_coming_soon_instead_of_a_count(client, session, collectables_soon):
    add_card(session, "Charizard")
    add_collectible(session, "Hippo Angel")
    body = client.get("/").text
    assert "href='/shop/collectibles'" in body
    assert "Coming soon" in body
    assert "1 listing to explore" in body  # the Pokémon tile, still counted


def test_a_coming_soon_department_shows_even_with_nothing_in_it(client, session, collectables_soon):
    add_card(session, "Charizard")
    assert "href='/shop/collectibles'" in client.get("/").text


def test_department_page_is_a_noindexed_coming_soon_panel(client, session, collectables_soon):
    add_collectible(session, "Hippo Angel")
    body = client.get("/shop/collectibles").text
    assert "Coming soon" in body
    assert "Hippo Angel" not in body
    assert "noindex" in body


def test_items_are_hidden_from_browse_and_search(client, session, collectables_soon):
    add_card(session, "Charizard Angel")
    add_collectible(session, "Hippo Angel")
    for url in ("/browse", "/browse?q=Angel"):
        body = client.get(url).text
        assert "Charizard Angel" in body
        assert "Hippo Angel" not in body


def test_item_page_redirects_to_the_coming_soon_page(client, session, collectables_soon):
    listing_id = add_collectible(session, "Hippo Angel")
    response = client.get(f"/item/{listing_id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/shop/collectibles"


def test_sitemap_leaves_out_the_department_and_its_items(client, session, collectables_soon):
    card = add_card(session, "Charizard")
    collectible = add_collectible(session, "Hippo Angel")
    body = client.get("/sitemap.xml").text
    assert f"/item/{card}<" in body
    assert f"/item/{collectible}<" not in body
    assert "/shop/collectibles<" not in body


def test_without_the_setting_collectables_behave_normally(client, session):
    listing_id = add_collectible(session, "Hippo Angel")
    assert "Hippo Angel" in client.get("/shop/collectibles").text
    assert client.get(f"/item/{listing_id}").status_code == 200


def test_game_departments_cannot_be_switched_off_this_way(monkeypatch):
    patched = get_settings().model_copy(update={"storefront_coming_soon": "game-pokemon, collectibles"})
    monkeypatch.setattr(departments, "get_settings", lambda: patched)
    assert departments.coming_soon_slugs() == {"collectibles"}
