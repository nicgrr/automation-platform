import argparse

import pytest

from scripts.convert_to_ebay_template import (
    PLACEHOLDER, SINGLE_CATEGORY_ID, build_variation_group, convert_row, read_ebay_header, sanitize_title,
)


# --- Franchise/Manufacturer by game (fixed 2026-09-22 -- was hardcoded
# Pokémon on every row, which would have mislabeled a Dragon Ball card) --

def test_a_pokemon_row_gets_pokemon_franchise():
    out = convert_row(_row(game="pokemon"), _args())
    assert out["*C:Franchise"] == "Pokémon"
    assert out["*C:Manufacturer"] == "The Pokémon Company"


def test_a_dragon_ball_row_gets_dragon_ball_franchise_not_pokemon():
    out = convert_row(_row(game="dragon_ball_super_divers"), _args())
    assert out["*C:Franchise"] == "Dragon Ball"
    assert out["*C:Manufacturer"] == "Bandai"


def test_a_one_piece_row_gets_one_piece_franchise():
    out = convert_row(_row(game="one_piece"), _args())
    assert out["*C:Franchise"] == "One Piece"
    assert out["*C:Manufacturer"] == "Bandai"


def test_a_row_with_no_game_field_falls_back_to_pokemon():
    """build_variation_rows' own rows don't carry "game" (that population
    is Pokemon-only) -- must not KeyError, and must default correctly."""
    row = _row()
    assert "game" not in row
    out = convert_row(row, _args())
    assert out["*C:Franchise"] == "Pokémon"


def _args(**overrides):
    base = dict(condition_id="4000", lot_condition_id="1000", location="Sydney NSW", dispatch_days="1",
                payment_policy="Payments", return_policy="Returns")
    base.update(overrides)
    return argparse.Namespace(**base)


def _row(**overrides):
    base = dict(sku="sku-1", title="Abra #54", description="Abra #54", condition="Near Mint",
                price="2.34", currency="AUD", quantity="1", weight_grams="15",
                image_url="https://example.test/abra.png", shipping_policy="Letter Mail",
                set_name="Unified Minds", rarity="", set_total="94")
    base.update(overrides)
    return base


def test_read_ebay_header_rejects_a_file_that_isnt_the_real_template(tmp_path):
    bad = tmp_path / "not_a_template.csv"
    bad.write_text("sku,title,price\n1,Abra,2.34\n")
    with pytest.raises(SystemExit):
        read_ebay_header(str(bad))


def test_read_ebay_header_parses_the_r_delimited_real_template(tmp_path):
    template = tmp_path / "template.csv"
    template.write_bytes(
        "﻿Info,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
        "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8),CustomLabel,*Category\r"
        .encode("utf-8")
    )
    info_row, header = read_ebay_header(str(template))
    assert header == ["*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)", "CustomLabel", "*Category"]
    # The Info row comes back too: an upload without it fails every row.
    assert info_row.startswith("Info,Version=")


def test_a_single_card_row_splits_name_and_number_from_the_title():
    out = convert_row(_row(title="Abra #54"), _args())
    assert out["C:Card Name"] == "Abra"
    assert out["C:Card Number"] == "54"
    assert "C:Number of Cards" not in out


def test_a_bulk_lot_row_gets_card_count_instead_of_a_name_and_number():
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)", weight_grams="143"), _args())
    assert out["C:Number of Cards"] == "21"
    assert "C:Card Name" not in out
    assert "C:Card Number" not in out


def test_weight_is_carried_through_with_a_grams_unit():
    out = convert_row(_row(weight_grams="635"), _args())
    assert out["C:Item Weight"] == "635 g"


def test_shipping_policy_passes_through_unchanged_from_the_upstream_export():
    out = convert_row(_row(shipping_policy="Trading Cards & Sealed TCG - Tracked Parcel"), _args())
    assert out["ShippingProfileName"] == "Trading Cards & Sealed TCG - Tracked Parcel"


def test_a_missing_real_value_stays_a_visible_placeholder_not_a_blank():
    """A blank cell in the uploaded file is easy to miss; a loud
    placeholder string is not -- this must never silently pass through
    an unfilled required field as empty."""
    out = convert_row(_row(), _args(location=PLACEHOLDER))
    assert out["*Location"] == PLACEHOLDER


def test_a_single_card_gets_the_individual_cards_category_not_the_lots_one():
    out = convert_row(_row(title="Abra #54"), _args())
    assert out["*Category"] == "183454"


def test_a_bulk_lot_gets_the_mixed_card_lots_category():
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)"), _args())
    assert out["*Category"] == "183455"


def test_set_name_passes_through_to_the_required_c_set_field():
    out = convert_row(_row(set_name="Unified Minds"), _args())
    assert out["*C:Set"] == "Unified Minds"


def test_a_single_card_gets_the_confirmed_near_mint_condition_descriptor():
    """Regression guard for a real failed upload: 990/1014 rows failed
    with "Card Condition (40001) is a required field" because this
    descriptor was missing entirely, not just wrong."""
    out = convert_row(_row(title="Abra #54"), _args())
    assert out["CD:Card Condition - (ID: 40001)"] == "400010"


def test_a_bulk_lot_does_not_get_the_singles_condition_descriptor():
    """The 40001 scheme is specific to the singles category (183454) --
    sending it for a lot (183455) is an unverified guess this script
    deliberately avoids making twice."""
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)"), _args())
    assert "CD:Card Condition - (ID: 40001)" not in out


def test_gender_symbols_are_spelled_out_not_stripped():
    """Regression guard for a real failed upload: eBay rejected 3 titles
    outright (error 240, "special characters... e.g. TM, heart, 1/2,
    star") because Nidoran's own printed name carries a gender symbol."""
    assert sanitize_title("Nidoran ♀ #29") == "Nidoran F #29"
    assert sanitize_title("Nidoran ♂ #43") == "Nidoran M #43"
    assert sanitize_title("Team Rocket's Nidoran♀ #114") == "Team Rocket's NidoranF #114"


def test_other_disallowed_symbol_categories_are_stripped_not_just_the_two_seen_so_far():
    """The two Nidoran symbols are what a real upload actually hit, but
    eBay's own error names TM/heart/half/star as further examples -- this
    must not be a fix for only the two characters that happened to fail."""
    assert sanitize_title("Charizard ★") == "Charizard "
    assert sanitize_title("Energy™ Card ½") == "Energy Card "


def test_ordinary_accented_letters_survive_sanitizing():
    """Pokémon itself has an accent -- the sanitizer must only strip
    symbol/fraction categories, never ordinary accented Latin letters."""
    assert sanitize_title("Pokémon Center Naviuwu") == "Pokémon Center Naviuwu"


def test_a_sanitized_title_still_splits_name_and_number_correctly():
    out = convert_row(_row(title="Nidoran ♀ #29"), _args())
    assert out["*Title"] == "Nidoran F #29"
    assert out["C:Card Name"] == "Nidoran F"
    assert out["C:Card Number"] == "29"


def test_singles_and_lots_use_different_condition_ids():
    """A real upload confirmed ConditionID 4000 is valid for singles
    (183454) but explicitly invalid for lots (183455) -- they must never
    share one --condition-id value."""
    single = convert_row(_row(title="Abra #54"), _args(condition_id="4000", lot_condition_id="1000"))
    lot = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)"), _args(condition_id="4000", lot_condition_id="1000"))
    assert single["*ConditionID"] == "4000"
    assert lot["*ConditionID"] == "1000"


def test_the_written_file_starts_with_the_info_row_and_a_bom(tmp_path):
    """The failure this guards produced "1,014 failed, 0 completed" on a
    real upload. File Exchange uses the Info row to identify which template
    a file was built from; without it every row fails while the row count
    still parses -- so it reads as a thousand bad listings rather than one
    bad file. The BOM goes with it: eBay's own template carries one and the
    data is not ASCII.
    """
    import subprocess
    import sys

    # Built from the converter's own output columns so a new field can't
    # quietly leave this test asserting against a stale template.
    columns = list(convert_row(_row(), _args()))
    action = "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)"
    header = [action] + [c for c in columns if c != action]

    template = tmp_path / "template.csv"
    template.write_bytes(
        ("\ufeffInfo,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
         + ",".join(header) + "\r").encode("utf-8")
    )
    source = tmp_path / "in.csv"
    source.write_text(
        "sku,title,description,condition,price,currency,quantity,weight_grams,image_url,shipping_policy,set_name\n"
        "abc,Abra #54,a Pok\u00e9mon card,New,2.34,AUD,1,15,http://x/y.png,letter,Mega Evolution\n",
        encoding="utf-8")
    out = tmp_path / "out.csv"

    subprocess.run([sys.executable, "-m", "scripts.convert_to_ebay_template",
                    "--template", str(template), "--in", str(source), "--out", str(out),
                    "--location", "Sydney", "--dispatch-days", "1",
                    "--payment-policy", "P", "--return-policy", "R"],
                   check=True, capture_output=True)

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0] == "Info,Version=1.0.0,Template=fx_category_template_EBAY_AU"
    assert lines[1].startswith("*Action(")
    assert lines[2].startswith("Add,")


# --- build_variation_group: one listing per set, a variation per card ---

def _cards(*numbers, set_name="Obsidian Flames", set_total="99"):
    return [
        _row(sku=f"sku-{n}", title=f"Card{n} #{n}", price=f"{n}.00", set_name=set_name, set_total=set_total)
        for n in numbers
    ]


def test_one_parent_row_plus_one_child_row_per_card():
    rows, skipped = build_variation_group("Obsidian Flames", _cards(4, 21, 63), _args())
    assert len(rows) == 4  # 1 parent + 3 children
    assert skipped == []


def test_the_parent_row_has_no_relationship_and_lists_every_value():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, 21, 63), _args())
    parent = rows[0]
    assert parent["Relationship"] == ""
    assert parent["RelationshipDetails"] == "MPN=04 - Card4;21 - Card21;63 - Card63"
    assert parent["*Quantity"] == ""
    assert parent["*StartPrice"] == ""
    assert parent["C:Card Name"] == ""
    assert parent["C:Card Number"] == ""


def test_each_child_row_is_marked_variation_with_its_own_value_and_price():
    """Format ("001/162 Pineco") matches a real, live, successful
    competitor listing inspected directly (2026-09-14), not a guess --
    the bare-number "Card #" value from the first attempt told a buyer
    nothing useful in the picker."""
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, 21), _args())
    children = rows[1:]
    assert {c["RelationshipDetails"] for c in children} == {"MPN=04 - Card4", "MPN=21 - Card21"}
    for c in children:
        assert c["Relationship"] == "Variation"
    prices = {c["RelationshipDetails"]: c["*StartPrice"] for c in children}
    assert prices["MPN=04 - Card4"] == "4.00"
    assert prices["MPN=21 - Card21"] == "21.00"


def test_the_padding_matches_the_sets_own_digit_width():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, set_total="8"), _args())
    assert rows[1]["RelationshipDetails"] == "MPN=4 - Card4"


def test_the_parent_gets_a_pick_your_card_title_and_the_sets_own_category_id():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4), _args())
    parent = rows[0]
    assert parent["*Title"] == "Obsidian Flames - Complete Your Master Set"
    assert parent["*Category"] == SINGLE_CATEGORY_ID
    assert parent["CustomLabel"] == "set-variation-obsidian-flames"


def test_a_card_with_no_readable_number_is_skipped_not_a_broken_variation():
    cards = _cards(4) + [_row(sku="sku-x", title="No Number Card", price="2.00", set_name="Obsidian Flames")]
    rows, skipped = build_variation_group("Obsidian Flames", cards, _args())
    # only the one real card became a variation; the other contributed nothing
    assert len(rows) == 2
    assert rows[1]["RelationshipDetails"] == "MPN=04 - Card4"
    assert "No Number Card" in skipped[0]


def test_a_second_card_sharing_a_printed_number_is_skipped_not_a_colliding_variation():
    """Regression guard for a real failed upload: 3 of 24 sets were
    rejected with "Duplicate VariationSpecifics trait value" because an
    alt-art/reprint shares its base card's number -- eBay has no way to
    tell two options both keyed on MPN 148 apart."""
    cards = _cards(148) + [_row(sku="sku-alt", title="Charizard (Alt Art) #148", price="20.00", set_name="Obsidian Flames")]
    rows, skipped = build_variation_group("Obsidian Flames", cards, _args())
    assert len(rows) == 2  # 1 parent + only the first #148
    assert rows[1]["CustomLabel"] == "sku-148"
    assert "sku-alt" in skipped[0]
    assert "148" in skipped[0]



def test_a_field_missing_from_this_categorys_template_is_dropped_not_a_crash(tmp_path):
    """Confirmed live against the real downloaded template: this account's
    Trading Cards category has no C:Rarity column, even though convert_row
    emits it when a card's rarity is known. DictWriter's strict "no extra
    keys" check would otherwise fail the whole export over one optional
    field a different category's template might well support."""
    import subprocess
    import sys

    columns = list(convert_row(_row(), _args()))
    action = "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)"
    # A template deliberately missing C:Rarity, the way the real one is.
    header = [action] + [c for c in columns if c not in (action, "C:Rarity")]

    template = tmp_path / "template.csv"
    template.write_bytes(
        ("\ufeffInfo,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
         + ",".join(header) + "\r").encode("utf-8")
    )
    source = tmp_path / "in.csv"
    source.write_text(
        "sku,title,description,condition,price,currency,quantity,weight_grams,image_url,shipping_policy,set_name,rarity\n"
        "abc,Abra #54,a card,New,2.34,AUD,1,15,http://x/y.png,letter,Mega Evolution,Rare\n",
        encoding="utf-8")
    out = tmp_path / "out.csv"

    result = subprocess.run([sys.executable, "-m", "scripts.convert_to_ebay_template",
                            "--template", str(template), "--in", str(source), "--out", str(out),
                            "--location", "Sydney", "--dispatch-days", "1",
                            "--payment-policy", "P", "--return-policy", "R"],
                           capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    lines = out.read_bytes().decode("utf-8-sig").splitlines()
    assert "C:Rarity" not in lines[1]  # the header the row was written against
    assert lines[2].startswith("Add,")  # the row itself still made it through


def test_the_variant_is_appended_to_the_variation_value():
    """A buyer choosing between a Normal and a Reverse Holo of the same
    numbered card needs to see which is which in the picker -- the MPN
    value is the only label eBay shows there."""
    cards = [_row(sku="sku-4", title="Card4 #4", price="4.00", set_total="99", variant="Reverse Holo")]
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    assert rows[1]["RelationshipDetails"] == "MPN=04 - Card4 (RH)"


def test_two_different_printings_of_the_same_number_are_both_included():
    """Unlike a genuine reprint/alt-art collision, a Normal and a Reverse
    Holo of the same card number are both real, distinct sellable copies
    -- the printing (folded into the value) is what tells them apart, so
    neither should be dropped as a duplicate."""
    cards = [
        _row(sku="sku-4-normal", title="Card4 #4", price="1.00", set_total="99", variant="Normal"),
        _row(sku="sku-4-rh", title="Card4 #4", price="3.00", set_total="99", variant="Reverse Holo"),
    ]
    rows, skipped = build_variation_group("Obsidian Flames", cards, _args())
    assert skipped == []
    children = rows[1:]
    assert {c["RelationshipDetails"] for c in children} == {
        "MPN=04 - Card4 (N)", "MPN=04 - Card4 (RH)",
    }


def test_a_genuine_reprint_with_the_same_number_and_printing_is_still_skipped():
    """The original duplicate-collision guard still applies when the
    printing is also identical -- that's the real alt-art/reprint case,
    not two distinct printings of one card."""
    cards = [
        _row(sku="sku-148", title="Charizard #148", price="20.00", set_total="99", variant="Holo"),
        _row(sku="sku-alt", title="Charizard (Alt Art) #148", price="30.00", set_total="99", variant="Holo"),
    ]
    rows, skipped = build_variation_group("Obsidian Flames", cards, _args())
    assert len(rows) == 2  # 1 parent + only the first
    assert "sku-alt" in skipped[0]


def test_the_parent_picture_is_the_sets_logo_not_a_cards_photo():
    cards = _cards(4, 21)
    cards[0]["set_logo_url"] = "https://example.test/obsidian-flames-logo.png"
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    assert rows[0]["PicURL"] == "https://example.test/obsidian-flames-logo.png"


def test_the_parent_picture_falls_back_to_a_cards_photo_with_no_set_logo():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4), _args())
    assert rows[0]["PicURL"] == "https://example.test/abra.png"
