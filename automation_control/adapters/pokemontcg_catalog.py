"""pokemontcg.io v2 catalog client -- set and card *reference* data.

Deliberately separate from `pokemontcg.py`, which owns ad-hoc price lookups
for the AI-capture flow. This module answers "what cards exist in set X, and
what do they look like", which the scan-ingest pipeline caches once per set
and then never re-fetches (see scan_ingest/catalog.py).

The API is measurably flaky -- bursts of 500/502 with empty bodies are
common, and a run of 3 failures before a success is normal. Every request
therefore retries with escalating backoff, and treats an unparseable body as
just another retryable failure (a 500 returns an empty body, so
`response.json()` raises rather than returning an error payload). Because
callers cache the result, a transient outage only ever delays the first
scan of a given set.
"""

import time
from pathlib import Path

import httpx

BASE_URL = "https://api.pokemontcg.io/v2"
MAX_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 1.5
MAX_RETRY_DELAY_SECONDS = 15.0
PAGE_SIZE = 250
REQUEST_TIMEOUT_SECONDS = 60.0


class CatalogLookupError(Exception):
    """The catalog request failed after exhausting retries."""


def _headers(api_key: str | None) -> dict[str, str]:
    return {"X-Api-Key": api_key} if api_key else {}


def _get_json(path: str, params: dict, api_key: str | None, client: httpx.Client) -> dict:
    last_error: Exception | str | None = None
    for attempt in range(MAX_ATTEMPTS):
        delay = min(RETRY_DELAY_SECONDS * (attempt + 1), MAX_RETRY_DELAY_SECONDS)
        try:
            response = client.get(f"{BASE_URL}{path}", params=params, headers=_headers(api_key), timeout=REQUEST_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            # 429 (rate limited on the keyless tier) and 5xx are transient;
            # any other 4xx means the request itself is wrong and retrying
            # can't fix it.
            if response.status_code != 429 and 400 <= response.status_code < 500:
                raise CatalogLookupError(f"pokemontcg.io returned {response.status_code}: {response.text[:200]}")
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    # A 200 with an unparseable body does happen here; treat
                    # it like any other transient failure rather than
                    # crashing the caller mid-cache.
                    last_error = f"unparseable response body: {exc}"
            else:
                last_error = f"pokemontcg.io returned {response.status_code}"
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    delay = min(float(retry_after), MAX_RETRY_DELAY_SECONDS)

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(delay)

    raise CatalogLookupError(f"pokemontcg.io unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


def _paginate(path: str, query: str | None, api_key: str | None, client: httpx.Client) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        params: dict = {"page": page, "pageSize": PAGE_SIZE}
        if query:
            params["q"] = query
        payload = _get_json(path, params, api_key, client)
        results.extend(payload.get("data", []))
        total = payload.get("totalCount")
        if total is None or len(results) >= total or not payload.get("data"):
            return results
        page += 1


def list_sets(api_key: str | None = None, client: httpx.Client | None = None) -> list[dict]:
    """Every set in the catalog, oldest first -- the picklist the operator
    chooses from at the start of a scan session."""
    owns_client = client is None
    client = client or httpx.Client()
    try:
        sets = _paginate("/sets", None, api_key, client)
    finally:
        if owns_client:
            client.close()
    return sorted(sets, key=lambda s: (s.get("releaseDate") or "", s.get("id") or ""))


def get_set(set_id: str, api_key: str | None = None, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        payload = _get_json(f"/sets/{set_id}", {}, api_key, client)
    finally:
        if owns_client:
            client.close()
    data = payload.get("data")
    if not data:
        raise CatalogLookupError(f"no such set: {set_id!r}")
    return data


def get_set_cards(set_id: str, api_key: str | None = None, client: httpx.Client | None = None) -> list[dict]:
    """Every card in one set. This is the per-session bulk fetch that gets
    cached locally, so it should run at most once per set."""
    owns_client = client is None
    client = client or httpx.Client()
    try:
        cards = _paginate("/cards", f"set.id:{set_id}", api_key, client)
    finally:
        if owns_client:
            client.close()
    if not cards:
        raise CatalogLookupError(f"no cards found for set {set_id!r}")
    return cards


def download_image(url: str, dest_path: Path, client: httpx.Client | None = None) -> Path:
    """Fetch one reference card image to disk. Retries on the same terms as
    the JSON endpoints -- images come from a different host
    (images.pokemontcg.io) but are no more reliable.
    """
    owns_client = client is None
    client = client or httpx.Client()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        last_error: Exception | str | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.get(url, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400 and response.content:
                    dest_path.write_bytes(response.content)
                    return dest_path
                if response.status_code != 429 and 400 <= response.status_code < 500:
                    raise CatalogLookupError(f"image fetch returned {response.status_code} for {url}")
                last_error = f"image fetch returned {response.status_code}"
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(min(RETRY_DELAY_SECONDS * (attempt + 1), MAX_RETRY_DELAY_SECONDS))
        raise CatalogLookupError(f"image unavailable after {MAX_ATTEMPTS} attempts ({url}): {last_error}")
    finally:
        if owns_client:
            client.close()
