"""Typed quantity, manual-price precedence, star rarities and printing families."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from automation_control.models import CardPrice, InventoryItem
from automation_control.rarity import display_rarity, listing_matches_card, normalize_star, printing_families
from automation_control.scan_ingest.identify import ReferenceCard, _pick_by_art

from tests.test_inventory_review import (  # noqa: F401  (fixtures)
    SET_PAGE, _add_ebay_price, _seed, client, session,
)


def test_typed_quantity_sets_the_exact_number(client, session):
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    resp = client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                                  "quantity": "7", "back": SET_PAGE}, follow_redirects=False)
    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().quantity == 7


def test_typing_zero_removes_the_holding(client, session):
    _seed(session, card_id="xy11-31", number="31", quantity=3)
    client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                           "action": "set", "quantity": "0", "back": SET_PAGE}, follow_redirects=False)
    assert session.scalars(select(InventoryItem)).all() == []


@pytest.mark.parametrize("bad", ["-1", "abc", "1.5", "100000"])
def test_typed_quantity_rejects_nonsense(client, session, bad):
    _seed(session, card_id="xy11-31", number="31", quantity=2)
    resp = client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                                  "quantity": bad, "back": SET_PAGE}, follow_redirects=False)
    assert resp.status_code == 400
    assert session.scalars(select(InventoryItem)).one().quantity == 2


def test_quantity_box_is_rendered_for_owned_cards(client, session):
    _seed(session, card_id="xy11-31", number="31", quantity=4)
    body = client.get(SET_PAGE).text
    assert "name='quantity'" in body and "value='4'" in body


def test_manual_price_beats_a_wild_ebay_price(client, session):
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    _add_ebay_price(session, "xy11-31", "3628.80")
    _add_ebay_price(session, "xy11-31", "8.00", source="manual")
    body = client.get(SET_PAGE).text
    assert "$8.00" in body and "3,628.80" not in body


@pytest.mark.parametrize("raw,expected", [
    ("SEC *", "SEC★"), ("sec☆", "SEC★"), ("R★", "R★"), ("SEC", "SEC"), ("Rare Holo", "Rare Holo"),
])
def test_normalize_star(raw, expected):
    assert normalize_star(raw) == expected


def test_alt_art_gets_a_star_but_base_and_special_rarities_do_not():
    assert display_rarity("SEC", "Portgas.D.Ace (119) (Parallel)") == "SEC★"
    assert display_rarity("R", "Roronoa Zoro - PRB02-006 (Alternate Art)") == "R★"
    assert display_rarity("SEC", "Portgas.D.Ace (119)") == "SEC"
    assert display_rarity("SEC★", "Portgas.D.Ace (Parallel)") == "SEC★"
    assert display_rarity("SP", "Portgas.D.Ace (SP)") == "SP"
    assert display_rarity(None, "x") == "Unknown"


def test_printing_families_and_listing_matching():
    assert printing_families("Portgas.D.Ace (Serial Numbered)") == {"serial"}
    assert printing_families("Portgas.D.Ace (119)") == set()
    base, alt, serial = "Portgas.D.Ace (119)", "Portgas.D.Ace (119) (Parallel)", "Portgas.D.Ace (Serial Numbered)"
    manga_listing = "Ace OP07-119 SEC Manga Rare Alt Art"
    assert not listing_matches_card(manga_listing, base)
    assert not listing_matches_card(manga_listing, alt)
    assert listing_matches_card("Ace OP07-119 SEC Alternate Art", alt)
    assert not listing_matches_card("Ace OP07-119 SEC Serial Numbered /10", alt)
    assert listing_matches_card("Ace OP07-119 SEC Serial Numbered /10", serial)
    assert listing_matches_card("Ace OP07-119 SEC", base)


def _ref(card_id, name):
    return ReferenceCard(card_id=card_id, number="OP07-119", name=name, phash="0" * 16)


def test_tied_art_prefers_the_commonest_printing_not_catalogue_order():
    serial = _ref("tcgp-630009", "Portgas.D.Ace (Serial Numbered)")
    alt = _ref("tcgp-545921", "Portgas.D.Ace (119) (Parallel)")
    base = _ref("tcgp-545920", "Portgas.D.Ace (119)")
    pick, decisive = _pick_by_art(None, [serial, alt, base])
    assert pick is base and not decisive
    # a printed star says alt art, so the plain card is not the answer
    pick, _ = _pick_by_art(None, [serial, alt, base], "SEC★")
    assert pick is alt
    # alt art outranks serial-numbered even when only those two are in play
    pick, _ = _pick_by_art(None, [serial, alt])
    assert pick is alt


def test_ebay_import_ignores_other_printings_and_outliers():
    from scripts import import_ebay_prices as script

    listings = [
        {"title": "Portgas.D.Ace OP07-119 SEC", "price": "40"},
        {"title": "Portgas.D.Ace OP07-119 SEC Manga Rare", "price": "3628.80"},
        {"title": "Portgas.D.Ace OP07-119 SEC Alt Art", "price": "260"},
    ]
    assert script._price_values(listings, "Portgas.D.Ace", card_name="Portgas.D.Ace (119)") == [Decimal("40")]
    # no genuine listing at all -> unpriced, never the manga price
    assert script._price_values(listings[1:2], "Portgas.D.Ace", card_name="Portgas.D.Ace (119)") == []
    # a stray 10x listing that slipped past the title filter is trimmed
    cheap = [{"title": "Ace OP07-119", "price": p} for p in ("10", "11", "12", "400")]
    assert script._price_values(cheap, "Ace", card_name="Portgas.D.Ace (119)") == [Decimal(x) for x in ("10", "11", "12")]


def test_ebay_import_skips_lots_graded_foreign_and_sp_listings():
    from scripts import import_ebay_prices as script

    name = "Ms. All Sunday"
    titles = {
        "Ms. All Sunday OP14-084 Super Rare One Piece TCG": "5",
        "MS. ALL SUNDAY OP14-084 SUPER RARE JAPANESE": "1.5",
        "Ms. All Sunday OP14-084 SR 4x Playset": "30",
        "Ms. All Sunday OP14-084 SR PSA 10": "90",
        "Ms. All Sunday OP14-084 SR* Alt Art": "90",
        "Ms. All Sunday OP14-084 SR SP The Time of Battle": "800",
        "Ms. All Sunday OP14-084 SR | ALT | One Piece TCG": "48",
    }
    listings = [{"title": t, "price": p} for t, p in titles.items()]
    assert script._price_values(listings, "Ms. All Sunday", card_name=name, language="English") == [Decimal("5")]
    alt = script._price_values(listings, "Ms. All Sunday", card_name=name + " (Alternate Art)", language="English")
    assert alt == [Decimal("90"), Decimal("48")]


def test_ebay_query_names_the_printing():
    from scripts import import_ebay_prices as script

    assert script._printing_words("Charlotte Linlin (112) (Alternate Art)") == " alt art"
    assert script._printing_words("Charlotte Linlin (112) (Manga)") == " manga"
    assert script._printing_words("Charlotte Linlin (112)") == ""
