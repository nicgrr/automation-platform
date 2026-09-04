"""Only the safety-critical behavior gets a test here (this codebase's
other backfill scripts aren't unit tested) -- see
PokemonPriceTrackerRateLimited's docstring for why: a caller that keeps
looping past a hard rate limit is what got a real API key temporarily
blocked for hammering it with 50+ guaranteed-to-fail requests in under 5
minutes.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from automation_control.adapters.pokemonpricetracker import PokemonPriceTrackerRateLimited
from automation_control.database import Base
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard, InventoryItem
from automation_control.scan_ingest import pricing as pricing_mod
from scripts import backfill_pokemonpricetracker_prices as backfill_mod


@dataclass
class FakeSettings:
    pokemonpricetracker_api_key: str | None = "fake-key"


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(session_factory, cards: list[tuple[str, str]]):
    with session_factory() as session:
        session.add(CardSet(id="xy11", name="Steam Siege"))
        for card_id, number in cards:
            session.add(CatalogCard(id=card_id, set_id="xy11", number=number, name=f"Card {number}"))
            session.add(InventoryItem(card_id=card_id, variant=CardVariant.NORMAL))
        session.commit()


def test_stops_the_whole_run_on_a_hard_rate_limit_instead_of_looping_through_every_card(monkeypatch, session_factory):
    """The bug this locks in: fetch_all_pokemonpricetracker_variants used
    to be called per card with no special handling, so once credits ran
    out every remaining card in --max-cards re-triggered the same 429.
    Confirmed live: that stormed 50+ 429s in under 5 minutes and got the
    API key temporarily blocked. One card succeeds, the next hits the hard
    limit -- the run must stop there, never reaching the third.
    """
    cards = [("xy11-1", "1"), ("xy11-2", "2"), ("xy11-3", "3")]
    _seed(session_factory, cards)
    monkeypatch.setattr(backfill_mod, "SessionLocal", session_factory)
    monkeypatch.setattr(backfill_mod, "get_settings", lambda: FakeSettings())

    calls = []

    def fake_search_cards(api_key, *, search=None, set_name=None, tcgplayer_id=None, limit=None, include_history=False, client=None):
        calls.append(search)
        if search == "Card 1":
            return [{"cardNumber": "1/198", "variants": {"Normal": {"marketPrice": 1.23}}}]
        raise PokemonPriceTrackerRateLimited(82757)

    monkeypatch.setattr(pricing_mod, "search_cards", fake_search_cards)

    exit_code = backfill_mod.main(["--max-cards", "10"])

    assert exit_code == 0
    assert calls == ["Card 1", "Card 2"]  # stopped right after the rate limit, never reached Card 3

    with session_factory() as session:
        prices = session.scalars(select(CardPrice)).all()
        assert len(prices) == 1
        assert prices[0].card_id == "xy11-1"
        assert prices[0].price == Decimal("1.23")


def test_skips_cards_already_priced_by_this_source(monkeypatch, session_factory):
    _seed(session_factory, [("xy11-1", "1")])
    with session_factory() as session:
        session.add(CardPrice(card_id="xy11-1", variant=CardVariant.NORMAL, source="pokemonpricetracker_market", price=Decimal("1.00"), currency="USD"))
        session.commit()

    monkeypatch.setattr(backfill_mod, "SessionLocal", session_factory)
    monkeypatch.setattr(backfill_mod, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(pricing_mod, "search_cards", lambda *a, **k: pytest.fail("should not search an already-priced card"))

    exit_code = backfill_mod.main([])
    assert exit_code == 0


def test_returns_early_without_a_key_configured(monkeypatch, session_factory):
    monkeypatch.setattr(backfill_mod, "SessionLocal", session_factory)
    monkeypatch.setattr(backfill_mod, "get_settings", lambda: FakeSettings(pokemonpricetracker_api_key=None))
    assert backfill_mod.main([]) == 1
