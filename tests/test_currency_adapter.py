from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from automation_control.adapters.currency import CurrencyLookupError, get_usd_to_aud_rate, usd_to_aud


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_usd_to_aud_rate_returns_decimal():
    def handler(request: httpx.Request):
        assert httpx.QueryParams(request.url.query).get("base") == "USD"
        assert httpx.QueryParams(request.url.query).get("symbols") == "AUD"
        return httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2026-08-14", "rates": {"AUD": 1.412}})

    rate = get_usd_to_aud_rate(client=_client(handler))
    assert rate == Decimal("1.412")


def test_get_usd_to_aud_rate_retries_on_5xx_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) < 2:
            return httpx.Response(502)
        return httpx.Response(200, json={"rates": {"AUD": 1.4}})

    with patch("automation_control.adapters.currency.time.sleep"):
        rate = get_usd_to_aud_rate(client=_client(handler))
    assert rate == Decimal("1.4")
    assert len(attempts) == 2


def test_get_usd_to_aud_rate_raises_after_exhausting_retries():
    def handler(request: httpx.Request):
        return httpx.Response(503)

    with patch("automation_control.adapters.currency.time.sleep"), pytest.raises(CurrencyLookupError):
        get_usd_to_aud_rate(client=_client(handler))


def test_get_usd_to_aud_rate_raises_immediately_on_4xx():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return httpx.Response(400)

    with pytest.raises(CurrencyLookupError):
        get_usd_to_aud_rate(client=_client(handler))
    assert len(attempts) == 1


def test_get_usd_to_aud_rate_raises_when_aud_missing_from_response():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"rates": {}})

    with patch("automation_control.adapters.currency.time.sleep"), pytest.raises(CurrencyLookupError):
        get_usd_to_aud_rate(client=_client(handler))


def test_usd_to_aud_converts_and_rounds_to_cents():
    assert usd_to_aud(Decimal("0.77"), Decimal("1.412")) == Decimal("1.09")
    assert usd_to_aud(Decimal("10.00"), Decimal("1.5")) == Decimal("15.00")


def test_usd_to_aud_rounds_half_up():
    assert usd_to_aud(Decimal("1.005"), Decimal("1.0")) == Decimal("1.01")
