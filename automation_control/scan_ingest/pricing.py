"""Pricing behind a swappable interface.

The user has flagged pokemontcg.io's pricing as unreliable, twice, based on
real experience pricing captured cards (see automation_control/price_review.py
and classification.py's price checks). `PokemonTcgPriceSource` exists anyway
as a rough, zero-extra-cost placeholder -- it reads TCGPlayer/Cardmarket
figures that already rode along in the same response Stage 2's `cache_set`
fetches for the card list, so showing *something* in the session summary
costs nothing further. It is explicitly not trusted as authoritative.

The point of `PriceSource` is that this can be swapped for a dedicated
pricing API (or eBay sold comps, the user's stated preference once one
exists) as a new class implementing the same one method, with zero changes
to identify.py, commit.py, or session.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

import httpx

from ..models import CardPrice, CardVariant, CatalogCard
from ..adapters.pokemonpricetracker import PokemonPriceTrackerLookupError, PokemonPriceTrackerRateLimited, search_cards

# TCGPlayer's own variant keys, keyed by our CardVariant -- these are the
# printings TCGPlayer actually distinguishes; a set without a holo print of
# a given card just won't have a "holofoil" key, handled as "no quote".
_TCGPLAYER_VARIANT_KEYS: dict[CardVariant, tuple[str, ...]] = {
    CardVariant.NORMAL: ("normal", "unlimited"),
    CardVariant.REVERSE_HOLO: ("reverseHolofoil",),
    CardVariant.HOLO: ("holofoil", "1stEditionHolofoil"),
}

# Cardmarket doesn't split cleanly by printing the way TCGPlayer does; only
# reverse holo gets dedicated fields. Used as a fallback when TCGPlayer has
# no quote at all, in EUR rather than USD.
_CARDMARKET_FIELDS: dict[CardVariant, tuple[str, ...]] = {
    CardVariant.NORMAL: ("trendPrice", "averageSellPrice", "lowPrice"),
    CardVariant.REVERSE_HOLO: ("reverseHoloTrend", "reverseHoloSell", "reverseHoloLow"),
    CardVariant.HOLO: ("trendPrice", "averageSellPrice", "lowPrice"),  # no dedicated holo fields; same rough fallback as normal
}


@dataclass
class PriceQuote:
    price: Decimal
    currency: str
    source: str


class PriceSource(ABC):
    """One method, deliberately: everything above this line is what
    identify/commit/session need to know about pricing at all."""

    @abstractmethod
    def get_price(self, card: CatalogCard, variant: CardVariant) -> PriceQuote | None:
        """A price estimate for one card in one printing, or None if this
        source has nothing for it. Never raises for "no data" -- only for
        a genuine implementation error."""


class PokemonTcgPriceSource(PriceSource):
    """Reads pricing already captured at cache time (see catalog.py) --
    makes no network calls itself. Explicitly a rough placeholder: this is
    the same pokemontcg.io data the user has found unreliable for actual
    listing decisions elsewhere in EzBay. Good enough to sanity-check a pile
    while scanning; not good enough to price a listing from.
    """

    def get_price(self, card: CatalogCard, variant: CardVariant) -> PriceQuote | None:
        if not card.raw_prices:
            return None

        tcgplayer = card.raw_prices.get("tcgplayer") or {}
        quote = self._from_tcgplayer(tcgplayer.get("prices") or {}, variant)
        if quote:
            return quote

        cardmarket = card.raw_prices.get("cardmarket") or {}
        return self._from_cardmarket(cardmarket.get("prices") or {}, variant)

    def _from_tcgplayer(self, prices_by_variant: dict, variant: CardVariant) -> PriceQuote | None:
        for key in _TCGPLAYER_VARIANT_KEYS.get(variant, ()):
            variant_prices = prices_by_variant.get(key)
            if not variant_prices:
                continue
            for field in ("market", "mid", "low"):
                value = variant_prices.get(field)
                if value:
                    return PriceQuote(price=Decimal(str(value)), currency="USD", source=f"pokemontcg_tcgplayer_{field}")
        return None

    def _from_cardmarket(self, prices: dict, variant: CardVariant) -> PriceQuote | None:
        for field in _CARDMARKET_FIELDS.get(variant, ()):
            value = prices.get(field)
            if value:
                return PriceQuote(price=Decimal(str(value)), currency="EUR", source=f"pokemontcg_cardmarket_{field}")
        return None


# pokemonpricetracker.com's printing names, keyed by our CardVariant --
# confirmed against real API responses (see the class docstring below),
# unlike TCGPlayer's variant keys above which came from the same cached
# catalogue response Stage 2 already fetches.
_POKEMONPRICETRACKER_PRINTING_BY_VARIANT: dict[CardVariant, str] = {
    CardVariant.NORMAL: "Normal",
    CardVariant.REVERSE_HOLO: "Reverse Holofoil",
    CardVariant.HOLO: "Holofoil",
}

# Shared with CachedPriceSource so the two never drift apart -- the cache
# has to match on the exact same string PriceQuote.source carries.
POKEMONPRICETRACKER_SOURCE_NAME = "pokemonpricetracker_market"


def _find_pokemonpricetracker_product(api_key: str, card: CatalogCard, client: httpx.Client | None = None) -> dict | None:
    """The one search result matching this card's specific printing, or
    None if nothing matched. Raises `PokemonPriceTrackerLookupError` (or
    its `PokemonPriceTrackerRateLimited` subclass) straight through on a
    genuine lookup failure -- callers decide for themselves whether that
    means "skip this one card" or "stop the whole run" (see
    `PokemonPriceTrackerRateLimited`'s own docstring for why those are not
    the same thing). Shared by `PokemonPriceTrackerSource` (one variant at
    a time) and `fetch_all_pokemonpricetracker_variants` (every variant a
    card has, in the one search this already paid for) so neither has to
    re-search for data the other already fetched.
    """
    # A name alone is nowhere near enough to narrow this: confirmed against
    # a real search for a Scarlet & Violet base-set common ("Wattrel") that
    # the same Pokemon has been reprinted often enough that three *other*
    # sets' printings outranked ours within the top 3 results, so our card
    # never appeared at all. The set name (via the card_set relationship,
    # not the internal set_id slug) narrows the search itself rather than
    # trying to out-guess its ranking.
    set_name = card.card_set.name if card.card_set else None
    # limit=3: enough slack to find the right printing among a few
    # same-named reprints within that one set without paying for results
    # we'll discard -- billed per card requested, not per card returned.
    results = search_cards(api_key, search=card.name, set_name=set_name, limit=3, client=client)

    # Numbers are authoritative the same way they are everywhere else in
    # this codebase (OCR, the vision fallback): two cards in the same set
    # never share one, where a name search can return several printings of
    # cards that merely share a name.
    our_number = (card.number or "").split("/")[0].lstrip("0") or "0"
    return next(
        (r for r in results if str(r.get("cardNumber", "")).split("/")[0].lstrip("0") == our_number),
        None,
    )


def fetch_all_pokemonpricetracker_variants(
    api_key: str, card: CatalogCard, client: httpx.Client | None = None,
) -> dict[CardVariant, PriceQuote]:
    """Every printing pokemonpricetracker.com has data for on this card, in
    one search -- for batch/backfill use, where a card with several held
    variants (e.g. both Normal and Reverse Holofoil copies in inventory)
    would otherwise cost one search each through `PokemonPriceTrackerSource`
    (see scripts/backfill_pokemonpricetracker_prices.py). Returns an empty
    dict for an ordinary lookup failure or no match.

    Deliberately does NOT catch `PokemonPriceTrackerRateLimited` -- a
    caller processing many cards in a loop (the only realistic caller of
    this function) must stop entirely on that, not treat it as "no data for
    this one" and move on to the next. See that exception's own docstring:
    doing the latter is exactly what got a real API key temporarily blocked
    for hammering it with 50+ guaranteed-to-fail requests in under 5
    minutes.
    """
    try:
        match = _find_pokemonpricetracker_product(api_key, card, client)
    except PokemonPriceTrackerRateLimited:
        raise
    except PokemonPriceTrackerLookupError:
        return {}
    if match is None:
        return {}

    quotes: dict[CardVariant, PriceQuote] = {}
    for variant, printing in _POKEMONPRICETRACKER_PRINTING_BY_VARIANT.items():
        variant_data = (match.get("variants") or {}).get(printing)
        price = variant_data.get("marketPrice") if variant_data else None
        if price:
            quotes[variant] = PriceQuote(price=Decimal(str(price)), currency="USD", source=POKEMONPRICETRACKER_SOURCE_NAME)
    return quotes


class PokemonPriceTrackerSource(PriceSource):
    """Real TCGPlayer market pricing (and, on a paid plan, eBay sold comps)
    per specific printing, from pokemonpricetracker.com -- the source the
    user asked for once one existed (see the module docstring above).

    Unlike `PokemonTcgPriceSource`, this makes a live, *billed* network
    call per lookup: the free tier is 100 credits/day, and a search is
    billed for its `limit` regardless of how many results actually come
    back (confirmed against a real call -- an unlimited search defaults to
    limit=50 server-side and bills for all 50). Always wrap this in
    `CachedPriceSource` before using it anywhere a card might be priced
    more than once (e.g. scanning multiple copies of the same common)
    -- unwrapped, a popular card scanned repeatedly in one session would
    burn through the daily quota on repeat lookups of the same price.

    Looking up more than one variant of the *same* card through this class
    re-searches once per variant -- fine for live scanning, where usually
    only one variant is being committed at a time, but wasteful for a
    batch job pricing many held variants at once; see
    `fetch_all_pokemonpricetracker_variants` for that case instead.
    """

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._api_key = api_key
        self._client = client

    def get_price(self, card: CatalogCard, variant: CardVariant) -> PriceQuote | None:
        printing = _POKEMONPRICETRACKER_PRINTING_BY_VARIANT.get(variant)
        if printing is None:
            return None

        # Per the PriceSource contract, never raise for "no data" -- a
        # single live-scan lookup swallows even a hard rate-limit/credit
        # exhaustion (PokemonPriceTrackerRateLimited is a subclass of this)
        # as "no price this time" rather than taking the commit down.
        try:
            match = _find_pokemonpricetracker_product(self._api_key, card, self._client)
        except PokemonPriceTrackerLookupError:
            return None
        if match is None:
            return None

        variant_data = (match.get("variants") or {}).get(printing)
        price = variant_data.get("marketPrice") if variant_data else None
        if not price:
            return None
        return PriceQuote(price=Decimal(str(price)), currency="USD", source=POKEMONPRICETRACKER_SOURCE_NAME)


class CachedPriceSource(PriceSource):
    """Wraps another `PriceSource`, skipping its lookup when a recent-enough
    observation from that same source already sits in `card_prices` --
    required for any *billed* source (see `PokemonPriceTrackerSource`), and
    harmless overhead for a free one. `max_age` defaults to the free tier's
    own daily reset cadence, so a card priced today is not re-billed for
    again until tomorrow's quota exists to spend on it.
    """

    def __init__(self, wrapped: PriceSource, session: Session, source_name: str, max_age: timedelta = timedelta(hours=24)):
        self._wrapped = wrapped
        self._session = session
        self._source_name = source_name
        self._max_age = max_age

    def get_price(self, card: CatalogCard, variant: CardVariant) -> PriceQuote | None:
        cutoff = datetime.now(UTC) - self._max_age
        recent = self._session.scalars(
            select(CardPrice)
            .where(
                CardPrice.card_id == card.id, CardPrice.variant == variant,
                CardPrice.source == self._source_name, CardPrice.fetched_at >= cutoff,
            )
            .order_by(CardPrice.fetched_at.desc())
            .limit(1)
        ).first()
        if recent is not None:
            return PriceQuote(price=recent.price, currency=recent.currency, source=recent.source)
        return self._wrapped.get_price(card, variant)


def price_and_record(session: Session, source: PriceSource, card: CatalogCard, variant: CardVariant) -> CardPrice | None:
    """Get a quote and persist it to the append-only `card_prices` table.

    Always inserts a fresh observation rather than upserting -- price
    history is the point of a separate table (per the schema's own
    docstring). `PokemonTcgPriceSource` makes no network call, so a fresh
    row per commit costs nothing; a billed source should be wrapped in
    `CachedPriceSource` first (see its own docstring) rather than relied on
    to skip calls itself. A caller that only wants a same-session quote
    without growing the table can call `source.get_price` directly instead.
    """
    quote = source.get_price(card, variant)
    if quote is None:
        return None

    record = CardPrice(
        card_id=card.id, variant=variant, source=quote.source,
        price=quote.price, currency=quote.currency, fetched_at=datetime.now(UTC),
    )
    session.add(record)
    session.commit()
    return record
