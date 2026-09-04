from unittest.mock import patch

import httpx
import pytest

from automation_control.adapters.pricecharting import PriceChartingLookupError, get_product, search_products


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_products_sends_query_and_key():
    def handler(request: httpx.Request):
        params = httpx.QueryParams(request.url.query)
        assert params.get("q") == "Charizard Base Set"
        assert params.get("t") == "fake-key"
        assert request.url.path == "/api/products"
        return httpx.Response(200, json={"products": [{"id": "abc", "product-name": "Charizard #4"}]})

    result = search_products("Charizard Base Set", "fake-key", client=_client(handler))
    assert result == [{"id": "abc", "product-name": "Charizard #4"}]


def test_search_products_returns_empty_list_for_no_matches():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"status": "success", "products": []})

    assert search_products("nonexistent card xyz", "fake-key", client=_client(handler)) == []


def test_search_products_retries_on_429_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"products": [{"id": "abc"}]})

    with patch("automation_control.adapters.pricecharting.time.sleep"):
        result = search_products("q", "fake-key", client=_client(handler))
    assert result == [{"id": "abc"}]
    assert len(attempts) == 2


def test_search_products_raises_immediately_on_4xx():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return httpx.Response(401, text="invalid token")

    with pytest.raises(PriceChartingLookupError):
        search_products("q", "bad-key", client=_client(handler))
    assert len(attempts) == 1


def test_search_products_raises_after_exhausting_retries():
    def handler(request: httpx.Request):
        return httpx.Response(503)

    with patch("automation_control.adapters.pricecharting.time.sleep"), pytest.raises(PriceChartingLookupError):
        search_products("q", "fake-key", client=_client(handler))


def test_get_product_fetches_by_id():
    def handler(request: httpx.Request):
        params = httpx.QueryParams(request.url.query)
        assert params.get("id") == "abc123"
        assert request.url.path == "/api/product"
        return httpx.Response(200, json={"id": "abc123", "product-name": "Charizard #4", "loose-price": 225500})

    result = get_product("abc123", "fake-key", client=_client(handler))
    assert result == {"id": "abc123", "product-name": "Charizard #4", "loose-price": 225500}


def test_get_product_returns_none_for_an_unknown_id():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"status": "error", "error-message": "product not found"})

    assert get_product("does-not-exist", "fake-key", client=_client(handler)) is None
