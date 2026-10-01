"""scripts/crm_health_check.py: the on-demand status report backing the
/crm-health-check Skill (Phase 3c, 2026-09-29)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardPrice, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus
from scripts.crm_health_check import STALE_PRICE_DAYS, stale_priced_available_items, main


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def make_inventory(session, *, card_id, catalog_item_id=None, status=InventoryStatus.AVAILABLE):
    item = InventoryItem(
        id=str(uuid.uuid4()), card_id=card_id,
        catalog_item_id=catalog_item_id or card_id, status=status, quantity=1,
    )
    session.add(item)
    session.commit()
    return item


def make_price(session, *, card_id, fetched_at):
    price = CardPrice(catalog_item_id=card_id, source="test", price="5.00", fetched_at=fetched_at)
    session.add(price)
    session.commit()
    return price


def test_an_item_with_a_fresh_price_is_not_stale(session):
    make_inventory(session, card_id="card-1")
    make_price(session, card_id="card-1", fetched_at=datetime.now(UTC) - timedelta(days=1))
    assert stale_priced_available_items(session) == 0


def test_an_item_with_only_an_old_price_is_stale(session):
    make_inventory(session, card_id="card-1")
    make_price(session, card_id="card-1", fetched_at=datetime.now(UTC) - timedelta(days=STALE_PRICE_DAYS + 1))
    assert stale_priced_available_items(session) == 1


def test_an_item_with_no_price_at_all_is_stale(session):
    make_inventory(session, card_id="card-1")
    assert stale_priced_available_items(session) == 1


def test_a_sold_items_stale_price_does_not_count(session):
    make_inventory(session, card_id="card-1", status=InventoryStatus.SOLD)
    assert stale_priced_available_items(session) == 0


def test_one_fresh_price_is_enough_even_with_an_old_one_too(session):
    make_inventory(session, card_id="card-1")
    make_price(session, card_id="card-1", fetched_at=datetime.now(UTC) - timedelta(days=STALE_PRICE_DAYS + 5))
    make_price(session, card_id="card-1", fetched_at=datetime.now(UTC) - timedelta(days=1))
    assert stale_priced_available_items(session) == 0


def test_non_card_collectibles_are_counted_by_distinct_catalog_item(session):
    make_inventory(session, card_id=None, catalog_item_id="collectible-1")
    make_inventory(session, card_id=None, catalog_item_id="collectible-2")
    make_price(session, card_id="collectible-1", fetched_at=datetime.now(UTC) - timedelta(days=1))

    assert stale_priced_available_items(session) == 1


def test_manual_only_item_is_not_a_false_stale_price_alert(session):
    session.add(CatalogItem(
        id="unsupported-print", item_type=CatalogItemType.TCG_CARD,
        name="Unsupported printing", pricing_policy="manual_only",
        pricing_policy_reason="No trustworthy automated source",
    ))
    session.commit()
    make_inventory(session, card_id=None, catalog_item_id="unsupported-print")

    assert stale_priced_available_items(session) == 0


def test_main_returns_zero_when_every_section_is_clean(session, capsys, monkeypatch):
    import scripts.crm_health_check as mod

    monkeypatch.setattr(mod, "SessionLocal", lambda: session)
    session.close = lambda: None
    session.__enter__ = lambda s=session: s
    session.__exit__ = lambda *a: None
    monkeypatch.setattr(mod, "queue_counts", lambda s, settings: {"/review": 0})
    monkeypatch.setattr(mod, "JOBS", ())

    assert main() == 0
    assert "All clear." in capsys.readouterr().out


def test_main_returns_one_when_the_backlog_is_nonzero(session, capsys, monkeypatch):
    import scripts.crm_health_check as mod

    monkeypatch.setattr(mod, "SessionLocal", lambda: session)
    session.close = lambda: None
    session.__enter__ = lambda s=session: s
    session.__exit__ = lambda *a: None
    monkeypatch.setattr(mod, "queue_counts", lambda s, settings: {"/review": 5})
    monkeypatch.setattr(mod, "JOBS", ())

    assert main() == 1
    assert "Needs attention" in capsys.readouterr().out
