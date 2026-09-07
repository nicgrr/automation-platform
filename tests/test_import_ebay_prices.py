from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.config import Settings
from automation_control.database import Base
from automation_control.models import (
    CardPrice, CardVariant, CatalogItem, CatalogItemType, InventoryItem, TcgCard,
)
from scripts import import_ebay_prices as script


def _configured_settings():
    return Settings(
        ebay_env="sandbox", ebay_client_id="app-SBX-x", ebay_runame="app-SBX-runame",
        ebay_client_secret="s", ebay_token_encryption_key=Fernet.generate_key().decode(),
        app_public_base_url="https://example.test",
    )


def _seed(session, *, owned=True):
    session.add(CatalogItem(id="op13-076", item_type=CatalogItemType.TCG_CARD, name="Divine Departure"))
    session.add(TcgCard(catalog_item_id="op13-076", game="one_piece", set_code="OP13", number="076"))
    if owned:
        session.add(InventoryItem(id="inv1", card_id=None, catalog_item_id="op13-076", variant=CardVariant.NORMAL, quantity=1))
    session.commit()


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def patched(monkeypatch, engine):
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script, "get_settings", _configured_settings)
    monkeypatch.setattr(script.time, "sleep", lambda *_: None)

    oauth = MagicMock()
    oauth.application_token = AsyncMock(return_value={"access_token": "app-token"})
    monkeypatch.setattr(script, "ebay_services", lambda settings: (MagicMock(), oauth))
    return engine


def _listings(*values, currency="AUD"):
    return {"itemSummaries": [
        {"itemId": str(i), "title": f"listing {i}", "price": {"value": str(v), "currency": currency}}
        for i, v in enumerate(values)
    ]}


def test_build_query_prefers_set_code_and_number():
    item = CatalogItem(id="x", item_type=CatalogItemType.TCG_CARD, name="Divine Departure")
    tcg = TcgCard(catalog_item_id="x", game="one_piece", set_code="OP13", number="076")
    assert script.build_query(item, tcg) == "Divine Departure OP13-076"


def test_build_query_falls_back_to_name_without_a_code():
    item = CatalogItem(id="x", item_type=CatalogItemType.TCG_CARD, name="Some Promo")
    assert script.build_query(item, None) == "Some Promo"


def test_records_low_and_median_as_separate_sources(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 20, 60))):
        assert script.main([]) == 0

    with Session(engine) as session:
        prices = {p.source: p for p in session.query(CardPrice).all()}
        assert set(prices) == {"ebay_active_low", "ebay_active_median"}
        assert prices["ebay_active_low"].price == Decimal("10")
        assert prices["ebay_active_median"].price == Decimal("20")
        assert prices["ebay_active_low"].currency == "AUD"
        assert prices["ebay_active_low"].card_id == "op13-076"


def test_dry_run_queries_but_writes_nothing(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 20))):
        assert script.main(["--dry-run"]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_no_listings_writes_no_fabricated_price(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value={"itemSummaries": []})):
        assert script.main([]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_api_failure_on_one_card_does_not_abort_the_run(patched):
    from automation_control.adapters.ebay import EbayApiError

    engine = patched
    with Session(engine) as session:
        _seed(session)
        session.add(CatalogItem(id="op13-077", item_type=CatalogItemType.TCG_CARD, name="Second Card"))
        session.add(TcgCard(catalog_item_id="op13-077", game="one_piece", set_code="OP13", number="077"))
        session.add(InventoryItem(id="inv2", card_id=None, catalog_item_id="op13-077", variant=CardVariant.NORMAL, quantity=1))
        session.commit()

    search = AsyncMock(side_effect=[EbayApiError("boom"), _listings(30, 40)])
    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=search):
        assert script.main([]) == 0

    with Session(engine) as session:
        # the second card still got priced despite the first one failing
        assert session.query(CardPrice).count() == 2


def test_defaults_to_only_pricing_cards_you_own(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session, owned=False)  # in the catalogue, but not owned

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10))):
        assert script.main([]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 0


def test_all_flag_prices_unowned_catalogue_cards_too(patched):
    engine = patched
    with Session(engine) as session:
        _seed(session, owned=False)

    with patch.object(script.EbaySandboxReadAdapter, "search_market", new=AsyncMock(return_value=_listings(10, 12))):
        assert script.main(["--all"]) == 0

    with Session(engine) as session:
        assert session.query(CardPrice).count() == 2


def test_unconfigured_ebay_exits_with_a_clear_message(monkeypatch, engine):
    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(script, "get_settings", lambda: Settings(ebay_env="sandbox"))

    with pytest.raises(SystemExit) as exc:
        script.main([])
    assert "not configured" in str(exc.value)
