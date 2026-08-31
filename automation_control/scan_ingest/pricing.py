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
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import CardPrice, CardVariant, CatalogCard

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


def price_and_record(session: Session, source: PriceSource, card: CatalogCard, variant: CardVariant) -> CardPrice | None:
    """Get a quote and persist it to the append-only `card_prices` table.

    Always inserts a fresh observation rather than upserting -- price
    history is the point of a separate table (per the schema's own
    docstring), and observations are cheap since this source makes no
    network call. A caller that only wants a same-session quote without
    growing the table can call `source.get_price` directly instead.
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
