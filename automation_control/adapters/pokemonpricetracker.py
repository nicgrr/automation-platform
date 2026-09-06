"""pokemonpricetracker.com's card-lookup API.

Aggregates TCGPlayer market pricing and eBay sold-comp data (graded and
ungraded) per specific printing, refreshed daily -- see
scan_ingest/pricing.py for why that matters: pokemontcg.io's own
TCGPlayer/Cardmarket snapshot has been flagged unreliable for actual
listing decisions. Free tier: 100 credits/day, ~1 credit per card looked up.
"""

import threading
import time

import httpx

BASE_URL = "https://www.pokemonpricetracker.com/api/v2"
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5

# The free/Pro tier caps at 60 requests/minute; live scanning has no notion
# of that; it fires one lookup per newly-seen card as sheets come in, with
# nothing to stop several arriving within the same second on a fast batch.
# Confirmed the hard way (2026-09-07): that alone -- not the backfill job,
# which already stops outright on a real block -- fired 50+ 429s in under
# 5 minutes across a night of scanning and got the API key blocked three
# times. This paces every call through this module, live scan or backfill
# alike, comfortably under the cap rather than trusting callers to.
MIN_SECONDS_BETWEEN_REQUESTS = 1.5

_pacing_lock = threading.Lock()
_last_request_at = 0.0
# Set the moment a real block is seen; every call anywhere in the process
# fails fast against this until it passes, instead of the live-scan path
# silently swallowing the 429 and re-triggering the same block on the very
# next card -- the actual mechanism behind three separate blocks in one
# night.
_blocked_until = 0.0


def _wait_for_pacing_slot() -> None:
    global _last_request_at
    with _pacing_lock:
        wait = _last_request_at + MIN_SECONDS_BETWEEN_REQUESTS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


class PokemonPriceTrackerLookupError(Exception):
    """The lookup itself failed (network error, non-2xx after retries, or a
    response that didn't parse as JSON). Distinct from a clean empty
    result, which just means nothing matched."""


class PokemonPriceTrackerRateLimited(PokemonPriceTrackerLookupError):
    """A 429 that isn't a brief per-minute limit -- confirmed against a
    real response: exhausting the daily credit quota returns 429 with
    `retry-after` set to the seconds until tomorrow's reset (~23 hours),
    not a short retryable window. Retrying that within this call cannot
    possibly succeed, and a caller loop that treats it as "no match, try
    the next one" will hit the exact same 429 on every remaining item --
    confirmed the hard way: a backfill run that kept doing that fired 50+
    429s in under 5 minutes and got the API key itself temporarily blocked
    for it. A caller iterating multiple lookups (see
    scripts/backfill_pokemonpricetracker_prices.py) must catch this
    specifically and stop entirely, not just skip the one item.
    """

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"pokemonpricetracker.com credits exhausted -- retry after {retry_after_seconds}s")


# A 429 asking to wait longer than this is treated as "not a brief rate
# limit" (see PokemonPriceTrackerRateLimited) rather than retried -- picked
# well above any plausible per-minute-window wait, comfortably below the
# ~23-hour wait a real daily-exhaustion response carries.
HARD_LIMIT_RETRY_AFTER_THRESHOLD_SECONDS = 300


def _get(path: str, params: dict, api_key: str, client: httpx.Client) -> dict:
    global _blocked_until
    remaining = _blocked_until - time.monotonic()
    if remaining > 0:
        # Already blocked from an earlier call in this process -- fail fast
        # without touching the network. This is what actually stops a busy
        # scanning session from re-triggering the same block on every next
        # card: without it, each new card's lookup would independently hit
        # the same 429 and get silently swallowed by `get_price`, which
        # counts against the block exactly like a fresh offense would.
        raise PokemonPriceTrackerRateLimited(int(remaining))

    headers = {"Authorization": f"Bearer {api_key}"}
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        _wait_for_pacing_slot()
        try:
            response = client.get(f"{BASE_URL}/{path}", params=params, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise PokemonPriceTrackerLookupError(f"pokemonpricetracker.com returned unparseable JSON: {exc}") from exc
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit() and int(retry_after) > HARD_LIMIT_RETRY_AFTER_THRESHOLD_SECONDS:
                    # Not retryable within this call or any nearby one --
                    # raise immediately, no sleep, no further attempts.
                    _blocked_until = time.monotonic() + int(retry_after)
                    raise PokemonPriceTrackerRateLimited(int(retry_after))
                last_error = PokemonPriceTrackerLookupError("pokemonpricetracker.com returned 429")
            elif response.status_code < 500:
                # Any other 4xx means the request itself is bad (e.g. an
                # invalid key) and retrying won't help.
                raise PokemonPriceTrackerLookupError(f"pokemonpricetracker.com returned {response.status_code}: {response.text[:200]}")
            else:
                last_error = PokemonPriceTrackerLookupError(f"pokemonpricetracker.com returned {response.status_code}")

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

    raise PokemonPriceTrackerLookupError(f"pokemonpricetracker.com unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


# Confirmed against a real call: billing is per card *requested* (the
# `limit` value itself), not per card actually returned -- a bare, unlimited
# search defaults server-side to requesting 50 and is billed for all 50
# regardless of how many results come back. `limit` is therefore mandatory
# here, not an optional tuning knob: forgetting it turns one lookup into up
# to 50x its real cost. Small enough to leave room to disambiguate against
# CatalogCard's own number/rarity without burning through the free tier's
# 100 credits/day on a single search.
DEFAULT_LIMIT = 5


def search_cards(
    api_key: str, *, search: str | None = None, set_name: str | None = None,
    tcgplayer_id: str | None = None, limit: int = DEFAULT_LIMIT, include_history: bool = False,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Cards matching any combination of a free-text name search, a set
    name, or an exact tcgplayer_id. Returns an empty list for "nothing
    matched", never raises for that case -- only for a genuine lookup
    failure. `include_history` costs an extra credit per card; leave it off
    for a plain current-price lookup.
    """
    params = {k: v for k, v in {"search": search, "set": set_name, "tcgPlayerId": tcgplayer_id}.items() if v}
    params["limit"] = limit
    if include_history:
        params["includeHistory"] = "true"
    owns_client = client is None
    client = client or httpx.Client()
    try:
        payload = _get("cards", params, api_key, client)
    finally:
        if owns_client:
            client.close()
    return payload.get("data", [])
