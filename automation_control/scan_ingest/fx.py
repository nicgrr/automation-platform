"""Currency conversion to AUD for display.

Prices arrive from pokemontcg.io in USD (TCGPlayer) and EUR (Cardmarket).
`CardPrice` rows deliberately keep the source currency -- an observation
should record what the source actually said, and a rate baked in at write
time would silently rot. Conversion happens here, at display time.

Rates come from frankfurter.app (European Central Bank reference rates, no
API key, no account). They're cached for a day: FX moves far less than the
error bar on pokemontcg.io's own pricing, which the operator has already
flagged as rough. If the fetch fails the cached rate is reused, and failing
that `FALLBACK_RATES` -- a stale rate is far better here than a blank value
or a crash, because this whole number is an estimate either way.
"""

from __future__ import annotations

import threading
import time

import httpx

BASE = "AUD"
ENDPOINT = "https://api.frankfurter.app/latest"
TTL_SECONDS = 24 * 3600
TIMEOUT = 5

# Only used if the network has never succeeded. Deliberately round numbers --
# they are a floor on usefulness, not a claim of accuracy, and anything shown
# from them is labelled as approximate like every other price here.
FALLBACK_RATES = {"USD": 1.50, "EUR": 1.65, "AUD": 1.0}

_lock = threading.Lock()
_cache: dict[str, float] = {}
_fetched_at: float = 0.0
# Whether the rates currently in use came from the network. Exposed so the
# UI can say "approximate" honestly rather than presenting a hardcoded
# fallback as though it were a real rate.
_is_live: bool = False


def is_live() -> bool:
    return _is_live


def _fetch_rates() -> dict[str, float]:
    """AUD per unit of each supported currency."""
    # follow_redirects: the endpoint 301s (Cloudflare), and httpx does not
    # follow redirects by default -- without this every fetch "succeeds"
    # with a 301 HTML body and silently falls back to stale rates.
    response = httpx.get(
        ENDPOINT, params={"from": BASE, "to": "USD,EUR"},
        timeout=TIMEOUT, follow_redirects=True,
    )
    response.raise_for_status()
    rates = (response.json() or {}).get("rates") or {}
    # The API returns foreign-per-AUD; we want AUD-per-foreign.
    out = {"AUD": 1.0}
    for code, value in rates.items():
        if value:
            out[code] = 1.0 / float(value)
    if len(out) < 2:
        raise ValueError("no usable rates in response")
    return out


def rates(force: bool = False) -> dict[str, float]:
    """Current AUD-per-unit rates, cached for `TTL_SECONDS`."""
    global _fetched_at, _is_live
    with _lock:
        fresh = _cache and (time.time() - _fetched_at) < TTL_SECONDS
        if fresh and not force:
            return dict(_cache)
        try:
            _cache.update(_fetch_rates())
            _fetched_at = time.time()
            _is_live = True
        except Exception:
            if not _cache:
                _is_live = False
                return dict(FALLBACK_RATES)
        return dict(_cache) if _cache else dict(FALLBACK_RATES)


def to_aud(amount, currency: str) -> float | None:
    """Convert to AUD, or None if the amount isn't usable.

    An unknown currency returns None rather than guessing a rate -- showing
    a wrong number confidently is worse than showing none.
    """
    if amount is None:
        return None
    code = (currency or "").upper()
    rate = rates().get(code)
    if rate is None:
        return None
    return float(amount) * rate
