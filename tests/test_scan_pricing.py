from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.adapters.pokemonpricetracker import PokemonPriceTrackerLookupError
from automation_control.database import Base
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard
from automation_control.scan_ingest import pricing as pricing_mod
from automation_control.scan_ingest.pricing import (
    POKEMONPRICETRACKER_SOURCE_NAME, CachedPriceSource, PokemonPriceTrackerSource, PokemonTcgPriceSource,
    PriceQuote, PriceSource, fetch_all_pokemonpricetracker_variants, price_and_record,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CardSet(id="xy11", name="Steam Siege"))
        db.commit()
        yield db


def _card(session, raw_prices=None, id_="xy11-31") -> CatalogCard:
    card = CatalogCard(id=id_, set_id="xy11", number="31", name="Dewott", raw_prices=raw_prices)
    session.add(card)
    session.commit()
    return card


TCGPLAYER = {
    "tcgplayer": {
        "prices": {
            "normal": {"low": 0.02, "mid": 0.2, "high": 19.98, "market": 0.17},
            "reverseHolofoil": {"low": 0.2, "mid": 0.5, "high": 19.98, "market": 0.49},
        }
    }
}

CARDMARKET_ONLY = {
    "cardmarket": {
        "prices": {
            "averageSellPrice": 0.1, "lowPrice": 0.02, "trendPrice": 0.06,
            "reverseHoloSell": 0.4, "reverseHoloLow": 0.1, "reverseHoloTrend": 0.62,
        }
    }
}


# --- PokemonTcgPriceSource ---

def test_returns_none_when_no_pricing_was_cached(session):
    card = _card(session, raw_prices=None)
    assert PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL) is None


def test_reads_tcgplayer_market_price_for_normal(session):
    card = _card(session, raw_prices=TCGPLAYER)
    quote = PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL)

    assert quote.price == Decimal("0.17")
    assert quote.currency == "USD"
    assert quote.source == "pokemontcg_tcgplayer_market"


def test_reads_the_matching_variant_not_just_the_first_one(session):
    """Reverse holo and normal are genuinely different prices for the same
    card -- picking the wrong one would misprice by 3x here."""
    card = _card(session, raw_prices=TCGPLAYER)
    normal = PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL)
    reverse = PokemonTcgPriceSource().get_price(card, CardVariant.REVERSE_HOLO)

    assert normal.price == Decimal("0.17")
    assert reverse.price == Decimal("0.49")
    assert normal.price != reverse.price


def test_returns_none_for_a_variant_tcgplayer_never_printed(session):
    """This set has no holofoil printing of this card -- must not fall back
    to a different variant's price and silently mislabel it."""
    card = _card(session, raw_prices=TCGPLAYER)
    assert PokemonTcgPriceSource().get_price(card, CardVariant.HOLO) is None


def test_falls_back_through_mid_and_low_when_market_is_missing(session):
    card = _card(session, raw_prices={"tcgplayer": {"prices": {"normal": {"low": 0.05, "mid": 0.15}}}})
    quote = PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL)
    assert quote.price == Decimal("0.15")
    assert quote.source == "pokemontcg_tcgplayer_mid"


def test_falls_back_to_cardmarket_when_tcgplayer_has_nothing(session):
    card = _card(session, raw_prices=CARDMARKET_ONLY)
    quote = PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL)

    assert quote.currency == "EUR"
    assert quote.source == "pokemontcg_cardmarket_trendPrice"
    assert quote.price == Decimal("0.06")


def test_cardmarket_reverse_holo_uses_dedicated_fields(session):
    card = _card(session, raw_prices=CARDMARKET_ONLY)
    quote = PokemonTcgPriceSource().get_price(card, CardVariant.REVERSE_HOLO)
    assert quote.price == Decimal("0.62")  # reverseHoloTrend, not the base trendPrice


def test_zero_price_is_treated_as_no_data_not_a_free_card(session):
    """A literal 0 in the payload means 'no market data', not 'worth
    nothing' -- treating it as a real price would show a nonsense estimate."""
    card = _card(session, raw_prices={"tcgplayer": {"prices": {"normal": {"market": 0, "mid": 0, "low": 0.03}}}})
    quote = PokemonTcgPriceSource().get_price(card, CardVariant.NORMAL)
    assert quote.price == Decimal("0.03")


# --- price_and_record ---

def test_price_and_record_persists_an_observation(session):
    card = _card(session, raw_prices=TCGPLAYER)
    record = price_and_record(session, PokemonTcgPriceSource(), card, CardVariant.NORMAL)

    assert record is not None
    stored = session.scalars(select(CardPrice)).one()
    assert stored.card_id == "xy11-31"
    assert stored.price == Decimal("0.17")
    assert stored.currency == "USD"
    assert stored.variant is CardVariant.NORMAL


def test_price_and_record_returns_none_and_writes_nothing_without_data(session):
    card = _card(session, raw_prices=None)
    record = price_and_record(session, PokemonTcgPriceSource(), card, CardVariant.NORMAL)

    assert record is None
    assert session.scalars(select(CardPrice)).all() == []


def test_price_and_record_appends_rather_than_upserts(session):
    """Price history is the point of a separate table -- pricing the same
    card twice must add a second observation, not overwrite the first."""
    card = _card(session, raw_prices=TCGPLAYER)
    price_and_record(session, PokemonTcgPriceSource(), card, CardVariant.NORMAL)
    price_and_record(session, PokemonTcgPriceSource(), card, CardVariant.NORMAL)

    assert len(session.scalars(select(CardPrice)).all()) == 2


# --- PokemonPriceTrackerSource ---

WATTREL_RESULTS = [
    {
        "cardNumber": "077/198", "name": "Wattrel",
        "variants": {"Normal": {"marketPrice": 0.14}, "Reverse Holofoil": {"marketPrice": 0.21}},
    },
    {
        "cardNumber": "078/198", "name": "Wattrel",
        "variants": {"Normal": {"marketPrice": 0.09}, "Reverse Holofoil": {"marketPrice": 0.18}},
    },
]


def _card_with_number(session, number: str, id_: str) -> CatalogCard:
    card = CatalogCard(id=id_, set_id="xy11", number=number, name="Wattrel")
    session.add(card)
    session.commit()
    return card


def test_pokemonpricetracker_matches_the_right_printing_by_number(session, monkeypatch):
    """Two same-named reprints in the search results -- the wrong one would
    misprice by nearly double here (0.09 vs 0.14)."""
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: WATTREL_RESULTS)
    card = _card_with_number(session, "078", "xy11-w78")

    quote = PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL)
    assert quote.price == Decimal("0.09")
    assert quote.currency == "USD"
    assert quote.source == POKEMONPRICETRACKER_SOURCE_NAME


def test_pokemonpricetracker_reads_the_matching_variant_not_just_the_first(session, monkeypatch):
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: WATTREL_RESULTS)
    card = _card_with_number(session, "078", "xy11-w78")

    normal = PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL)
    reverse = PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.REVERSE_HOLO)
    assert normal.price == Decimal("0.09")
    assert reverse.price == Decimal("0.18")


def test_pokemonpricetracker_returns_none_when_no_result_matches_the_number(session, monkeypatch):
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: WATTREL_RESULTS)
    card = _card_with_number(session, "999", "xy11-w999")
    assert PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL) is None


def test_pokemonpricetracker_returns_none_for_a_printing_the_card_never_had(session, monkeypatch):
    only_normal = [{"cardNumber": "078/198", "name": "Wattrel", "variants": {"Normal": {"marketPrice": 0.09}}}]
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: only_normal)
    card = _card_with_number(session, "078", "xy11-w78")
    assert PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.HOLO) is None


def test_pokemonpricetracker_returns_none_on_lookup_failure_rather_than_raising(session, monkeypatch):
    def boom(*args, **kwargs):
        raise PokemonPriceTrackerLookupError("out of credits")
    monkeypatch.setattr(pricing_mod, "search_cards", boom)
    card = _card_with_number(session, "078", "xy11-w78")
    assert PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL) is None


def test_pokemonpricetracker_always_passes_a_small_limit(session, monkeypatch):
    """Billed per card requested, not per card returned (confirmed against
    a real call) -- must never fall back to an unbounded/default-size
    search."""
    seen = {}

    def fake_search_cards(api_key, *, search=None, set_name=None, tcgplayer_id=None, limit=None, include_history=False, client=None):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(pricing_mod, "search_cards", fake_search_cards)
    card = _card_with_number(session, "078", "xy11-w78")
    PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL)
    assert seen["limit"] is not None and seen["limit"] <= 5


def test_pokemonpricetracker_narrows_the_search_by_set_name(session, monkeypatch):
    """A name alone is nowhere near enough: confirmed against a real search
    for a Scarlet & Violet base-set common ("Wattrel") where three *other*
    sets' printings outranked ours within the top 3 results, so ours never
    appeared at all until the set name was added to the query."""
    seen = {}

    def fake_search_cards(api_key, *, search=None, set_name=None, tcgplayer_id=None, limit=None, include_history=False, client=None):
        seen["set_name"] = set_name
        return []

    monkeypatch.setattr(pricing_mod, "search_cards", fake_search_cards)
    card = _card_with_number(session, "078", "xy11-w78")
    PokemonPriceTrackerSource("fake-key").get_price(card, CardVariant.NORMAL)
    assert seen["set_name"] == "Steam Siege"  # the session fixture's CardSet name


# --- fetch_all_pokemonpricetracker_variants ---

def test_fetch_all_variants_returns_every_priced_printing_from_one_search(session, monkeypatch):
    """The whole point: a card held in both Normal and Reverse Holofoil
    costs one search here, not two through PokemonPriceTrackerSource."""
    calls = []

    def fake_search_cards(api_key, *, search=None, set_name=None, tcgplayer_id=None, limit=None, include_history=False, client=None):
        calls.append(1)
        return WATTREL_RESULTS

    monkeypatch.setattr(pricing_mod, "search_cards", fake_search_cards)
    card = _card_with_number(session, "078", "xy11-w78")

    quotes = fetch_all_pokemonpricetracker_variants("fake-key", card)
    assert quotes[CardVariant.NORMAL].price == Decimal("0.09")
    assert quotes[CardVariant.REVERSE_HOLO].price == Decimal("0.18")
    assert CardVariant.HOLO not in quotes  # this card never had a holo printing
    assert len(calls) == 1


def test_fetch_all_variants_returns_empty_dict_when_nothing_matches(session, monkeypatch):
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: WATTREL_RESULTS)
    card = _card_with_number(session, "999", "xy11-w999")
    assert fetch_all_pokemonpricetracker_variants("fake-key", card) == {}


def test_fetch_all_variants_returns_empty_dict_on_lookup_failure(session, monkeypatch):
    def boom(*args, **kwargs):
        raise PokemonPriceTrackerLookupError("out of credits")
    monkeypatch.setattr(pricing_mod, "search_cards", boom)
    card = _card_with_number(session, "078", "xy11-w78")
    assert fetch_all_pokemonpricetracker_variants("fake-key", card) == {}


# --- CachedPriceSource ---

class _CountingSource(PriceSource):
    def __init__(self, quote: PriceQuote):
        self.calls = 0
        self._quote = quote

    def get_price(self, card, variant):
        self.calls += 1
        return self._quote


def test_cached_price_source_skips_the_wrapped_call_when_a_recent_row_exists(session):
    card = _card(session, raw_prices=None)
    session.add(CardPrice(
        card_id=card.id, variant=CardVariant.NORMAL, source=POKEMONPRICETRACKER_SOURCE_NAME,
        price=Decimal("1.23"), currency="USD", fetched_at=datetime.now(UTC) - timedelta(hours=1),
    ))
    session.commit()

    wrapped = _CountingSource(PriceQuote(Decimal("9.99"), "USD", POKEMONPRICETRACKER_SOURCE_NAME))
    cached = CachedPriceSource(wrapped, session, POKEMONPRICETRACKER_SOURCE_NAME)
    quote = cached.get_price(card, CardVariant.NORMAL)

    assert quote.price == Decimal("1.23")  # the cached row, not a fresh 9.99
    assert wrapped.calls == 0


def test_cached_price_source_calls_through_when_the_cached_row_is_stale(session):
    card = _card(session, raw_prices=None)
    session.add(CardPrice(
        card_id=card.id, variant=CardVariant.NORMAL, source=POKEMONPRICETRACKER_SOURCE_NAME,
        price=Decimal("1.23"), currency="USD", fetched_at=datetime.now(UTC) - timedelta(hours=48),
    ))
    session.commit()

    wrapped = _CountingSource(PriceQuote(Decimal("9.99"), "USD", POKEMONPRICETRACKER_SOURCE_NAME))
    cached = CachedPriceSource(wrapped, session, POKEMONPRICETRACKER_SOURCE_NAME, max_age=timedelta(hours=24))
    quote = cached.get_price(card, CardVariant.NORMAL)

    assert quote.price == Decimal("9.99")
    assert wrapped.calls == 1


def test_cached_price_source_ignores_rows_from_a_different_source(session):
    """A recent PokemonTcgPriceSource row must not suppress a fresh
    PokemonPriceTracker lookup -- they're different sources with different
    reliability, tracked separately on purpose."""
    card = _card(session, raw_prices=None)
    session.add(CardPrice(
        card_id=card.id, variant=CardVariant.NORMAL, source="pokemontcg_tcgplayer_market",
        price=Decimal("1.23"), currency="USD", fetched_at=datetime.now(UTC),
    ))
    session.commit()

    wrapped = _CountingSource(PriceQuote(Decimal("9.99"), "USD", POKEMONPRICETRACKER_SOURCE_NAME))
    cached = CachedPriceSource(wrapped, session, POKEMONPRICETRACKER_SOURCE_NAME)
    quote = cached.get_price(card, CardVariant.NORMAL)

    assert quote.price == Decimal("9.99")
    assert wrapped.calls == 1


def test_cached_price_source_ignores_rows_for_a_different_variant(session):
    card = _card(session, raw_prices=None)
    session.add(CardPrice(
        card_id=card.id, variant=CardVariant.REVERSE_HOLO, source=POKEMONPRICETRACKER_SOURCE_NAME,
        price=Decimal("1.23"), currency="USD", fetched_at=datetime.now(UTC),
    ))
    session.commit()

    wrapped = _CountingSource(PriceQuote(Decimal("9.99"), "USD", POKEMONPRICETRACKER_SOURCE_NAME))
    cached = CachedPriceSource(wrapped, session, POKEMONPRICETRACKER_SOURCE_NAME)
    quote = cached.get_price(card, CardVariant.NORMAL)

    assert quote.price == Decimal("9.99")
    assert wrapped.calls == 1


# --- the interface itself ---

def test_price_source_is_swappable():
    """The whole point: a new source is a new class implementing one
    method, usable anywhere a PriceSource is expected."""

    class FixedPriceSource(PriceSource):
        def get_price(self, card, variant):
            return PriceQuote(price=Decimal("9.99"), currency="AUD", source="fixed-for-test")

    quote = FixedPriceSource().get_price(card=None, variant=CardVariant.NORMAL)
    assert quote.price == Decimal("9.99")


def test_price_source_cannot_be_instantiated_without_implementing_get_price():
    with pytest.raises(TypeError):
        PriceSource()
