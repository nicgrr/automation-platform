"""Four top-level categories: Pokémon, One Piece, Dragon Ball, Collectables."""

from tests.test_departments import (  # noqa: F401  (fixtures + helpers)
    add_bulk_lot, add_card, add_collectible, add_sealed, client, session,
)
from storefront import departments


def _stock(session):
    add_card(session, "Pika", game="pokemon")
    add_card(session, "Pikachu JP", game="pokemon_japan")
    add_card(session, "Luffy", game="one_piece")
    add_card(session, "Goku", game="dragon_ball_super")
    add_card(session, "Gohan", game="dragon_ball_fusion_world")
    add_card(session, "Vegeta", game="super_dragon_ball_heroes")
    add_collectible(session, "Sheep")


def test_franchise_ranges_are_one_department_each(session):
    _stock(session)
    slugs = [d.slug for d, _ in departments.in_stock(session)]
    assert slugs == ["game-pokemon", "game-one_piece", "game-dragon_ball", "collectibles"]


def test_department_names_and_counts(session):
    _stock(session)
    by_slug = {d.slug: (d.name, n) for d, n in departments.in_stock(session)}
    assert by_slug["game-pokemon"] == ("Pokémon", 2)
    assert by_slug["game-dragon_ball"] == ("Dragon Ball", 3)
    assert by_slug["collectibles"][0] == "Collectables"


def test_homepage_shows_just_the_four_categories_in_order(client, session):
    _stock(session)
    body = client.get("/").text
    positions = [body.index(f"href='/shop/{slug}'") for slug in
                 ("game-pokemon", "game-one_piece", "game-dragon_ball", "collectibles")]
    assert positions == sorted(positions)
    assert body.count("class='dept-card'") == 4
    assert "Fresh in the vault" not in body


def test_dragon_ball_page_lists_every_range_and_sub_tabs(client, session):
    _stock(session)
    body = client.get("/shop/game-dragon_ball").text
    for name in ("Goku", "Gohan", "Vegeta"):
        assert name in body
    for label in ("All Dragon Ball", "Dragon Ball Super", "Fusion World", "Super Dragon Ball Heroes"):
        assert label in body


def test_choosing_a_sub_category_narrows_the_page(client, session):
    _stock(session)
    body = client.get("/shop/game-dragon_ball?sub=dragon_ball_fusion_world").text
    assert "Gohan" in body and "Goku" not in body and "Vegeta" not in body


def test_unknown_sub_category_is_ignored(client, session):
    _stock(session)
    body = client.get("/shop/game-dragon_ball?sub=nonsense").text
    assert "Goku" in body and "Gohan" in body


def test_pokemon_splits_english_and_japanese(client, session):
    _stock(session)
    assert "Japanese" in client.get("/shop/game-pokemon").text
    jp = client.get("/shop/game-pokemon?sub=pokemon_japan").text
    assert "Pikachu JP" in jp and ">Pika<" not in jp


def test_a_single_range_franchise_has_no_sub_tabs(client, session):
    _stock(session)
    assert "<a class='sub-tab" not in client.get("/shop/game-one_piece").text


def test_collectables_filter_by_brand(client, session):
    _stock(session)
    body = client.get("/shop/collectibles").text
    assert "All Collectables" in body and "Sonny Angel" in body
    assert "Sheep" in client.get("/shop/collectibles?brand=Sonny+Angel").text
    assert "Sheep" in client.get("/shop/collectibles?brand=Unknown+Brand").text  # not a real brand: ignored


def test_old_range_bookmarks_redirect_to_the_franchise(client, session):
    _stock(session)
    resp = client.get("/shop/game-dragon_ball_super", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/shop/game-dragon_ball?sub=dragon_ball_super"
    assert client.get("/shop/game-nonsense", follow_redirects=False).status_code == 404


def test_segment_links_keep_the_chosen_sub_category(client, session):
    _stock(session)
    add_sealed(session, "Booster Box", game="dragon_ball_super")
    body = client.get("/shop/game-dragon_ball?sub=dragon_ball_super").text
    assert "sub=dragon_ball_super" in body and "seg=sealed" in body


def test_bulk_lots_count_toward_the_franchise(session):
    add_bulk_lot(session, game="dragon_ball_fusion_world")
    assert dict((d.slug, n) for d, n in departments.in_stock(session))["game-dragon_ball"] == 1
