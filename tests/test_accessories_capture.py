import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.accessories import CAPTURE_FIELDS
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedAccessory
from automation_control.database import Base, get_session
from automation_control.models import AccessoryProduct, CapturedAccessory, CardCaptureStatus, CatalogItem, InventoryItem


def test_capture_fields_are_the_accessory_specific_set():
    assert CAPTURE_FIELDS == ["brand", "product_type", "pack_size", "color"]


@pytest.fixture
def engine(tmp_path):
    # File-based, not :memory:: bulk capture's background task opens its own
    # DB session on its own connection (from a different thread) -- see
    # test_collectibles_capture.py's identical fixture for why.
    return create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})


@pytest.fixture
def session(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, engine, tmp_path):
    original_api_key = app.state.settings.anthropic_api_key
    original_dir = app.state.settings.captured_cards_dir
    app.state.settings.anthropic_api_key = "test-key"
    app.state.settings.captured_cards_dir = str(tmp_path / "captured_cards")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("automation_control.accessories.SessionLocal", test_session_factory):
        yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.anthropic_api_key = original_api_key
    app.state.settings.captured_cards_dir = original_dir


def _upload():
    return {"photo": ("sleeves.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")}


def test_capture_page_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/accessories/capture")
    assert resp.status_code == 401


def test_capture_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/accessories/capture", files=_upload())
    assert resp.status_code == 503


def test_capture_success_creates_pending_review_row(client, session):
    fake = ExtractedAccessory(brand="Ultra Pro", product_type="Sleeves", pack_size="100ct", color="",
                              unreadable_fields=["color"])
    with patch("automation_control.accessories.extract_accessory_details", return_value=[fake]):
        resp = client.post("/accessories/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedAccessory, capture_id)
    assert capture.brand == "Ultra Pro"
    assert capture.product_type == "Sleeves"
    assert capture.color is None  # was in unreadable_fields
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_no_accessory_detected_creates_blank_row(client, session):
    with patch("automation_control.accessories.extract_accessory_details", return_value=[]):
        resp = client.post("/accessories/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedAccessory, capture_id)
    assert capture.brand is None
    assert capture.ai_raw_response == {"note": "no supply item detected in photo"}
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_ai_failure_still_creates_row_for_manual_entry(client, session):
    with patch("automation_control.accessories.extract_accessory_details", side_effect=RuntimeError("boom")):
        resp = client.post("/accessories/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    capture_id = resp.headers["location"].rsplit("/", 1)[-1]
    capture = session.get(CapturedAccessory, capture_id)
    assert capture.ai_raw_response == {"error": "boom"}
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_multiple_items_creates_multiple_rows(client, session):
    items = [
        ExtractedAccessory(brand="Ultra Pro", product_type="Sleeves", pack_size="100ct", color="Clear", unreadable_fields=[]),
        ExtractedAccessory(brand="Dragon Shield", product_type="Deck Box", pack_size="", color="Black", unreadable_fields=["pack_size"]),
    ]
    with patch("automation_control.accessories.extract_accessory_details", return_value=items):
        resp = client.post("/accessories/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 303
    rows = session.query(CapturedAccessory).order_by(CapturedAccessory.brand).all()
    assert len(rows) == 2
    assert {r.brand for r in rows} == {"Dragon Shield", "Ultra Pro"}


def test_review_list_shows_only_pending(client, session):
    session.add_all([
        CapturedAccessory(id="p1", image_path="f", product_type="Pending Item", status=CardCaptureStatus.PENDING_REVIEW),
        CapturedAccessory(id="r1", image_path="f", product_type="Reviewed Item", status=CardCaptureStatus.REVIEWED),
    ])
    session.commit()

    resp = client.get("/accessories/review")
    assert resp.status_code == 200
    assert "Pending Item" in resp.text
    assert "Reviewed Item" not in resp.text


def test_review_list_empty_state(client):
    resp = client.get("/accessories/review")
    assert resp.status_code == 200
    assert "empty" in resp.text.lower()


def test_review_form_404_for_unknown_capture(client):
    resp = client.get("/accessories/review/does-not-exist")
    assert resp.status_code == 404


def test_review_confirm_creates_catalog_item_and_accessory_product(client, session):
    capture = CapturedAccessory(id="abc123", image_path="f", brand="Ultra Pro", product_type="Sleeves",
                                pack_size="100ct", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post(
        "/accessories/review/abc123",
        data={"action": "confirm", "brand": "Ultra Pro", "product_type": "Sleeves", "pack_size": "100ct", "color": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/accessories/review"

    session.expire_all()
    capture = session.get(CapturedAccessory, "abc123")
    assert capture.status == CardCaptureStatus.REVIEWED
    assert capture.catalog_item_id is not None

    item = session.get(CatalogItem, capture.catalog_item_id)
    assert item.name == "Ultra Pro Sleeves 100ct"
    product = session.get(AccessoryProduct, capture.catalog_item_id)
    assert product.brand == "Ultra Pro"
    assert product.pack_size == "100ct"

    inventory_item = session.query(InventoryItem).filter_by(catalog_item_id=capture.catalog_item_id).one()
    assert inventory_item.card_id is None
    assert inventory_item.quantity == 1
    assert inventory_item.scan_image_path == "f"


def test_review_confirm_with_custom_quantity(client, session):
    capture = CapturedAccessory(id="abc123", image_path="f", product_type="Sleeves", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post(
        "/accessories/review/abc123",
        data={"action": "confirm", "product_type": "Sleeves", "quantity": "12"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    session.expire_all()
    capture = session.get(CapturedAccessory, "abc123")
    inventory_item = session.query(InventoryItem).filter_by(catalog_item_id=capture.catalog_item_id).one()
    assert inventory_item.quantity == 12


def test_review_reject_marks_rejected_without_creating_catalog_item(client, session):
    capture = CapturedAccessory(id="abc123", image_path="f", product_type="Sleeves", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post("/accessories/review/abc123", data={"action": "reject"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/accessories/review"

    session.expire_all()
    capture = session.get(CapturedAccessory, "abc123")
    assert capture.status == CardCaptureStatus.REJECTED
    assert capture.catalog_item_id is None
    assert session.query(CatalogItem).count() == 0


def test_review_skip_leaves_status_pending(client, session):
    capture = CapturedAccessory(id="abc123", image_path="f", product_type="Sleeves", status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()

    resp = client.post("/accessories/review/abc123", data={"action": "skip"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    capture = session.get(CapturedAccessory, "abc123")
    assert capture.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_image_404_when_unknown(client):
    resp = client.get("/accessories/capture/image/does-not-exist")
    assert resp.status_code == 404
