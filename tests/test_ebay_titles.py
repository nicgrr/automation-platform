from automation_control.ebay_titles import build_single_card_ebay_title
from automation_control.models import (
    CardSet, CardVariant, CatalogItem, CatalogItemType, InventoryItem, TcgCard,
)


def _item(name: str = "Chien-Pao ex") -> CatalogItem:
    return CatalogItem(id="card-1", name=name, item_type=CatalogItemType.TCG_CARD)


def test_pokemon_title_contains_exact_printing_metadata():
    card = TcgCard(
        catalog_item_id="card-1", game="pokemon", set_id="sv4pt5", number="242",
        rarity="Hyper Rare", language="English",
    )
    card_set = CardSet(id="sv4pt5", name="Paldean Fates", printed_total=91, game="pokemon")
    inventory = InventoryItem(
        id="stock-1", catalog_item_id="card-1", variant=CardVariant.HOLO, quantity=1,
    )

    title = build_single_card_ebay_title(_item(), card, card_set, inventory)

    assert title == "Chien-Pao ex 242/091 Paldean Fates Hyper Rare Holo Pokémon TCG English"
    assert len(title) <= 80


def test_existing_code_is_not_repeated_and_alt_art_is_preserved():
    card = TcgCard(
        catalog_item_id="card-1", game="dragon_ball_fusion_world", set_id="db-fw9",
        number="FB09-036", rarity="Rare", parallel="Alternate Art", language="Japanese",
    )
    card_set = CardSet(id="db-fw9", name="Dual Evolution", game="dragon_ball_fusion_world")

    title = build_single_card_ebay_title(
        _item("Son Goku - FB09-036 (Alternate Art)"), card, card_set,
    )

    assert title.count("FB09-036") == 1
    assert "Alt Art" in title or "Alternate Art" in title
    assert "Dual Evolution" in title
    assert "DB Fusion World" in title
    assert "Japanese" in title
    assert len(title) <= 80


def test_long_title_keeps_number_game_and_language_inside_ebay_limit():
    card = TcgCard(
        catalog_item_id="card-1", game="dragon_ball_fusion_world", set_id="db-long",
        number="FB04-091", rarity="Super Rare · Dokkan Battle Alternate Art",
        parallel="Dragon Ball Z Dokkan Battle Alternate Art", language="Japanese",
    )
    card_set = CardSet(
        id="db-long", name="Ultra Limit Alternate Art Reprints", game="dragon_ball_fusion_world",
    )

    title = build_single_card_ebay_title(
        _item("Majin Buu : Evil - FB04-091 (Dragon Ball Z Dokkan Battle Alternate Art)"),
        card,
        card_set,
    )

    assert len(title) <= 80
    assert "FB04-091" in title
    assert "DB Fusion World" in title
    assert title.endswith("Japanese")
    assert "…" not in title


def test_one_piece_title_includes_set_code_rarity_and_finish():
    card = TcgCard(
        catalog_item_id="card-1", game="one_piece", set_id="op-eb03", number="EB03-055",
        rarity="Super Rare", language="English",
    )
    card_set = CardSet(id="op-eb03", name="Heroines Edition", game="one_piece")
    inventory = InventoryItem(
        id="stock-1", catalog_item_id="card-1", variant=CardVariant.HOLO, quantity=1,
    )

    title = build_single_card_ebay_title(_item("Nico Robin"), card, card_set, inventory)

    assert "Nico Robin" in title
    assert "EB03-055" in title
    assert "Heroines Edition" in title
    assert "Super Rare" in title
    assert "Holo" in title
    assert "One Piece TCG" in title
    assert title.endswith("English")
    assert len(title) <= 80
