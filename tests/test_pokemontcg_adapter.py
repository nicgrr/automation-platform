from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from automation_control.adapters.pokemontcg import PriceLookupError, PriceNotFound, _hyphenate_ex_gx_suffix, _strip_set_code_suffix, lookup_price


def _card(number="167", market=12.5, mid=None, id_="set1-167"):
    prices = {}
    if market is not None or mid is not None:
        variant = {}
        if market is not None:
            variant["market"] = market
        if mid is not None:
            variant["mid"] = mid
        prices["holofoil"] = variant
    return {"id": id_, "number": number, "tcgplayer": {"prices": prices}}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_lookup_price_returns_market_price_for_single_match():
    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        assert "Flareon" in query and "Scarlet & Violet Black Star Promos" in query
        return httpx.Response(200, json={"data": [_card(number="167", market=15.75)]})

    price = lookup_price("Flareon", "Scarlet & Violet Black Star Promos", "167", client=_client(handler))
    assert price == Decimal("15.75")


def test_lookup_price_falls_back_to_mid_when_market_missing():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": [_card(market=None, mid=9.99)]})

    price = lookup_price("Flareon", "Promo", "167", client=_client(handler))
    assert price == Decimal("9.99")


def test_lookup_price_narrows_by_card_number_when_multiple_results():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": [_card(number="167", market=10), _card(number="168", market=99)]})

    price = lookup_price("Eeveelution", "Promo", "167", client=_client(handler))
    assert price == Decimal("10")


def test_lookup_price_matches_number_ignoring_total_suffix():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": [_card(number="125", market=5)]})

    price = lookup_price("Electabuzz", "Scarlet & Violet—151", "125/165", client=_client(handler))
    assert price == Decimal("5")


def test_lookup_price_raises_when_no_results():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": []})

    with pytest.raises(PriceNotFound):
        lookup_price("Nonexistent Card", "Some Set", None, client=_client(handler))


def test_lookup_price_raises_when_no_result_has_pricing():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": [_card(market=None, mid=None)]})

    with pytest.raises(PriceNotFound):
        lookup_price("Flareon", "Promo", "167", client=_client(handler))


def test_lookup_price_raises_when_candidates_disagree_on_price():
    def handler(request: httpx.Request):
        # two different cards both match the (loose, no-number) query with genuinely different prices
        return httpx.Response(200, json={"data": [_card(id_="a", number="1", market=5), _card(id_="b", number="2", market=50)]})

    with pytest.raises(PriceNotFound):
        lookup_price("Common Name", "Some Set", None, client=_client(handler))


def test_lookup_price_does_not_raise_when_candidates_agree_on_price():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": [_card(id_="a", number="1", market=5), _card(id_="b", number="1", market=5)]})

    price = lookup_price("Common Name", "Some Set", None, client=_client(handler))
    assert price == Decimal("5")


def test_lookup_price_retries_on_5xx_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": [_card(market=7)]})

    with patch("automation_control.adapters.pokemontcg.time.sleep"):
        price = lookup_price("Flareon", "Promo", "167", client=_client(handler))
    assert price == Decimal("7")
    assert len(attempts) == 3


def test_lookup_price_raises_lookup_error_after_exhausting_retries():
    def handler(request: httpx.Request):
        return httpx.Response(502)

    with patch("automation_control.adapters.pokemontcg.time.sleep"), pytest.raises(PriceLookupError):
        lookup_price("Flareon", "Promo", "167", client=_client(handler))


def test_lookup_price_raises_immediately_on_4xx_no_retry():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return httpx.Response(400)

    with pytest.raises(PriceLookupError):
        lookup_price("Flareon", "Promo", "167", client=_client(handler))
    assert len(attempts) == 1


def test_lookup_price_retries_on_429_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [_card(market=7)]})

    with patch("automation_control.adapters.pokemontcg.time.sleep"):
        price = lookup_price("Flareon", "Promo", "167", client=_client(handler))
    assert price == Decimal("7")
    assert len(attempts) == 2


def test_lookup_price_honors_retry_after_header_on_429():
    def handler(request: httpx.Request):
        return httpx.Response(429, headers={"retry-after": "3"})

    with patch("automation_control.adapters.pokemontcg.time.sleep") as mock_sleep:
        with pytest.raises(PriceLookupError):
            lookup_price("Flareon", "Promo", "167", client=_client(handler))
    mock_sleep.assert_any_call(3.0)


def test_lookup_price_caps_retry_after_at_max_delay():
    def handler(request: httpx.Request):
        return httpx.Response(429, headers={"retry-after": "9999"})

    with patch("automation_control.adapters.pokemontcg.time.sleep") as mock_sleep:
        with pytest.raises(PriceLookupError):
            lookup_price("Flareon", "Promo", "167", client=_client(handler))
    mock_sleep.assert_any_call(10.0)


# --- fallback search variants ---

def test_lookup_price_falls_back_to_set_name_with_code_suffix_stripped():
    queries = []

    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        queries.append(query)
        if "Stellar Crown (SCR)" in query:
            return httpx.Response(200, json={"data": []})
        assert "Stellar Crown" in query
        return httpx.Response(200, json={"data": [_card(market=20)]})

    price = lookup_price("Medicham ex", "Stellar Crown (SCR)", "161", client=_client(handler))
    assert price == Decimal("20")
    assert len(queries) == 2


def test_lookup_price_falls_back_to_name_only_when_set_name_never_matches():
    queries = []

    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        queries.append(query)
        if "set.name" in query:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [_card(number="217", market=3)]})

    price = lookup_price("Totodile", "ASC", "041/217", client=_client(handler))
    assert price == Decimal("3")
    assert len(queries) == 2
    assert 'set.name' not in queries[-1]


def test_lookup_price_stops_at_first_variant_that_finds_an_ambiguous_match():
    # the full set-name query finds genuinely conflicting candidates --
    # falling through to a broader (name-only) search would only make the
    # ambiguity worse, so this should raise rather than try again.
    queries = []

    def handler(request: httpx.Request):
        queries.append(httpx.QueryParams(request.url.query).get("q", ""))
        return httpx.Response(200, json={"data": [_card(id_="a", number="1", market=5), _card(id_="b", number="2", market=50)]})

    with pytest.raises(PriceNotFound):
        lookup_price("Common Name", "Some Set (ABC)", None, client=_client(handler))
    assert len(queries) == 1


def test_lookup_price_raises_after_exhausting_all_variants():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": []})

    with pytest.raises(PriceNotFound):
        lookup_price("Nonexistent Card", "Some Set (ABC)", None, client=_client(handler))


# --- promo-aware disambiguation ---

def test_lookup_price_filters_to_promo_sets_when_name_only_fallback_is_ambiguous():
    # regression case: Gothitelle #211 in an unmatched promo set name falls
    # through to a name-only search, which finds both the correct promo
    # printing AND an unrelated card from a totally different (non-promo)
    # set that happens to reuse the number "211". Without the promo filter
    # this would raise as ambiguous; with it, only the promo candidate
    # remains.
    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        if "set.name" in query:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [
            {"id": "promo", "number": "211", "set": {"name": "Scarlet & Violet Black Star Promos"}, "tcgplayer": {"prices": {"holofoil": {"market": 3.5}}}},
            {"id": "unrelated", "number": "211", "set": {"name": "Some Regular Expansion"}, "tcgplayer": {"prices": {"holofoil": {"market": 99.0}}}},
        ]})

    price = lookup_price("Gothitelle", "Scarlet & Violet Black Star Promos (SVP)", "211", client=_client(handler))
    assert price == Decimal("3.5")


def test_lookup_price_falls_through_to_unfiltered_search_when_no_promo_candidates_have_pricing():
    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        if "set.name" in query:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [
            {"id": "promo-unpriced", "number": "211", "set": {"name": "Some Promos"}, "tcgplayer": {"prices": {}}},
            {"id": "regular", "number": "211", "set": {"name": "Some Regular Expansion"}, "tcgplayer": {"prices": {"holofoil": {"market": 8.0}}}},
        ]})

    price = lookup_price("Gothitelle", "Scarlet & Violet Black Star Promos (SVP)", "211", client=_client(handler))
    assert price == Decimal("8.0")


def test_lookup_price_skips_promo_filter_for_non_promo_set_names():
    queries = []

    def handler(request: httpx.Request):
        queries.append(httpx.QueryParams(request.url.query).get("q", ""))
        return httpx.Response(200, json={"data": []})

    with pytest.raises(PriceNotFound):
        lookup_price("Nonexistent", "Stellar Crown", "1", client=_client(handler))
    # exact set name, then name-only -- no promo-filtered variant in between
    assert len(queries) == 2


# --- EX/GX hyphenation fallback ---

def test_lookup_price_falls_back_to_hyphenated_ex_suffix():
    # regression case: pokemontcg.io stores old-style uppercase EX/GX cards
    # with a hyphen ("Terrakion-EX") even though the AI reads it off the
    # card as "Terrakion EX" (space) -- an exact-phrase search on the
    # space-separated form finds nothing at all.
    queries = []

    def handler(request: httpx.Request):
        query = httpx.QueryParams(request.url.query).get("q", "")
        queries.append(query)
        if "Terrakion-EX" in query:
            return httpx.Response(200, json={"data": [_card(number="71", market=12.0)]})
        return httpx.Response(200, json={"data": []})

    price = lookup_price("Terrakion EX", "Dragons Exalted", "71/124", client=_client(handler))
    assert price == Decimal("12.0")
    assert any("Terrakion-EX" in q for q in queries)


def test_lookup_price_does_not_try_hyphenation_for_lowercase_ex():
    queries = []

    def handler(request: httpx.Request):
        queries.append(httpx.QueryParams(request.url.query).get("q", ""))
        return httpx.Response(200, json={"data": []})

    with pytest.raises(PriceNotFound):
        lookup_price("Zapdos ex", "Some Set", "1", client=_client(handler))
    assert all("Zapdos-ex" not in q for q in queries)


def test_strip_set_code_suffix_removes_trailing_code():
    assert _strip_set_code_suffix("Stellar Crown (SCR)") == "Stellar Crown"
    assert _strip_set_code_suffix("Paldean Fates (PAF)") == "Paldean Fates"


def test_strip_set_code_suffix_returns_none_when_no_suffix():
    assert _strip_set_code_suffix("ASC") is None
    assert _strip_set_code_suffix("Stellar Crown") is None


def test_hyphenate_ex_gx_suffix():
    assert _hyphenate_ex_gx_suffix("Terrakion EX") == "Terrakion-EX"
    assert _hyphenate_ex_gx_suffix("Vaporeon GX") == "Vaporeon-GX"


def test_hyphenate_ex_gx_suffix_returns_none_for_non_matching_names():
    assert _hyphenate_ex_gx_suffix("Zapdos ex") is None  # lowercase, not old-style
    assert _hyphenate_ex_gx_suffix("Vaporeon VMAX") is None
    assert _hyphenate_ex_gx_suffix("Flareon") is None
