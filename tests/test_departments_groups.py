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


def test_sealed_games_saved_as_display_text_join_their_franchise(session):
    """Listing Studio saved sealed games as "One Piece" and "Dragon Ball Super
    Fusion World", which made extra home tiles beside the real departments."""
    _stock(session)
    add_sealed(session, "OP box", game="One Piece")
    add_sealed(session, "FW box", game="Dragon Ball Super Fusion World")
    by_slug = {d.slug: n for d, n in departments.in_stock(session)}
    assert set(by_slug) == {"game-pokemon", "game-one_piece", "game-dragon_ball", "collectibles", "sealed"}
    assert by_slug["game-one_piece"] == 2 and by_slug["game-dragon_ball"] == 4


def test_fusion_world_tab_includes_display_text_sealed(client, session):
    _stock(session)
    add_sealed(session, "FW box", game="Dragon Ball Super Fusion World")
    body = client.get("/shop/game-dragon_ball").text
    assert body.count(">Fusion World<") == 1
    narrowed = client.get("/shop/game-dragon_ball?sub=dragon_ball_fusion_world").text
    assert "FW box" in narrowed and "Gohan" in narrowed and "Goku" not in narrowed


def test_sealed_department_gathers_every_game_with_franchise_tabs(client, session):
    _stock(session)
    add_sealed(session, "ETB", game="pokemon")
    add_sealed(session, "OP box", game="One Piece")
    add_sealed(session, "FW box", game="Dragon Ball Super Fusion World")
    body = client.get("/shop/sealed").text
    assert all(name in body for name in ("ETB", "OP box", "FW box"))
    assert "Pika" not in body and "Luffy" not in body
    for label in ("All Sealed products", ">Pokémon<", ">One Piece<", ">Dragon Ball<"):
        assert label in body
    one_piece = client.get("/shop/sealed?sub=one_piece").text
    assert "OP box" in one_piece and "ETB" not in one_piece and "FW box" not in one_piece
    # still under each game's own department too
    assert "ETB" in client.get("/shop/game-pokemon?seg=sealed").text


def test_other_replaces_apparel_and_old_links_redirect(client, session):
    from decimal import Decimal
    from automation_control.models import CatalogItem, CatalogItemType, ShopListing, ShopListingStatus

    for item_id, kind in (("shirt", CatalogItemType.APPAREL), ("mat", CatalogItemType.ACCESSORY),
                          ("misc", CatalogItemType.OTHER)):
        session.add(CatalogItem(id=item_id, item_type=kind, name=item_id))
        session.add(ShopListing(id=f"l-{item_id}", catalog_item_id=item_id, title=f"Item {item_id}",
                                price=Decimal("5"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE))
    session.commit()
    by_slug = {d.slug: (d.name, n) for d, n in departments.in_stock(session)}
    assert by_slug["other"] == ("Other", 3) and "apparel" not in by_slug
    resp = client.get("/shop/apparel", follow_redirects=False)
    assert resp.status_code == 301 and resp.headers["location"] == "/shop/other"


def test_tile_uses_a_published_ebay_photo_in_live_mode(session, monkeypatch):
    """The dearest sealed item has no published photo, so the tile skips to
    the next live listing that does instead of showing a placeholder."""
    from automation_control.models import EbayListing, EbayListingLink, EbayListingLinkStatus
    from storefront import app as storefront_app, live_inventory

    for name, price, photos in (("Booster box", "300", []), ("ETB", "90", ["https://i.ebayimg.com/etb.jpg"])):
        add_sealed(session, name, game="pokemon", price=price)
        remote = f"r-{name}"
        session.add(EbayListing(ebay_listing_id=remote, title=name, current_price_minor=int(price) * 100,
                                currency="AUD", quantity=1, listing_status="Active", raw_snapshot={"image_urls": photos}))
        session.add(EbayListingLink(shop_listing_id=f"l-{name}", sku=f"l-{name}", marketplace_id="EBAY_AU",
                                    status=EbayListingLinkStatus.ACTIVE, ebay_listing_id=remote))
    session.commit()
    monkeypatch.setattr(live_inventory, "enabled", lambda: True)
    tile = storefront_app._dept_hero(session, departments.find(session, "sealed"))
    assert "src='https://i.ebayimg.com/etb.jpg'" in tile and "Photos on eBay" not in tile
