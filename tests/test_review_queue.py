from datetime import UTC, datetime
from pathlib import Path

import imagehash
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control import review_queue
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CardSet, CardVariant, CatalogCard, InventoryItem, ScanSession


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def media(tmp_path, monkeypatch):
    """Point the app's media dir at a temp tree so the real queue is never
    touched by a test run."""
    root = tmp_path / "media"
    (root / "needs_review").mkdir(parents=True)
    settings = type("S", (), {"scan_media_dir": str(root), "scan_phash_max_distance": 12})()
    monkeypatch.setattr(review_queue, "get_settings", lambda: settings)
    return root


@pytest.fixture
def client(session, media):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _art(seed: int, size=(245, 342)) -> Image.Image:
    import numpy as np

    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))


def _queue(media: Path, name: str, seed: int = 1) -> Path:
    path = media / "needs_review" / name
    _art(seed).save(path)
    return path


def _cache_card(session, tmp_path, *, set_id="xy11", number="31", name="Dewott", seed=1):
    if session.get(CardSet, set_id) is None:
        # cached_at matters: only sets marked cached are matched against.
        session.add(CardSet(id=set_id, name="Steam Siege", printed_total=114,
                            cached_at=datetime.now(UTC)))
    ref = tmp_path / f"{set_id}-{number}.png"
    _art(seed).save(ref)
    with Image.open(ref) as image:
        phash = str(imagehash.phash(image.convert("RGB")))
    session.add(CatalogCard(id=f"{set_id}-{number}", set_id=set_id, number=number, name=name,
                            local_image_path=str(ref), phash=phash))
    session.commit()


# --- the page --------------------------------------------------------------

def test_review_requires_auth(media):
    app.dependency_overrides.clear()
    assert TestClient(app).get("/review").status_code in (401, 403, 307, 302)


def test_empty_queue_says_so(client):
    resp = client.get("/review")
    assert resp.status_code == 200
    assert "Queue is empty" in resp.text


def test_queue_lists_each_crop_with_its_image(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=1)
    resp = client.get("/review")
    assert "sheet-a-card1-unidentified.jpg" in resp.text
    assert "/review/image/sheet-a-card1-unidentified.jpg" in resp.text


def test_queue_shows_the_matcher_s_suggestion(client, media, session, tmp_path):
    """A crop identical to a cached card's art should be suggested as that
    card, so the operator's usual action is a single click."""
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.get("/review")
    assert "Dewott" in resp.text
    assert "#31" in resp.text


def test_queue_says_so_when_nothing_matches(client, media, session, tmp_path):
    _cache_card(session, tmp_path, seed=3)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=999)
    resp = client.get("/review")
    assert "No confident match" in resp.text


def test_queue_paginates(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    for n in range(review_queue.PAGE_SIZE + 3):
        _queue(media, f"sheet-a-card{n}-unidentified.jpg", seed=n + 20)
    first = client.get("/review")
    assert "Page 1 of 2" in first.text
    assert client.get("/review?page=2").status_code == 200


# --- decisions -------------------------------------------------------------

def test_accept_commits_the_card_and_clears_the_crop(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                               "set_id": "xy11", "number": "31"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert not crop.exists()
    item = session.scalars(select(InventoryItem)).one()
    assert item.card_id == "xy11-31"


def test_accept_records_a_scan_session(client, media, session, tmp_path):
    """Committing through the web must leave the same trail as the CLI, so
    the card is attributable to a session rather than appearing from nowhere."""
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31"}, follow_redirects=False)

    scan_session = session.scalars(select(ScanSession)).one()
    assert scan_session.set_id == "xy11"
    assert session.scalars(select(InventoryItem)).one().session_id == scan_session.id


def test_accept_with_a_corrected_number_commits_that_card(client, media, session, tmp_path):
    """The suggestion is only a suggestion -- a typed number wins."""
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    _cache_card(session, tmp_path, number="32", name="Samurott", seed=8)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "32"}, follow_redirects=False)

    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-32"


def test_accept_rejects_a_number_not_in_the_set(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                               "set_id": "xy11", "number": "999"}, follow_redirects=False)

    assert resp.status_code == 400
    assert crop.exists()  # nothing lost on a bad request
    assert session.scalars(select(InventoryItem)).all() == []


def test_discard_removes_the_crop_without_committing(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "discard"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert not crop.exists()
    assert session.scalars(select(InventoryItem)).all() == []


def test_unknown_action_is_rejected(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.post("/review/decide", data={"crop": crop.name, "action": "launch"}, follow_redirects=False)
    assert resp.status_code == 400
    assert crop.exists()


# --- path safety -----------------------------------------------------------

def test_crop_name_cannot_escape_the_review_directory(client, media, session, tmp_path):
    """The crop name arrives from a form field, so a traversal attempt must
    not reach a file outside the queue -- deleting or serving arbitrary
    files would otherwise be one POST away."""
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"not yours")

    for name in ("../secret.jpg", "../../secret.jpg", "/etc/hostname"):
        assert client.post("/review/decide", data={"crop": name, "action": "discard"},
                           follow_redirects=False).status_code == 404
        assert client.get(f"/review/image/{name}").status_code in (404, 307, 404)

    assert secret.exists()


def test_image_route_serves_a_queued_crop(client, media, session, tmp_path):
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.get(f"/review/image/{crop.name}")
    assert resp.status_code == 200
    assert resp.content == crop.read_bytes()
