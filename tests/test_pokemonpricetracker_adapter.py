from unittest.mock import patch

import httpx
import pytest

from automation_control.adapters.pokemonpricetracker import (
    PokemonPriceTrackerLookupError, PokemonPriceTrackerRateLimited, search_cards,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_cards_sends_bearer_token_and_search_param():
    def handler(request: httpx.Request):
        assert request.headers.get("authorization") == "Bearer fake-key"
        params = httpx.QueryParams(request.url.query)
        assert params.get("search") == "Charizard"
        assert request.url.path == "/api/v2/cards"
        return httpx.Response(200, json={"data": [{"name": "Charizard"}]})

    result = search_cards("fake-key", search="Charizard", client=_client(handler))
    assert result == [{"name": "Charizard"}]


def test_search_cards_always_sends_a_limit():
    """Confirmed against a real call: billing is per card *requested*
    (the limit itself), not per card returned -- an unlimited search bills
    for a server-side default of 50 regardless of match count. Forgetting
    this turns one lookup into up to 50x its real cost."""
    def handler(request: httpx.Request):
        assert httpx.QueryParams(request.url.query).get("limit") == "5"
        return httpx.Response(200, json={"data": []})

    search_cards("fake-key", search="q", client=_client(handler))


def test_search_cards_accepts_a_smaller_explicit_limit():
    def handler(request: httpx.Request):
        assert httpx.QueryParams(request.url.query).get("limit") == "1"
        return httpx.Response(200, json={"data": []})

    search_cards("fake-key", search="q", limit=1, client=_client(handler))


def test_search_cards_combines_search_and_set_params():
    def handler(request: httpx.Request):
        params = httpx.QueryParams(request.url.query)
        assert params.get("search") == "Charizard"
        assert params.get("set") == "celebrations"
        return httpx.Response(200, json={"data": []})

    search_cards("fake-key", search="Charizard", set_name="celebrations", client=_client(handler))


def test_search_cards_by_tcgplayer_id_only():
    def handler(request: httpx.Request):
        params = httpx.QueryParams(request.url.query)
        assert params.get("tcgPlayerId") == "490294"
        assert "search" not in params
        return httpx.Response(200, json={"data": [{"name": "Charizard"}]})

    search_cards("fake-key", tcgplayer_id="490294", client=_client(handler))


def test_search_cards_omits_include_history_by_default():
    def handler(request: httpx.Request):
        assert "includeHistory" not in httpx.QueryParams(request.url.query)
        return httpx.Response(200, json={"data": []})

    search_cards("fake-key", search="q", client=_client(handler))


def test_search_cards_sets_include_history_when_requested():
    def handler(request: httpx.Request):
        assert httpx.QueryParams(request.url.query).get("includeHistory") == "true"
        return httpx.Response(200, json={"data": []})

    search_cards("fake-key", search="q", include_history=True, client=_client(handler))


def test_search_cards_returns_empty_list_for_no_matches():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"data": []})

    assert search_cards("fake-key", search="nonexistent card xyz", client=_client(handler)) == []


def test_search_cards_retries_on_429_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [{"name": "Pikachu"}]})

    with patch("automation_control.adapters.pokemonpricetracker.time.sleep"):
        result = search_cards("fake-key", search="Pikachu", client=_client(handler))
    assert result == [{"name": "Pikachu"}]
    assert len(attempts) == 2


def test_search_cards_stops_immediately_on_a_long_retry_after():
    """The real failure this guards against: a daily-credit-exhaustion 429
    carries a ~23-hour retry-after. Retrying it (or, worse, a caller loop
    treating it as "skip this one, try the next") cannot possibly succeed
    and is exactly what got a real API key temporarily blocked for
    hammering it with 50+ guaranteed-to-fail requests in under 5 minutes.
    """
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return httpx.Response(429, headers={"retry-after": "82757"})

    with pytest.raises(PokemonPriceTrackerRateLimited) as exc_info:
        search_cards("fake-key", search="q", client=_client(handler))
    assert exc_info.value.retry_after_seconds == 82757
    assert len(attempts) == 1  # no retry -- retrying this cannot succeed


def test_search_cards_still_retries_a_429_with_a_short_retry_after():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"data": [{"name": "Pikachu"}]})

    with patch("automation_control.adapters.pokemonpricetracker.time.sleep"):
        result = search_cards("fake-key", search="Pikachu", client=_client(handler))
    assert result == [{"name": "Pikachu"}]
    assert len(attempts) == 2


def test_search_cards_raises_immediately_on_4xx():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return httpx.Response(401, text="invalid token")

    with pytest.raises(PokemonPriceTrackerLookupError):
        search_cards("bad-key", search="q", client=_client(handler))
    assert len(attempts) == 1


def test_search_cards_raises_after_exhausting_retries():
    def handler(request: httpx.Request):
        return httpx.Response(503)

    with patch("automation_control.adapters.pokemonpricetracker.time.sleep"), pytest.raises(PokemonPriceTrackerLookupError):
        search_cards("fake-key", search="q", client=_client(handler))
