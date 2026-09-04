"""pricecharting.com's product-search API.

Unlike pokemontcg.io/TCGPlayer, PriceCharting doesn't split one card into
variant sub-fields (normal/reverseHolofoil/holofoil) on a single record --
its catalogue treats each specific printing as its own product, the same way
it treats each specific video game release as its own product. Matching a
card to the right product is therefore a search-and-pick problem, not a
"read this one field" problem; see scan_ingest/pricing.py for how the result
gets matched against a CatalogCard's specific printing.
"""

import time

import httpx

BASE_URL = "https://www.pricecharting.com/api"
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5


class PriceChartingLookupError(Exception):
    """The lookup itself failed (network error, non-2xx after retries, or a
    response that didn't parse as JSON). Distinct from a clean empty
    result, which just means nothing matched the query."""


def _get(path: str, params: dict, api_key: str, client: httpx.Client) -> dict:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.get(f"{BASE_URL}/{path}", params={**params, "t": api_key}, timeout=15.0)
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise PriceChartingLookupError(f"pricecharting.com returned unparseable JSON: {exc}") from exc
            # 429 (rate limited) is retryable like a 5xx; any other 4xx
            # means the request itself is bad (e.g. an invalid key) and
            # retrying won't help.
            if response.status_code != 429 and response.status_code < 500:
                raise PriceChartingLookupError(f"pricecharting.com returned {response.status_code}: {response.text[:200]}")
            last_error = PriceChartingLookupError(f"pricecharting.com returned {response.status_code}")

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

    raise PriceChartingLookupError(f"pricecharting.com unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


def search_products(query: str, api_key: str, client: httpx.Client | None = None) -> list[dict]:
    """Full-text search across PriceCharting's whole catalogue (all
    categories, not just trading cards) -- callers narrow by checking each
    result's own fields (e.g. console-name / product-name) rather than the
    query alone, the same way `adapters/pokemontcg.py` re-checks its own
    search results instead of trusting the query to have been precise
    enough. Returns an empty list for "nothing matched", never raises for
    that case -- only for a genuine lookup failure.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        payload = _get("products", {"q": query}, api_key, client)
    finally:
        if owns_client:
            client.close()
    return payload.get("products", [])


def get_product(product_id: str, api_key: str, client: httpx.Client | None = None) -> dict | None:
    """One product's full record by its PriceCharting id (as returned in a
    search result's own "id" field). Returns None if the id doesn't exist;
    raises only for a genuine lookup failure.
    """
    owns_client = client is None
    client = client or httpx.Client()
    try:
        payload = _get("product", {"id": product_id}, api_key, client)
    finally:
        if owns_client:
            client.close()
    return payload if payload.get("status") != "error" else None
