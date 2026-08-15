import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCard
from automation_control.database import Base, get_session
from automation_control.models import CapturedCard, CardCaptureStatus


@pytest.fixture
def engine(tmp_path):
    # File-based, not :memory:: bulk capture's background task opens its own
    # DB session on its own connection (from a different thread), separate
    # from the request-scoped session used elsewhere in a test. A shared
    # :memory: connection (even via StaticPool) hands both sessions the same
    # raw sqlite3 connection object, which sqlite doesn't support two
    # concurrent transactions on -- a real file lets each session hold its
    # own connection while still reading/writing the same data.
    db_path = tmp_path / "test.db"
    return create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})


@pytest.fixture
def session(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session: Session, engine, tmp_path):
    original_api_key = app.state.settings.anthropic_api_key
    original_dir = app.state.settings.captured_cards_dir
    app.state.settings.anthropic_api_key = "test-key"
    app.state.settings.captured_cards_dir = str(tmp_path / "captured_cards")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    # Bulk capture processes in a background task using its own fresh DB
    # session (SessionLocal), since the request-scoped session above closes
    # before the background task runs. Point that at the same in-memory test
    # engine so background-processed data lands where the test can see it.
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with patch("automation_control.capture.SessionLocal", test_session_factory):
        yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.anthropic_api_key = original_api_key
    app.state.settings.captured_cards_dir = original_dir


def _upload_files():
    return {
        "front": ("front.jpg", io.BytesIO(b"fake-front-bytes"), "image/jpeg"),
        "back": ("back.jpg", io.BytesIO(b"fake-back-bytes"), "image/jpeg"),
    }


def test_capture_page_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/cards/capture")
    assert resp.status_code == 401


def test_capture_submit_success_creates_reviewed_fields(client, session):
    fake = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    with patch("automation_control.capture.extract_card_details", return_value=[fake]):
        resp = client.post("/cards/capture", files=_upload_files(), follow_redirects=False)

    assert resp.status_code == 303
    card_id = resp.headers["location"].rsplit("/", 1)[-1]
    card = session.get(CapturedCard, card_id)
    assert card.character == "Pikachu"
    assert card.set_name == "Base Set"
    assert card.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_submit_without_back_photo_is_allowed(client, session):
    fake = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    with patch("automation_control.capture.extract_card_details", return_value=[fake]) as mock_extract:
        resp = client.post(
            "/cards/capture",
            files={"front": ("front.jpg", io.BytesIO(b"fake-front"), "image/jpeg")},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    card_id = resp.headers["location"].rsplit("/", 1)[-1]
    card = session.get(CapturedCard, card_id)
    assert card.back_image_path is None
    assert card.character == "Pikachu"
    # extract_card_details called with back_path=None
    assert mock_extract.call_args.args[1] is None


def test_review_form_shows_no_back_photo_when_absent(client, session):
    card = CapturedCard(id="abc123", front_image_path="f", back_image_path=None, status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={})
    session.add(card)
    session.commit()
    resp = client.get("/cards/review/abc123")
    assert resp.status_code == 200
    assert "no back photo" in resp.text
    assert "/cards/image/abc123/back" not in resp.text


def test_card_image_404_when_back_not_captured(client, session):
    card = CapturedCard(id="abc123", front_image_path="f", back_image_path=None, status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={})
    session.add(card)
    session.commit()
    resp = client.get("/cards/image/abc123/back")
    assert resp.status_code == 404


def test_bulk_capture_creates_one_card_per_photo(client, session):
    fake = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    with patch("automation_control.capture.extract_card_details", return_value=[fake]):
        resp = client.post(
            "/cards/capture/bulk",
            files=[
                ("photos", ("card1.jpg", io.BytesIO(b"one"), "image/jpeg")),
                ("photos", ("card2.jpg", io.BytesIO(b"two"), "image/jpeg")),
                ("photos", ("card3.jpg", io.BytesIO(b"three"), "image/jpeg")),
            ],
            follow_redirects=False,
        )

    # background task runs to completion within the TestClient call, so by
    # the time we get the redirect, processing has already finished.
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/cards/capture/bulk/status/")
    cards = session.query(CapturedCard).all()
    assert len(cards) == 3
    assert all(c.back_image_path is None for c in cards)
    assert all(c.status == CardCaptureStatus.PENDING_REVIEW for c in cards)

    # the batch row was touched (and its attributes cached) by this same
    # session during the POST above, before the background task committed
    # its updates on a separate session -- expire so the GET below re-reads
    # the now-current row instead of serving the stale cached one.
    session.expire_all()
    status_resp = client.get(resp.headers["location"])
    assert status_resp.status_code == 200
    assert "3 card(s) captured from 3 photo(s)" in status_resp.text
    assert "Done" in status_resp.text


def test_bulk_capture_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post(
        "/cards/capture/bulk",
        files=[("photos", ("card1.jpg", io.BytesIO(b"one"), "image/jpeg"))],
    )
    assert resp.status_code == 503


def test_capture_one_photo_with_multiple_cards_creates_multiple_rows(client, session):
    cards = [
        ExtractedCard(character="Zapdos ex", set_name="Promo", card_number="SVP 049", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
        ExtractedCard(character="Flareon", set_name="Promo", card_number="SVP 167", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
    ]
    with patch("automation_control.capture.extract_card_details", return_value=cards):
        resp = client.post(
            "/cards/capture",
            files={"front": ("front.jpg", io.BytesIO(b"fake-front"), "image/jpeg")},
            follow_redirects=False,
        )

    # more than one card detected -> redirect to the queue, not a single review page
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cards/review"

    saved = session.query(CapturedCard).order_by(CapturedCard.character).all()
    assert len(saved) == 2
    assert {c.character for c in saved} == {"Zapdos ex", "Flareon"}
    # both rows point at the same shared photo
    assert saved[0].front_image_path == saved[1].front_image_path


def test_capture_no_card_detected_creates_blank_row_for_manual_entry(client, session):
    with patch("automation_control.capture.extract_card_details", return_value=[]):
        resp = client.post(
            "/cards/capture",
            files={"front": ("front.jpg", io.BytesIO(b"fake-front"), "image/jpeg")},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    card_id = resp.headers["location"].rsplit("/", 1)[-1]
    card = session.get(CapturedCard, card_id)
    assert card.character is None
    assert card.ai_raw_response == {"note": "no card detected in photo"}
    assert card.status == CardCaptureStatus.PENDING_REVIEW


def test_bulk_capture_counts_multiple_cards_per_photo(client, session):
    single_card = [ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])]
    group_cards = [
        ExtractedCard(character="Zapdos ex", set_name="Promo", card_number="SVP 049", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
        ExtractedCard(character="Flareon", set_name="Promo", card_number="SVP 167", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
        ExtractedCard(character="Tornadus", set_name="Promo", card_number="SVP 210", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
    ]
    with patch("automation_control.capture.extract_card_details", side_effect=[single_card, group_cards]):
        resp = client.post(
            "/cards/capture/bulk",
            files=[
                ("photos", ("single.jpg", io.BytesIO(b"one"), "image/jpeg")),
                ("photos", ("group.jpg", io.BytesIO(b"group"), "image/jpeg")),
            ],
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert session.query(CapturedCard).count() == 4
    # the batch row's attributes were cached in this session by the request
    # above, before the background task updated them via its own session --
    # expire so the status-page fetch below re-reads the current row.
    session.expire_all()
    status_resp = client.get(resp.headers["location"])
    assert status_resp.status_code == 200
    assert "4 card(s) captured from 2 photo(s)" in status_resp.text


def test_bulk_capture_status_page_shows_progress_while_running(client, session):
    from automation_control.models import CaptureBatch, CaptureBatchStatus

    batch = CaptureBatch(id="batch1", total_photos=5, processed_photos=2, cards_created=2, status=CaptureBatchStatus.RUNNING, started_by="testuser")
    session.add(batch)
    session.commit()

    resp = client.get("/cards/capture/bulk/status/batch1")
    assert resp.status_code == 200
    assert "2 of 5" in resp.text
    assert "refresh" in resp.text.lower()


def test_bulk_capture_status_page_404_for_unknown_batch(client):
    resp = client.get("/cards/capture/bulk/status/does-not-exist")
    assert resp.status_code == 404


def test_bulk_capture_one_photo_db_failure_does_not_abort_the_rest(client, session):
    # Regression test: a DB-level failure on one photo (e.g. a schema
    # mismatch) must not crash the whole request and lose every remaining
    # photo in the batch -- this happened for real when back_image_path was
    # still NOT NULL in the live database after the column was made optional.
    good = [ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])]
    with patch("automation_control.capture.extract_card_details", return_value=good), \
         patch("automation_control.capture._save_upload", side_effect=[Path("/tmp/f1.jpg"), RuntimeError("disk full"), Path("/tmp/f2.jpg")]):
        resp = client.post(
            "/cards/capture/bulk",
            files=[
                ("photos", ("ok1.jpg", io.BytesIO(b"one"), "image/jpeg")),
                ("photos", ("broken.jpg", io.BytesIO(b"two"), "image/jpeg")),
                ("photos", ("ok2.jpg", io.BytesIO(b"three"), "image/jpeg")),
            ],
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert session.query(CapturedCard).count() == 2
    session.expire_all()
    status_resp = client.get(resp.headers["location"])
    assert status_resp.status_code == 200
    assert "2 card(s) captured from 3 photo(s)" in status_resp.text
    assert "1 photo(s) failed" in status_resp.text
    assert "broken.jpg" in status_resp.text


def test_capture_unreadable_field_stored_as_none_not_placeholder(client, session):
    fake = ExtractedCard(character="Pikachu", set_name="hard to tell", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=["set_name"])
    with patch("automation_control.capture.extract_card_details", return_value=[fake]):
        resp = client.post("/cards/capture", files=_upload_files(), follow_redirects=False)

    card_id = resp.headers["location"].rsplit("/", 1)[-1]
    card = session.get(CapturedCard, card_id)
    assert card.set_name is None
    assert card.character == "Pikachu"


def test_capture_ai_failure_still_creates_record_for_manual_entry(client, session):
    with patch("automation_control.capture.extract_card_details", side_effect=RuntimeError("boom")):
        resp = client.post("/cards/capture", files=_upload_files(), follow_redirects=False)

    assert resp.status_code == 303
    card_id = resp.headers["location"].rsplit("/", 1)[-1]
    card = session.get(CapturedCard, card_id)
    assert card.character is None
    assert card.ai_raw_response == {"error": "boom"}
    assert card.status == CardCaptureStatus.PENDING_REVIEW


def test_capture_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/cards/capture", files=_upload_files())
    assert resp.status_code == 503


def test_review_list_shows_only_pending(client, session):
    session.add_all([
        CapturedCard(id="p1", front_image_path="f", back_image_path="b", character="Pending Card", status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={}),
        CapturedCard(id="r1", front_image_path="f", back_image_path="b", character="Reviewed Card", status=CardCaptureStatus.REVIEWED, ai_raw_response={}),
    ])
    session.commit()

    resp = client.get("/cards/review")
    assert resp.status_code == 200
    assert "Pending Card" in resp.text
    assert "Reviewed Card" not in resp.text


def test_review_submit_marks_reviewed_and_updates_fields(client, session):
    card = CapturedCard(id="abc123", front_image_path="f", back_image_path="b", character="Pikachu", status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={})
    session.add(card)
    session.commit()

    resp = client.post(
        "/cards/review/abc123",
        data={"character": "Pikachu", "set_name": "Base Set", "card_number": "58/102", "rarity": "Common", "language": "English", "graded": "Raw"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    session.refresh(card)
    assert card.status == CardCaptureStatus.REVIEWED
    assert card.set_name == "Base Set"
    assert card.reviewed_by == "testuser"
    assert card.reviewed_at is not None


def test_review_form_404_for_unknown_card(client):
    resp = client.get("/cards/review/does-not-exist")
    assert resp.status_code == 404


def test_card_image_404_for_unknown_side(client, session):
    card = CapturedCard(id="abc123", front_image_path="f", back_image_path="b", status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={})
    session.add(card)
    session.commit()
    resp = client.get("/cards/image/abc123/sideways")
    assert resp.status_code == 404


def test_card_image_404_when_file_missing(client, session, tmp_path):
    card = CapturedCard(id="abc123", front_image_path=str(tmp_path / "missing.jpg"), back_image_path=str(tmp_path / "missing2.jpg"), status=CardCaptureStatus.PENDING_REVIEW, ai_raw_response={})
    session.add(card)
    session.commit()
    resp = client.get("/cards/image/abc123/front")
    assert resp.status_code == 404


def test_dashboard_photos_needed_tile_reflects_checklist_row_count(client, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "image_naming_checklist.csv").write_text(
        "Listing ID,Listing,Image filename,What to shoot\n"
        "01,Some Title,01-1.jpg,Card FRONT\n"
        "01,Some Title,01-2.jpg,Card BACK\n"
        "08,Bundle Title,08-1.jpg,Card A\n"
    )
    app.state.settings.listing_pipeline_output_dir = str(output_dir)

    resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "Photos needed" in resp.text
    assert "<div class='value'>3</div>" in resp.text


def test_dashboard_photos_needed_tile_zero_with_no_output(client, tmp_path):
    app.state.settings.listing_pipeline_output_dir = str(tmp_path / "no_output_here")
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Photos needed" in resp.text
