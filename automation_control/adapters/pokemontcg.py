import re
import time
from decimal import Decimal

import httpx

BASE_URL = "https://api.pokemontcg.io/v2/cards"
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.5
MAX_RETRY_DELAY_SECONDS = 10.0

_SET_CODE_SUFFIX = re.compile(r"^(.*\S)\s*\([A-Za-z0-9]{2,5}\)\s*$")
_UPPERCASE_EX_GX_SUFFIX = re.compile(r"^(.+) (EX|GX)$")


class PriceLookupError(Exception):
    """The lookup itself failed (network error, non-2xx after retries)."""


class PriceNotFound(Exception):
    """No single confident price match exists -- zero results after trying
    every search variant, or a set of candidates that disagree on price.
    Callers should fall back to manual entry rather than guess.
    """


def _search(query: str, api_key: str | None, client: httpx.Client) -> list[dict]:
    headers = {"X-Api-Key": api_key} if api_key else {}

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        delay = RETRY_DELAY_SECONDS * (attempt + 1)
        try:
            response = client.get(BASE_URL, params={"q": query}, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            last_error = exc
        else:
            if response.status_code < 400:
                return response.json().get("data", [])
            # 429 (rate limited, common on the free/no-key tier) is retryable
            # just like a 5xx; any other 4xx means the request itself is bad
            # and retrying won't help.
            if response.status_code != 429 and response.status_code < 500:
                raise PriceLookupError(f"pokemontcg.io returned {response.status_code}: {response.text[:200]}")
            last_error = PriceLookupError(f"pokemontcg.io returned {response.status_code}")
            retry_after = response.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = min(float(retry_after), MAX_RETRY_DELAY_SECONDS)

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(delay)

    raise PriceLookupError(f"pokemontcg.io unavailable after {MAX_ATTEMPTS} attempts: {last_error}")


def _best_variant_price(prices_by_variant: dict) -> Decimal | None:
    for variant_prices in prices_by_variant.values():
        for key in ("market", "mid", "low"):
            value = variant_prices.get(key)
            if value:
                return Decimal(str(value))
    return None


def _card_number_matches(card: dict, card_number: str | None) -> bool:
    if not card_number:
        return True
    # our AI-extracted numbers are sometimes "167", sometimes "125/165";
    # pokemontcg.io's `number` field is typically just the leading digits.
    ours = card_number.split("/")[0].strip().lstrip("0") or "0"
    theirs = str(card.get("number", "")).split("/")[0].strip().lstrip("0") or "0"
    return ours == theirs


def _strip_set_code_suffix(set_name: str) -> str | None:
    """"Stellar Crown (SCR)" -> "Stellar Crown". The AI sometimes appends
    the set's short code in parentheses (visible on the card itself), which
    pokemontcg.io's set.name field never includes -- an exact-phrase search
    on the untouched string finds nothing. Returns None if there's no such
    suffix to strip (nothing gained by trying the same query twice).
    """
    match = _SET_CODE_SUFFIX.match(set_name)
    return match.group(1) if match else None


def _is_promo_set_name(set_name: str | None) -> bool:
    return bool(set_name) and "promo" in set_name.lower()


def _hyphenate_ex_gx_suffix(character: str) -> str | None:
    """"Terrakion EX" -> "Terrakion-EX". pokemontcg.io stores the old-style
    (pre-2020ish) uppercase EX/GX suffix with a hyphen even though that's
    not how it's printed on the card or how our AI reads it off -- an exact
    phrase search on the space-separated form finds nothing. Modern
    lowercase "ex" and other suffixes (V, VMAX, VSTAR) don't get this
    treatment. Returns None if the name doesn't end in EX/GX.
    """
    match = _UPPERCASE_EX_GX_SUFFIX.match(character)
    return f"{match.group(1)}-{match.group(2)}" if match else None


def _try_search(character: str, set_name: str | None, card_number: str | None, api_key: str | None, client: httpx.Client, require_promo: bool = False) -> Decimal | None:
    """One search attempt. Returns a price if there's a single confident
    match, or None if nothing usable came back (caller should try a
    broader query). Raises PriceNotFound if results disagree on price --
    broadening the search further would only make that worse, so this is
    a hard stop rather than something to fall through past.
    """
    query = f'name:"{character}"' + (f' set.name:"{set_name}"' if set_name else "")
    results = _search(query, api_key, client)

    if require_promo:
        # a name-only + number-only match is prone to colliding with an
        # unrelated card from a completely different, non-promo set that
        # happens to reuse the same short number (promo numbers like "211"
        # or "025" aren't unique across the whole card database). If our own
        # set name says this is a promo, only accept candidates that are
        # also promos.
        results = [card for card in results if _is_promo_set_name((card.get("set") or {}).get("name"))]

    narrowed = [card for card in results if _card_number_matches(card, card_number)]
    if narrowed:
        results = narrowed

    priced: list[tuple[dict, Decimal]] = []
    for card in results:
        price = _best_variant_price((card.get("tcgplayer") or {}).get("prices") or {})
        if price is not None:
            priced.append((card, price))

    if not priced:
        return None

    distinct_prices = {price for _, price in priced}
    if len(distinct_prices) > 1:
        raise PriceNotFound(f"ambiguous match for {character!r} (query={query!r} #{card_number!r}, promo_filter={require_promo}): {len(priced)} candidates with different prices")

    return priced[0][1]


def lookup_price(character: str, set_name: str, card_number: str | None, api_key: str | None = None, client: httpx.Client | None = None) -> Decimal:
    """Look up a card's current market price via pokemontcg.io (free, no key
    required for basic use; TCGPlayer-sourced USD pricing). Tries, in order:
    the exact set name; a version with any "(SET CODE)" suffix stripped;
    (if the set name says this is a promo) a name-only search restricted to
    other promo sets, to avoid colliding with an unrelated card that reuses
    the same short promo number; an unrestricted name-only search; and
    finally, if the name ends in old-style uppercase EX/GX, the same
    name-only search with that suffix hyphenated. Raises PriceNotFound
    rather than guessing when nothing comes back confident, at any stage.
    """
    owns_client = client is None
    client = client or httpx.Client()

    strategies: list[tuple[str, str | None, bool]] = []
    if set_name:
        strategies.append((character, set_name, False))
        stripped = _strip_set_code_suffix(set_name)
        if stripped:
            strategies.append((character, stripped, False))
    if _is_promo_set_name(set_name):
        strategies.append((character, None, True))
    strategies.append((character, None, False))

    hyphenated = _hyphenate_ex_gx_suffix(character)
    if hyphenated:
        strategies.append((hyphenated, None, False))

    try:
        for name_query, set_query, require_promo in strategies:
            price = _try_search(name_query, set_query, card_number, api_key, client, require_promo=require_promo)
            if price is not None:
                return price
    finally:
        if owns_client:
            client.close()

    raise PriceNotFound(f"no priced match found for {character!r} ({set_name!r} #{card_number!r}) after {len(strategies)} search variant(s)")
