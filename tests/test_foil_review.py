import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import automation_control.foil_review as foil_review
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.foil_review import _pending
from automation_control.models import CardVariant, CatalogCard, InventoryItem


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    """foil_review's candidate cache is a file next to scan_media_dir --
    redirected to a temp path so tests never touch the real one."""
    path = tmp_path / "foil_candidates_cache.json"
    monkeypatch.setattr(foil_review, "_cache_path", lambda: path)
    return path


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _write_cache(cache_path, candidates: list[dict]) -> None:
    cache_path.write_text(json.dumps(candidates))


def _candidate(item_id: str, *, card_id="xy11-1", label="Test #1", magnitude=1.0) -> dict:
    return {
        "item_id": item_id, "card_id": card_id, "label": label, "filed": "normal",
        "suggested": "holo", "confidence": 0.98, "reason": "test", "metrics": {}, "magnitude": magnitude,
    }


def test_a_cached_candidate_for_a_deleted_item_is_dropped_not_shown(session, cache_path):
    """Real production incident (2026-09-25): a cached candidate's
    InventoryItem was deleted after the cache was built. The page only
    ever renders pending[0], so one stale entry at the front jammed the
    whole queue -- every image request and decide POST 404d, with no way
    to reach any of the other legitimately pending candidates."""
    _write_cache(cache_path, [_candidate("deleted-item")])
    # No InventoryItem with this id exists in `session` at all.
    assert _pending(session) == []


def test_a_live_candidate_still_shows_once_the_dead_one_is_filtered(session, cache_path):
    session.add(CatalogCard(id="xy11-1", set_id="xy11", number="1", name="Live Card"))
    session.add(InventoryItem(id="live-item", card_id="xy11-1", variant=CardVariant.NORMAL,
                              condition="Near Mint", quantity=1))
    session.commit()
    _write_cache(cache_path, [
        _candidate("deleted-item", label="Dead #1", magnitude=2.0),
        _candidate("live-item", label="Live #1", magnitude=1.0),
    ])

    pending = _pending(session)

    assert [c["item_id"] for c in pending] == ["live-item"]


def test_foil_review_page_skips_a_stale_candidate_and_shows_the_real_one(client, session, cache_path):
    session.add(CatalogCard(id="xy11-1", set_id="xy11", number="1", name="Live Card"))
    session.add(InventoryItem(id="live-item", card_id="xy11-1", variant=CardVariant.NORMAL,
                              condition="Near Mint", quantity=1))
    session.commit()
    _write_cache(cache_path, [
        _candidate("deleted-item", label="Dead #1", magnitude=2.0),
        _candidate("live-item", label="Live #1", magnitude=1.0),
    ])

    body = client.get("/foil-review").text

    assert "live-item" in body
    assert "deleted-item" not in body
