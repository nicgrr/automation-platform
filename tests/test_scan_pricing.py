from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard
from automation_control.scan_ingest.pricing import PokemonTcgPriceSource, PriceQuote, PriceSource, price_and_record


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
