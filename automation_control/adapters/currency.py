import time
from decimal import ROUND_HALF_UP, Decimal

import httpx

BASE_URL = "https://api.frankfurter.dev/v1/latest"
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


class CurrencyLookupError(Exception):
    """Could not get a current exchange rate. Callers should not fall back
    to a guessed or stale rate -- retry later instead.
    """


def get_usd_to_aud_rate(client: httpx.Client | None = None) -> Decimal:
    """Current USD->AUD rate from frankfurter.dev (European Central Bank
    reference rates, free, no API key)."""
    owns_client = client is None
    client = client or httpx.Client()
    last_error: Exception | None = None
    try:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.get(BASE_URL, params={"base": "USD", "symbols": "AUD"}, timeout=10.0)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    rate = (response.json().get("rates") or {}).get("AUD")
                    if rate:
                        return Decimal(str(rate))
                    last_error = CurrencyLookupError("response was missing an AUD rate")
                elif response.status_code < 500:
                    raise CurrencyLookupError(f"frankfurter.dev returned {response.status_code}: {response.text[:200]}")
                else:
                    last_error = CurrencyLookupError(f"frankfurter.dev returned {response.status_code}")

            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))
    finally:
        if owns_client:
            client.close()

    raise CurrencyLookupError(f"frankfurter.dev unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


def usd_to_aud(usd_amount: Decimal, rate: Decimal) -> Decimal:
    return (usd_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
