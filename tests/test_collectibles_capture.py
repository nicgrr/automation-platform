import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCollectible
from automation_control.database import Base, get_session
from automation_control.models import CapturedCollectible, CardCaptureStatus, CatalogItem, CollectibleProduct, InventoryItem


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, tmp_path):
    original_api_key = app.state.settings.anthropic_api_key
    original_dir = app.state.settings.captured_cards_dir
    app.state.settings.anthropic_api_key = "test-key"
    app.state.settings.captured_cards_dir = str(tmp_path / "captured_cards")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.anthropic_api_key = original_api_key
    app.state.settings.captured_cards_dir = original_dir


def _upload():
    return {"photo": ("figure.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")}


def test_capture_page_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/collectibles/capture")
    assert resp.status_code == 401


def test_capture_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/collectibles/capture", files=_upload())
    assert resp.status_code == 503


def test_capture_success_creates_pending_review_row(client, session):
    fake = ExtractedCollectible(brand="Sonny Angel", series="Fruits", character="Peach", variant="", is_secret=False, blind_box_series="Fruits Series 2", unreadable_fields=["variant"])
    with patch("automation_control.collectibles.extract_collectible_details", return_value=[fake]):
        resp = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedCollectible, capture_id)
    assert capture.brand == "Sonny Angel"
    assert capture.character == "Peach"
    assert capture.variant is None  # was in unreadable_fields
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_no_collectible_detected_creates_blank_row(client, session):
    with patch("automation_control.collectibles.extract_collectible_details", return_value=[]):
        resp = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedCollectible, capture_id)
    assert capture.brand is None
    assert capture.ai_raw_response == {"note": "no collectible detected in photo"}
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_ai_failure_still_creates_row_for_manual_entry(client, session):
    with patch("automation_control.collectibles.extract_collectible_details", side_effect=RuntimeError("boom")):
        resp = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedCollectible, capture_id)
    assert capture.brand is None
    assert capture.ai_raw_response == {"error": "boom"}
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_multiple_figures_creates_multiple_rows(client, session):
    figures = [
        ExtractedCollectible(brand="Sonny Angel", series="Fruits", character="Peach", variant="", is_secret=False, blind_box_series="", unreadable_fields=[]),
        ExtractedCollectible(brand="Smiski", series="Bathroom", character="Toothbrush", variant="", is_secret=True, blind_box_series="", unreadable_fields=[]),
    ]
    with patch("automation_control.collectibles.extract_collectible_details", return_value=figures):
        resp = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    rows = session.query(CapturedCollectible).order_by(CapturedCollectible.character).all()
    assert len(rows) == 2
    assert {r.character for r in rows} == {"Peach", "Toothbrush"}
    assert any(r.is_secret for r in rows)


def test_review_list_shows_only_pending(client, session):
    session.add_all([
        CapturedCollectible(id="p1", image_path="f", character="Pending Figure", status=CardCaptureStatus.PENDING_REVIEW),
        CapturedCollectible(id="r1", image_path="f", character="Reviewed Figure", status=CardCaptureStatus.REVIEWED),
    ])
    session.commit()

    resp = client.get("/collectibles/review")
    assert resp.status_code == 200
    assert "Pending Figure" in resp.text
    assert "Reviewed Figure" not in resp.text


def test_review_list_empty_state(client):
    resp = client.get("/collectibles/review")
    assert resp.status_code == 200
    assert "empty" in resp.text.lower()


def test_review_form_404_for_unknown_capture(client):
    resp = client.get("/collectibles/review/does-not-exist")
    assert resp.status_code == 404


def test_review_confirm_creates_catalog_item_and_collectible_product(client, session):
    capture = CapturedCollectible(id="abc123", image_path="f", brand="Sonny Angel", series="Fruits", character="Peach", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post(
        "/collectibles/review/abc123",
        data={"action": "confirm", "brand": "Sonny Angel", "series": "Fruits", "character": "Peach", "variant": "", "blind_box_series": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/collectibles"

    session.expire_all()
    capture = session.get(CapturedCollectible, "abc123")
    assert capture.status == CardCaptureStatus.REVIEWED
    assert capture.catalog_item_id is not None

    item = session.get(CatalogItem, capture.catalog_item_id)
    assert item.name == "Peach"
    product = session.get(CollectibleProduct, capture.catalog_item_id)
    assert product.brand == "Sonny Angel"

    inventory_item = session.query(InventoryItem).filter_by(catalog_item_id=capture.catalog_item_id).one()
    assert inventory_item.card_id is None
    assert inventory_item.quantity == 1  # default confirm quantity


def test_review_confirm_with_custom_quantity(client, session):
    capture = CapturedCollectible(id="abc123", image_path="f", character="Peach", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post(
        "/collectibles/review/abc123",
        data={"action": "confirm", "character": "Peach", "quantity": "4"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.expire_all()
    capture = session.get(CapturedCollectible, "abc123")
    inventory_item = session.query(InventoryItem).filter_by(catalog_item_id=capture.catalog_item_id).one()
    assert inventory_item.quantity == 4


def test_review_reject_marks_rejected_without_creating_catalog_item(client, session):
    capture = CapturedCollectible(id="abc123", image_path="f", character="Peach", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post("/collectibles/review/abc123", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/collectibles/review"

    session.expire_all()
    capture = session.get(CapturedCollectible, "abc123")
    assert capture.status == CardCaptureStatus.REJECTED
    assert capture.catalog_item_id is None
    assert session.query(CatalogItem).count() == 0


def test_capture_image_404_when_unknown(client):
    resp = client.get("/collectibles/capture/image/does-not-exist")
    assert resp.status_code == 404


def test_collectibles_page_shows_pending_review_count(client, session):
    session.add(CapturedCollectible(id="p1", image_path="f", character="Peach", status=CardCaptureStatus.PENDING_REVIEW))
    session.commit()
    resp = client.get("/collectibles")
    assert resp.status_code == 200
    assert "Review queue (1)" in resp.text
