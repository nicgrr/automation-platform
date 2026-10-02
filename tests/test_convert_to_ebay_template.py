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


def test_a_dragon_ball_carddass_row_gets_dragon_ball_franchise_not_pokemon():
    """Real gap found 2026-09-26: dragon_ball_carddass and
    dragon_ball_art_card were never added to GAME_ITEM_SPECIFICS when the
    other Dragon Ball games were, so 12 real vintage Carddass/art-card
    listings silently exported as Franchise=Pokémon."""
    out = convert_row(_row(game="dragon_ball_carddass"), _args())
    assert out["*C:Franchise"] == "Dragon Ball"
    assert out["*C:Manufacturer"] == "Bandai"


def test_a_dragon_ball_art_card_row_gets_dragon_ball_franchise_not_pokemon():
    out = convert_row(_row(game="dragon_ball_art_card"), _args())
    assert out["*C:Franchise"] == "Dragon Ball"
    # Not Bandai: real mislabeling found 2026-09-28 -- this catalogue's
    # only dragon_ball_art_card set is explicitly "(Unofficial)", a
    # fan-made product Bandai never released. "Unbranded" is eBay's own
    # standard convention for an item with no real manufacturer to name.
    assert out["*C:Manufacturer"] == "Unbranded"


def test_a_dragon_ball_z_row_gets_panini_manufacturer_not_bandai():
    """Real mislabeling found 2026-09-28, auditing every Dragon Ball game
    key: dragon_ball_z is this catalogue's key for the Score/Panini-
    published Dragon Ball Z CCG (every one of its 1806 real cards' own
    CardSet name already says "Panini: ..."), not a Bandai product."""
    out = convert_row(_row(game="dragon_ball_z"), _args())
    assert out["*C:Franchise"] == "Dragon Ball"
    assert out["*C:Manufacturer"] == "Panini"


def test_a_dragon_ball_artbox_row_gets_artbox_manufacturer_not_bandai():
    """Real mislabeling found 2026-09-27: these 12 cards are 1996 Artbox
    Entertainment trading cards, not a Bandai product -- they'd shared
    the "dragon_ball_z" game key (Manufacturer=Bandai) purely because the
    set they lived in had never been given its own real identity."""
    out = convert_row(_row(game="dragon_ball_artbox"), _args())
    assert out["*C:Franchise"] == "Dragon Ball"
    assert out["*C:Manufacturer"] == "Artbox"
    assert "C:Game" not in out


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


# --- C:Game (added 2026-09-27 -- eBay's own item-specifics editor showed
# "Required -- Game" blank on a live listing; nothing in this pipeline had
# ever populated it) --

def test_a_pokemon_row_gets_the_pokemon_game_specific():
    out = convert_row(_row(game="pokemon"), _args())
    assert out["C:Game"] == "Pokémon TCG"


def test_a_one_piece_row_gets_the_one_piece_game_specific():
    out = convert_row(_row(game="one_piece"), _args())
    assert out["C:Game"] == "One Piece Card Game"


def test_a_dragon_ball_carddass_row_gets_no_game_specific():
    """Carddass/art cards aren't really "a game" in the sense this
    picklist means -- a guessed value risks being wrong, so it's left
    unset rather than guessed."""
    out = convert_row(_row(game="dragon_ball_carddass"), _args())
    assert "C:Game" not in out


def test_a_row_with_no_game_field_gets_the_pokemon_game_specific():
    out = convert_row(_row(), _args())
    assert out["C:Game"] == "Pokémon TCG"


# --- C:Finish (added 2026-09-27 -- real per-card printing data this app
# already tracks, e.g. via InventoryItem.variant, but never sent on) --

def test_a_single_cards_finish_is_sent_as_its_own_item_specific():
    out = convert_row(_row(variant="Reverse Holo"), _args())
    assert out["C:Finish"] == "Reverse Holo"


def test_a_lot_never_gets_a_finish_specific():
    """A lot mixes many cards' printings under one sku -- a single Finish
    value would misrepresent whichever ones aren't that exact printing."""
    out = convert_row(_row(title="Bulk lot (12 cards)", variant="Holo"), _args())
    assert "C:Finish" not in out


def test_the_variation_parent_never_inherits_a_childs_finish():
    """Otherwise the parent silently inherits child_rows[0]'s printing and
    misrepresents every other Normal/Reverse Holo card in the set -- the
    same class of bug 2026-09-26's language incident was about."""
    cards = [
        _row(sku="sku-4-holo", title="Card4 #4", set_total="99", variant="Holo"),
        _row(sku="sku-5-normal", title="Card5 #5", set_total="99", variant="Normal"),
    ]
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    assert rows[0]["C:Finish"] == ""


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


def test_a_card_name_over_65_chars_is_truncated_not_rejected():
    """Real upload failure (2026-09-26, ErrorCode 21919308): 'Card Name's
    value of "Majin Buu : Evil - FB04-091 (Dragon Ball Z Dokkan Battle
    Alternate Art)" is too long. Enter a value of no more than 65
    characters.' -- *Title was already capped at 80, Card Name never
    was."""
    long_name = "Majin Buu : Evil - FB04-091 (Dragon Ball Z Dokkan Battle Alternate Art) #FB04-091"
    out = convert_row(_row(title=long_name), _args())
    assert len(out["C:Card Name"]) <= 65
    assert out["C:Card Name"] == "Majin Buu : Evil - FB04-091 (Dragon Ball Z Dokkan Battle Alternat"


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


# --- *Description's actual body text (2026-09-27) -- until now this was
# just row["description"], which for most listings meant the title
# repeated verbatim: no set, no condition, no language, nothing this app
# had already computed for the item specifics sitting right next to it. --

def test_a_singles_description_mentions_its_set_and_a_condition_statement():
    out = convert_row(_row(title="Abra #54", set_name="Unified Minds"), _args())
    assert "Unified Minds" in out["*Description"]
    assert "Near Mint or Better" in out["*Description"]
    assert "Abra" in out["*Description"]


def test_a_singles_description_includes_rarity_and_finish_when_present():
    out = convert_row(_row(rarity="Ultra Rare", variant="Holo"), _args())
    assert "Ultra Rare" in out["*Description"]
    assert "Holo" in out["*Description"]


def test_a_japanese_singles_description_states_its_language():
    out = convert_row(_row(language="Japanese"), _args())
    assert "Language: Japanese" in out["*Description"]


def test_an_english_singles_description_does_not_bother_stating_the_language():
    out = convert_row(_row(language="English"), _args())
    assert "Language:" not in out["*Description"]


def test_a_lots_description_mentions_its_set_not_a_single_cards_specifics():
    out = convert_row(_row(title="Obsidian Flames — bulk lot (21 cards)", set_name="Obsidian Flames",
                          weight_grams="143"), _args())
    assert "Obsidian Flames" in out["*Description"]
    assert "Near Mint or Better" in out["*Description"]
    assert "C:Card Name" not in out  # unaffected -- still no per-card specifics on a lot


# --- C:Language -- added 2026-09-27 after a real sale of a Japanese-printed
# Dragon Ball card pictured and catalogued as the English release. Confirmed
# against the real downloaded template: C:Language is a recognised aspect
# on this category, with "Japanese" one of its own listed values. --------

def test_language_passes_through_to_c_language():
    out = convert_row(_row(language="Japanese"), _args())
    assert out["C:Language"] == "Japanese"


def test_a_row_with_no_language_field_defaults_to_english():
    """_row()'s own base dict predates this field, matching a real row
    from a builder that hasn't been updated for it (e.g. a lot, per
    export_ebay_bulk_listing.py's own comment on why it leaves lots
    blank) -- convert_row must not KeyError, and English is exactly as
    accurate a guess here as it was before the field existed."""
    row = _row()
    assert "language" not in row
    out = convert_row(row, _args())
    assert out["C:Language"] == "English"


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
    assert parent["RelationshipDetails"] == "MPN=04/99 - Card4;21/99 - Card21;63/99 - Card63"
    assert parent["*Quantity"] == ""
    assert parent["*StartPrice"] == ""
    assert parent["C:Card Name"] == ""
    assert parent["C:Card Number"] == ""


def test_each_child_row_is_marked_variation_with_its_own_value_and_price():
    """The picker includes both halves of the collector number and the
    card name; a bare number does not identify the card to a buyer."""
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, 21), _args())
    children = rows[1:]
    assert {c["RelationshipDetails"] for c in children} == {
        "MPN=04/99 - Card4", "MPN=21/99 - Card21",
    }
    for c in children:
        assert c["Relationship"] == "Variation"
    prices = {c["RelationshipDetails"]: c["*StartPrice"] for c in children}
    assert prices["MPN=04/99 - Card4"] == "4.00"
    assert prices["MPN=21/99 - Card21"] == "21.00"


def test_the_padding_matches_the_sets_own_digit_width():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, set_total="8"), _args())
    assert rows[1]["RelationshipDetails"] == "MPN=4/8 - Card4"


def test_the_parent_gets_a_pick_your_card_title_and_the_sets_own_category_id():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4), _args())
    parent = rows[0]
    assert parent["*Title"] == "Obsidian Flames - Complete Your Master Set"
    assert parent["*Category"] == SINGLE_CATEGORY_ID
    assert parent["CustomLabel"] == "set-variation-obsidian-flames"


def test_the_parent_description_states_the_card_count_and_a_condition_statement():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4, 21, 63), _args())
    assert "3 available" in rows[0]["*Description"]
    assert "Near Mint or Better" in rows[0]["*Description"]


def test_a_japanese_group_key_produces_an_honest_title_and_a_collision_free_slug():
    """build_variation_group takes whatever string export_ebay_bulk_listing.py's
    _set_group_key hands it as the group's identity -- this is the other
    half of the language-split fix: a set that exists in both languages
    already arrives here as two differently-keyed groups ("Cross Force" /
    "Cross Force (Japanese)"), and this function needs no knowledge of
    language at all for the right title and a non-colliding sku to fall
    out of that automatically."""
    rows, _ = build_variation_group("Cross Force (Japanese)", _cards(4), _args())
    parent = rows[0]
    assert parent["*Title"] == "Cross Force (Japanese) - Complete Your Master Set"
    assert parent["CustomLabel"] == "set-variation-cross-force-japanese"


def test_a_card_with_no_readable_number_is_skipped_not_a_broken_variation():
    cards = _cards(4) + [_row(sku="sku-x", title="No Number Card", price="2.00", set_name="Obsidian Flames")]
    rows, skipped = build_variation_group("Obsidian Flames", cards, _args())
    # only the one real card became a variation; the other contributed nothing
    assert len(rows) == 2
    assert rows[1]["RelationshipDetails"] == "MPN=04/99 - Card4"
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
    assert rows[1]["RelationshipDetails"] == "MPN=04/99 - Card4 - Reverse Holo"


def test_the_picker_spells_out_number_name_finish_and_rarity():
    """Regression guard for the mobile eBay picker: terse values such as
    `8 - Golduck (N)` made customers decode an internal abbreviation and
    omitted known card details."""
    cards = [_row(
        sku="golduck-promo", title="Golduck #8", set_total="",
        variant="Normal", rarity="Promo",
    )]
    rows, _ = build_variation_group("Mega Evolution Black Star Promos", cards, _args())
    assert rows[1]["RelationshipDetails"] == "MPN=8 - Golduck - Normal - Promo"


def test_an_mpn_value_over_65_chars_is_truncated_with_the_variant_suffix_kept():
    """Real upload failure (2026-09-26, ErrorCode 21919318): 'The
    variation detail MPN contains the value "EX25-39 - Kale & Caulifla,
    Becoming the Strongest in the Multiverse (N)", which has too many
    characters. Reduce it to 65 or fewer characters.' The suffix must
    survive truncation -- losing it would make this collide with the (H)
    printing's own value (see the next test)."""
    cards = [_row(sku="sku-39", title="Kale & Caulifla, Becoming the Strongest in the Multiverse #39",
                  number="39", set_total="45", variant="Normal")]
    rows, _ = build_variation_group("Expansion Deck Box Set 25", cards, _args())
    value = rows[1]["RelationshipDetails"].removeprefix("MPN=")
    assert len(value) <= 65
    assert value.endswith("- Normal")


def test_two_variants_of_an_overly_long_name_still_get_distinct_mpn_values():
    cards = [
        _row(sku="sku-39-n", title="Kale & Caulifla, Becoming the Strongest in the Multiverse #39",
             number="39", set_total="45", variant="Normal"),
        _row(sku="sku-39-h", title="Kale & Caulifla, Becoming the Strongest in the Multiverse #39",
             number="39", set_total="45", variant="Holo"),
    ]
    rows, skipped = build_variation_group("Expansion Deck Box Set 25", cards, _args())
    assert skipped == []
    children = rows[1:]
    values = {c["RelationshipDetails"] for c in children}
    assert len(values) == 2  # not collapsed to one truncated value


def test_javascript_token_inside_an_innocent_card_name_is_broken_for_ebay():
    cards = [_row(
        sku="energy-retrieval", title="Energy Retrieval #82", number="82",
        set_name="White Flare", set_total="86", variant="Reverse Holo",
    )]

    rows, skipped = build_variation_group("White Flare", cards, _args())

    assert skipped == []
    assert rows[1]["RelationshipDetails"] == "MPN=82/86 - Energy Retr. - Reverse Holo"
    assert "eval" not in rows[0]["RelationshipDetails"].casefold()


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
        "MPN=04/99 - Card4 - Normal", "MPN=04/99 - Card4 - Reverse Holo",
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


def test_cards_with_no_real_printed_number_are_not_treated_as_reprints_of_each_other():
    """Real bug found 2026-09-27: 12 real, distinct 1996 Artbox Dragon Ball
    Z cards all partition to "#n/a" (no printed number at all), which
    collided on the same dedup key and silently dropped 11 of the 12 --
    only the first one made it into a real relist CSV. Distinct card
    names with the placeholder "n/a" number must all survive."""
    cards = [
        _row(sku="sku-piccolo", title="Piccolo (Artbox '96) #n/a", set_total="", variant="Holo"),
        _row(sku="sku-trunks", title="Trunks #n/a", set_total="", variant="Holo"),
        _row(sku="sku-vegeta", title="Vegeta: Saiyan Saga (Artbox '96) #n/a", set_total="", variant="Holo"),
    ]
    rows, skipped = build_variation_group("Dragon Ball Z Artbox '96", cards, _args())
    assert skipped == []
    children = rows[1:]
    assert len(children) == 3
    assert len({c["RelationshipDetails"] for c in children}) == 3


def test_two_genuinely_identical_no_number_cards_are_still_deduped_by_name():
    """The fallback keys on name instead of the placeholder number, but a
    true duplicate (same name, same printing, no number) should still be
    caught the same way a real numbered reprint is."""
    cards = [
        _row(sku="sku-a", title="Trunks #n/a", set_total="", variant="Holo"),
        _row(sku="sku-b", title="Trunks #n/a", set_total="", variant="Holo"),
    ]
    rows, skipped = build_variation_group("Dragon Ball Z Artbox '96", cards, _args())
    assert len(rows) == 2  # 1 parent + only the first
    assert "sku-b" in skipped[0]


def test_the_parent_picture_is_the_sets_logo_not_a_pool():
    """Confirmed 2026-09-27 against eBay's own File Exchange docs (v3.5.2):
    the item row's PicURL is one representative photo, not a pool of
    every variation's picture -- an earlier fix that pooled them here was
    wrong (see this function's docstring)."""
    cards = _cards(4, 21)
    cards[0]["set_logo_url"] = "https://example.test/obsidian-flames-logo.png"
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    assert rows[0]["PicURL"] == "https://example.test/obsidian-flames-logo.png"


def test_the_parent_picture_falls_back_to_a_cards_photo_with_no_set_logo():
    rows, _ = build_variation_group("Obsidian Flames", _cards(4), _args())
    assert rows[0]["PicURL"] == "https://example.test/abra.png"


def test_a_childs_picurl_is_keyed_by_its_own_mpn_value():
    """The real, documented mechanism (File Exchange Advanced Template
    Instructions v3.5.2, "Using relationship fields for variations",
    pp.24-25): a variation row's PicURL must be "<its own trait
    value>=<url>", the exact same value used in that row's own
    RelationshipDetails -- a bare URL here is what a buyer's variation
    picker has nothing to match, and the card's own photo never displays
    once selected (the real cause of "the photos don't come up",
    reported 2026-09-27)."""
    cards = _cards(4, 21, set_total="99")
    cards[0]["image_url"] = "https://example.test/card-4.png"
    cards[1]["image_url"] = "https://example.test/card-21.png"
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    children = rows[1:]
    assert {c["PicURL"] for c in children} == {
        "04/99 - Card4=https://example.test/card-4.png",
        "21/99 - Card21=https://example.test/card-21.png",
    }
    for child in children:
        mpn_value = child["RelationshipDetails"].removeprefix("MPN=")
        assert child["PicURL"].startswith(f"{mpn_value}=")


def test_two_variations_photos_never_collide_since_every_mpn_value_is_unique():
    """"PicURL values for variations of a given item must use a single
    trait name" (same doc) -- satisfied here since MPN is the only trait
    this listing ever uses, and every child's own MPN value is already
    guaranteed unique by the dedup guard above, so no two children ever
    need to share one photo entry."""
    cards = _cards(4, 21, 63, set_total="99")
    for i, card in enumerate(cards):
        card["image_url"] = f"https://example.test/card-{i}.png"
    rows, _ = build_variation_group("Obsidian Flames", cards, _args())
    children = rows[1:]
    keys = [c["PicURL"].split("=", 1)[0] for c in children]
    assert len(keys) == len(set(keys))
