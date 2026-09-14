"""The per-brand vocabulary: Sonny Angel's master list and Pop Mart's
figure lists, and reading a name off Google Lens matches.

The figures in here come from Nic's real master list export and Pop Mart's
own set page, so a re-export that changes either shape fails these rather
than silently narrowing the vocabulary.
"""

import pytest
from pathlib import Path

from automation_control.vocabulary import (
    CHARACTER,
    GIFTS,
    HIPPERS,
    LIMITED,
    POP_MART,
    REGULAR,
    SERIES,
    SONNY_ANGEL,
    all_entries,
    character_names,
    entries_for,
    match,
    series_names,
    suggest,
)


def all_series():
    """The Sonny Angel half, which is what most of this file is about."""
    return tuple(e for e in entries_for(SONNY_ANGEL) if e.kind == SERIES)


def match_series(text):
    return match(text, SONNY_ANGEL)


def suggest_series(titles):
    return suggest(titles, SONNY_ANGEL)


# --- parsing the sheet -----------------------------------------------------

def test_every_column_of_the_master_list_is_parsed():
    """Three parallel columns of differing lengths, under a heading row
    whose last cell is a paragraph aimed at humans."""
    by_category = {}
    for series in all_series():
        by_category.setdefault(series.category, []).append(series.name)

    assert len(by_category[REGULAR]) == 10
    assert len(by_category[LIMITED]) == 46
    assert len(by_category[HIPPERS]) == 6
    assert len(by_category[GIFTS]) == 3
    assert len(all_series()) == 65


def test_snack_series_is_present_where_the_old_hardcoded_list_missed_it():
    """The 11-name list in the seed script had nine of the ten regular
    series -- this is the one it didn't know about."""
    assert "Snack Series" in series_names(SONNY_ANGEL)


def test_hippers_are_their_own_category_not_regular_series():
    """The seed script called these "Hippers Animal Series" and filed them
    as regular. The sheet puts them in their own column, and the line is
    actually named the other way round."""
    hippers = {s.name for s in all_series() if s.category == HIPPERS}
    assert "Animal Hippers" in hippers
    assert "Harvest Hippers" in hippers
    assert "Hippers Animal Series" not in series_names(SONNY_ANGEL)


def test_gifts_are_split_out_from_hippers():
    """Same column, different kind of product -- a gift prices nothing like
    a hipper, so it shouldn't inherit its category."""
    gifts = {s.name for s in all_series() if s.category == GIFTS}
    assert gifts == {"Flower Gift", "Birthday Gift Cake", "Birthday Gift Bear"}


def test_the_bare_gifts_caption_is_not_itself_a_series():
    """It's a heading inside the column, above the three named gifts."""
    assert "Gifts" not in series_names(SONNY_ANGEL)


def test_year_prefixes_become_data_and_leave_the_name():
    """A name is about to become a DB value and an eBay query, so
    "(LATEST - 2026)" must not travel with it -- least of all the word
    LATEST, which is wrong the moment the next series ships."""
    by_name = {s.name: s for s in all_series()}

    assert by_name["Summer Vacation"].year == 2026
    assert by_name["Pumpkin Patch"].year == 2025
    assert by_name["Seoul Series"].year == 2017
    assert not any("LATEST" in name for name in series_names(SONNY_ANGEL))
    assert not any(name.startswith("(") for name in series_names(SONNY_ANGEL))

    # Two entries carry no year in the sheet; absent beats invented.
    assert by_name["Okinawa Series"].year is None


# --- matching a series out of free text ------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Sonny Angel Marine Series Dolphin", "Marine Series"),
    ("Sonny Angel Animal Series 4 secret", "Animal Series 4"),
    ("sonny angel summer vacation blind box", "Summer Vacation"),
    ("Sonny Angel Birthday Gift Bear", "Birthday Gift Bear"),
    # Punctuation differs from the sheet's "Cherry Blossom: Hanami Edition".
    ("Sonny Angel Cherry Blossom Hanami Edition", "Cherry Blossom: Hanami Edition"),
])
def test_match_series_reads_the_series_out_of_a_shop_title(title, expected):
    found = match_series(title)
    assert found is not None and found.name == expected


def test_fast_food_series_resolves_to_the_canonical_snack_series():
    """Confirmed by Nic: the catalogue's Fast Food rows are Snack Series
    figures. Sellers use the Fast Food name, so matching canonical names
    alone missed every title for the Hamburger secret -- the most valuable
    thing in the catalogue, and the pricer's own worked example."""
    for title in ("Sonny Angel Fast Food Series Hamburger secret",
                  "Sonny Angel Hamburger / Fast Food Series",
                  "sonny angel fast food burger"):
        found = match_series(title)
        assert found is not None and found.name == "Snack Series"


def test_the_apps_own_legacy_hipper_names_resolve_to_the_real_line():
    """The seed script invented "Hippers Animal Series" for what the sheet
    calls "Animal Hippers". Matching is word-order sensitive, so without an
    alias the bare "Hippers" needle wins and an Animal Hipper is stored as
    a plain one -- a wrong series, which gets priced, not a blank one."""
    assert match_series("Hippers Animal Series").name == "Animal Hippers"
    assert match_series("Hippers Harvest Series").name == "Harvest Hippers"
    # The correctly-ordered name still wins over the bare line.
    assert match_series("Sonny Angel Animal Hippers Bear").name == "Animal Hippers"
    assert match_series("Sonny Angel Hippers Strawberry").name == "Hippers"


def test_an_alias_never_becomes_the_stored_name():
    """The whole point is converging on one name per series, so an alias
    resolves to the canonical one and is not itself offered."""
    assert "Fast Food Series" not in series_names(SONNY_ANGEL)
    assert "Fast Food" not in series_names(SONNY_ANGEL)
    assert len(series_names(SONNY_ANGEL)) == 65


def test_every_alias_points_at_a_real_series():
    """A typo in the alias map would silently stop matching rather than
    fail, so this is the guard."""
    from automation_control.vocabulary import _needles

    _needles.cache_clear()
    assert _needles(None)  # raises if an alias names an unknown entry


def test_match_series_prefers_the_most_specific_name():
    """"Cherry Blossom Hippers" and "Cherry Blossom Series" both appear in
    the sheet, and "Animal Hippers" overlaps the bare "Hippers" line."""
    assert match_series("Sonny Angel Cherry Blossom Hippers").name == "Cherry Blossom Hippers"
    assert match_series("Sonny Angel Animal Hippers Bear").name == "Animal Hippers"
    assert match_series("Sonny Angel Hippers Strawberry").name == "Hippers"


def test_match_series_refuses_to_guess_which_animal_series():
    """There are four. A title naming none of them must stay blank: the
    series feeds the eBay query, so a wrong one prices the figure against
    the wrong comparables -- worse than a field the human fills in."""
    assert match_series("Sonny Angel Animal Series figure, loose") is None


def test_match_series_does_not_match_a_longer_number():
    assert match_series("Sonny Angel Animal Series 10 fantasy") is None


@pytest.mark.parametrize("text", ["", "random unrelated listing", "Smiski Hiding Series"])
def test_match_series_finds_nothing_in_unrelated_text(text):
    assert match_series(text) is None


# --- consensus across a whole Lens run -------------------------------------

def test_suggest_series_ranks_by_how_many_matches_agree():
    """One listing naming a series is a guess; six agreeing is the answer.
    These counts are the real numbers from the Lens run on capture
    fd0da650 -- a Flower Series figure, correctly identified."""
    titles = [
        "Sonny Angel Fruit Series Peach",
        "Sonny Angel Fruit Series Figures, Blind Box",
        "Sonny Angel Fruit Series secret",
        "Sonny Angel Marine Series Dolphin",
        "dreaming elephant hipper",
        "Sonny Angel Handmade Outfit Sets - Blues - Etsy",
    ]
    ranked = suggest_series(titles)

    assert ranked[0][0].name == "Fruit Series"
    assert ranked[0][1] == 3
    assert ranked[1][1] == 1  # Marine Series, the lone dissenter
    # Titles naming no series are simply absent rather than counted.
    assert sum(count for _, count in ranked) == 4


def test_suggest_series_is_empty_when_nothing_names_a_series():
    assert suggest_series(["a post", "another post", ""]) == []


# --- Pop Mart, and keeping the two brands apart ----------------------------

def test_pop_mart_figures_are_matched_by_character_not_just_series():
    """A Pop Mart set page names every figure and marks its chases, so
    there's no reason to stop at the series the way Sonny Angel does."""
    found = match("POP MART Twinkle Twinkle Create Your Taste Baguette", POP_MART)
    assert found.name == "Baguette"
    assert found.kind == CHARACTER
    assert found.series == "Twinkle Twinkle Create Your Taste Series"
    assert found.is_secret is False


def test_a_character_beats_the_series_it_belongs_to():
    """Naming the figure tells you its series as well, so it is strictly
    the better answer -- length must not decide this."""
    found = match("Twinkle Twinkle Chili Sauce super secret", POP_MART)
    assert found.name == "Chili Sauce"
    assert found.kind == CHARACTER


def test_the_series_still_matches_when_no_figure_is_named():
    found = match("popmart twinkle twinkle blind box set", POP_MART)
    assert found.kind == SERIES
    assert found.name == "Twinkle Twinkle Create Your Taste Series"


def test_all_three_twinkle_twinkle_chases_are_flagged_secret():
    tt = {e.name for e in entries_for(POP_MART)
          if e.is_secret and e.series == "Twinkle Twinkle Create Your Taste Series"}
    assert tt == {"Lemon", "Milk", "Chili Sauce"}
    assert len([e for e in entries_for(POP_MART)
                if e.kind == CHARACTER and e.series == "Twinkle Twinkle Create Your Taste Series"]) == 12


def test_crybaby_series_are_loaded_with_their_chases():
    """Read off Pop Mart's own product pages. Crying Again has three
    secrets, Crying For Love a secret and a super secret."""
    again = {e.name for e in entries_for(POP_MART)
             if e.is_secret and e.series == "CRYBABY Crying Again Series"}
    love = {e.name for e in entries_for(POP_MART)
            if e.is_secret and e.series == "CRYBABY Crying For Love Series"}
    assert again == {"The Queen of Broken Heart", "She's Alice Halloween Ver.", "The Robber Red Ver."}
    assert love == {"Sparkling Love", "Be Mine"}


def test_the_two_captures_that_had_no_consensus_now_resolve():
    """Both came back blank because CRYBABY wasn't in the vocabulary --
    Lens had named them all along. Titles here are real ones from those
    cached runs."""
    robber = match("Crybaby Crying Again Series The Robber - TOYSEZ", POP_MART)
    assert robber.name == "The Robber"
    assert robber.series == "CRYBABY Crying Again Series"

    cherry = match("finally, love you cherry much #crybaby #cryingforlove", POP_MART)
    assert cherry.name == "Love You Cherry Much"
    assert cherry.series == "CRYBABY Crying For Love Series"


def test_a_variant_beats_the_figure_it_varies():
    """"The Robber Red Ver." is a separate secret from "The Robber", and
    it prices as one -- longest-match inside the character kind keeps
    them apart."""
    assert match("Crybaby The Robber Red Ver.", POP_MART).is_secret is True
    assert match("Crybaby The Robber", POP_MART).is_secret is False


def test_listings_that_drop_the_crybaby_prefix_still_match():
    """Sellers routinely write just "Crying Again Series"."""
    assert match("popmart crying again series blind box", POP_MART).name == "CRYBABY Crying Again Series"


def test_mustard_resolves_by_brand_and_never_across_it():
    """Sonny Angel's Snack Series has a Mustard and so does Twinkle
    Twinkle. Unscoped, this name is a coin toss between two companies'
    figures -- and the wrong one gets priced."""
    assert match("Mustard", POP_MART).brand == POP_MART
    # Sonny Angel's vocabulary is series-level, so its Mustard isn't a name
    # this can match at all -- which is the point: it must not fall through
    # to Pop Mart's.
    assert match("Mustard", SONNY_ANGEL) is None


def test_a_brand_only_sees_its_own_aliases():
    """Fast Food is a Sonny Angel alias; it must not resolve for Pop Mart."""
    assert match("Fast Food Series", SONNY_ANGEL).name == "Snack Series"
    assert match("Fast Food Series", POP_MART) is None


def test_suggest_is_brand_scoped_too():
    titles = ["Sonny Angel Flower Series Lily Bell", "Twinkle Twinkle Baguette"]
    assert [e.name for e, _ in suggest(titles, SONNY_ANGEL)] == ["Lily Bell"]
    assert [e.name for e, _ in suggest(titles, POP_MART)] == ["Baguette"]


def test_both_brands_load():
    brands = {e.brand for e in all_entries()}
    assert brands == {SONNY_ANGEL, POP_MART}


# --- Sonny Angel figures, arriving one series at a time --------------------

def test_sonny_angel_summer_vacation_figures_are_loaded():
    """Cross-checked against a second source before landing: 8 types, 6
    regular and 2 secret."""
    figures = [e for e in entries_for(SONNY_ANGEL)
               if e.kind == CHARACTER and e.series == "Summer Vacation"]
    assert {e.name for e in figures if not e.is_secret} == {
        "Rabbit", "Manatee", "Octopus", "Pineapple", "Shell", "Shark"}
    assert {e.name for e in figures if e.is_secret} == {"Unicorn", "Flamingo Robby Angel"}


def test_the_edition_name_resolves_to_the_master_list_series():
    """The sheet says "SUMMER VACATION"; everyone selling it says
    "Sun-Kissed Style", which is how the catalogue ended up with
    "Summer Vacation Series - Sun-Kissed Style"."""
    found = match("Sonny Angel Summer Vacation Sun-Kissed Style Octopus", SONNY_ANGEL)
    assert found.name == "Octopus"
    assert found.series == "Summer Vacation"


def test_a_character_never_overrides_a_different_series_named_in_the_title():
    """Figure lists arrive one series at a time, so a generic name exists
    in series not loaded yet. Matching the one Rabbit we happen to know
    would report Summer Vacation for an Animal Series figure -- worse than
    reporting only the series the title actually named."""
    found = match("Sonny Angel Animal Series 4 Rabbit figure", SONNY_ANGEL)
    assert found.kind == SERIES
    assert found.name == "Animal Series 4"


def test_the_character_still_wins_within_its_own_series():
    """The guard must not cost us the Pop Mart behaviour it was added
    around."""
    assert match("Twinkle Twinkle Chili Sauce super secret", POP_MART).name == "Chili Sauce"
    assert match("Crybaby Crying Again Series The Robber", POP_MART).name == "The Robber"
    assert match("Sonny Angel Sun Kissed Style Shark", SONNY_ANGEL).name == "Shark"


def test_a_figure_filed_under_an_unknown_series_is_rejected_outright():
    """A typo here would match while reporting a series that can never be
    picked from the datalist, so it fails loudly at load instead."""
    import automation_control.vocabulary as vocab

    original = vocab.SONNY_ANGEL_FIGURES_PATH
    bad = Path(__file__).parent / "_bad_figures.csv"
    bad.write_text("series,character,is_secret,secret_label\nNo Such Series,Ghost,no,\n")
    vocab.SONNY_ANGEL_FIGURES_PATH = bad
    vocab.all_entries.cache_clear()
    vocab._needles.cache_clear()
    try:
        with pytest.raises(ValueError, match="not in the master list"):
            vocab.all_entries()
    finally:
        vocab.SONNY_ANGEL_FIGURES_PATH = original
        bad.unlink()
        vocab.all_entries.cache_clear()
        vocab._needles.cache_clear()


def test_flower_series_figures_turn_a_series_guess_into_a_figure():
    """The real cached Lens run on capture fd0da650 could only manage a
    weak "Flower Series 5" while the vocabulary stopped at the series. The
    same titles now name the figure."""
    found = match("Sonny Angel Flower Series Lily Bell Figure- US Selller", SONNY_ANGEL)
    assert found.name == "Lily Bell"
    assert found.series == "Flower Series"
    assert found.kind == CHARACTER


def test_a_figure_name_that_is_also_a_series_name_does_not_hijack_it():
    """Cactus is a Flower Series figure and Cactus Series is a limited
    series of its own; Cherry Blossom is both a figure and a series."""
    assert match("Sonny Angel Cactus Series 2020", SONNY_ANGEL).name == "Cactus Series"
    assert match("Sonny Angel Flower Series Cactus", SONNY_ANGEL).name == "Cactus"
    assert match("Sonny Angel Cherry Blossom Series", SONNY_ANGEL).name == "Cherry Blossom Series"


def test_the_plural_spelling_of_cherry_blossom_still_matches():
    """Retailers split on it, and matching is exact on word boundaries."""
    found = match("Sonny Angel Flower Series Cherry Blossoms", SONNY_ANGEL)
    assert found.name == "Cherry Blossom"
    assert found.series == "Flower Series"


# --- Cat Life Series --------------------------------------------------------

def test_cat_life_figures_are_loaded_with_two_rarity_tiers():
    """Two independent sources (sonnyangel.com's own release post and
    Toysez) agreed on the same 14: 9 base patterns, 3 Lucky, 2 Secret."""
    cats = [e for e in entries_for(SONNY_ANGEL) if e.series == "Cat Life"]
    assert len(cats) == 14
    base = {e.name for e in cats if not e.is_secret}
    assert base == {"Calico", "Silver Tabby", "Tuxedo", "Siamese", "White",
                    "Brown & Black", "Bluish Gray", "Red Tabby", "Black"}
    secrets = {e.name for e in cats if e.is_secret}
    assert secrets == {"Lucky Orange", "Lucky Pink", "Lucky Purple",
                       "Happy Pink", "Happy Pink Robby Angel"}


def test_cat_life_series_alias_resolves_to_the_master_lists_short_name():
    """The master list's raw export shortens this to "Cat Life"; every
    retailer, and the brand's own product page, calls it "Cat Life
    Series"."""
    found = match("Sonny Angel Cat Life Series Tuxedo", SONNY_ANGEL)
    assert found.name == "Tuxedo"
    assert found.series == "Cat Life"


def test_a_lucky_figure_is_flagged_secret_same_as_a_true_secret():
    """The brand distinguishes "Lucky" from "Secret" as separate rarity
    tiers, but this vocabulary only has one is_secret flag -- both are
    rare pulls that price like a secret, so both set it."""
    lucky = match("Sonny Angel Cat Life Series Lucky Orange", SONNY_ANGEL)
    secret = match("Sonny Angel Cat Life Happy Pink Robby Angel", SONNY_ANGEL)
    assert lucky.is_secret is True
    assert secret.is_secret is True
