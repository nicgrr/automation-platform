import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCollectible
from automation_control.database import Base, get_session
from automation_control.models import CapturedCollectible, CardCaptureStatus, CatalogItem, CollectibleProduct, InventoryItem


@pytest.fixture
def engine(tmp_path):
    # File-based, not :memory:: bulk capture's background task opens its own
    # DB session on its own connection (from a different thread), same
    # reason test_card_capture_routes.py uses a real file.
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
    with patch("automation_control.collectibles.SessionLocal", test_session_factory):
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


def test_capture_page_allows_choosing_from_photo_library(client):
    # Regression: capture='environment' forces mobile browsers straight
    # into the camera, hiding the "choose from library" option entirely.
    resp = client.get("/collectibles/capture")
    assert "capture=" not in resp.text


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


def test_capture_db_failure_returns_a_controlled_error_not_a_crash(client, session):
    """Same failure mode as accessories.py, same fix: an AI-call failure is
    already caught cleanly, but the fallback row it produces can still hit
    its own DB error on commit, and until now that second failure went
    uncaught -- a raw 500 with nothing in the audit log, and a session left
    dead for every request after it."""
    with patch("automation_control.collectibles.extract_collectible_details", return_value=[]), \
         patch.object(session, "commit", side_effect=Exception("database is locked")):
        resp = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    assert resp.status_code == 500
    assert "failed to save this capture" in resp.json()["detail"]
    assert session.query(CapturedCollectible).count() == 0

    with patch("automation_control.collectibles.extract_collectible_details", return_value=[]):
        resp2 = client.post("/collectibles/capture", files=_upload(), follow_redirects=False)
    assert resp2.status_code == 303


def test_capture_records_the_real_failure_reason_in_the_audit_log(client, session):
    from sqlalchemy import select
    from automation_control.models import AuditEvent

    with patch("automation_control.collectibles.extract_collectible_details", side_effect=RuntimeError("boom")):
        client.post("/collectibles/capture", files=_upload(), follow_redirects=False)

    event = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "collectible.capture")
    ).one()
    assert event.outcome == "failed"
    assert event.details == {"error": "boom"}


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
    # confirm advances through the queue rather than dumping you back on
    # the collectibles page -- with nothing else pending it lands on the
    # queue entry point, which renders the "queue is empty" page
    assert resp.headers["location"] == "/collectibles/review"

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
    # the crop taken during capture IS a photo of the actual figure --
    # carrying it forward is what lets the shop show it instead of
    # falling back to "no image" the way an unphotographed item would
    assert inventory_item.scan_image_path == "f"


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


def test_bulk_capture_page_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/collectibles/capture/bulk")
    assert resp.status_code == 401


def test_bulk_capture_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/collectibles/capture/bulk", files=[("photos", ("f1.jpg", io.BytesIO(b"one"), "image/jpeg"))])
    assert resp.status_code == 503


def test_bulk_capture_creates_one_row_per_photo(client, session):
    fake = ExtractedCollectible(brand="Sonny Angel", series="Fruits", character="Peach", variant="", is_secret=False, blind_box_series="", unreadable_fields=[])
    with patch("automation_control.collectibles.extract_collectible_details", return_value=[fake]):
        resp = client.post(
            "/collectibles/capture/bulk",
            files=[
                ("photos", ("f1.jpg", io.BytesIO(b"one"), "image/jpeg")),
                ("photos", ("f2.jpg", io.BytesIO(b"two"), "image/jpeg")),
                ("photos", ("f3.jpg", io.BytesIO(b"three"), "image/jpeg")),
            ],
            follow_redirects=False,
        )

    # background task runs to completion within the TestClient call, so by
    # the time we get the redirect, processing has already finished.
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/collectibles/capture/bulk/status/")
    rows = session.query(CapturedCollectible).all()
    assert len(rows) == 3
    assert all(r.status == CardCaptureStatus.PENDING_REVIEW for r in rows)

    session.expire_all()
    status_resp = client.get(resp.headers["location"])
    assert status_resp.status_code == 200
    assert "3 figure(s) captured from 3 photo(s)" in status_resp.text
    assert "Done" in status_resp.text


def test_bulk_capture_one_photo_failure_does_not_abort_the_rest(client, session):
    fake = ExtractedCollectible(brand="Sonny Angel", series="Fruits", character="Peach", variant="", is_secret=False, blind_box_series="", unreadable_fields=[])
    with patch("automation_control.collectibles.extract_collectible_details", side_effect=[[fake], RuntimeError("boom"), [fake]]):
        resp = client.post(
            "/collectibles/capture/bulk",
            files=[
                ("photos", ("ok1.jpg", io.BytesIO(b"one"), "image/jpeg")),
                ("photos", ("broken.jpg", io.BytesIO(b"two"), "image/jpeg")),
                ("photos", ("ok2.jpg", io.BytesIO(b"three"), "image/jpeg")),
            ],
            follow_redirects=False,
        )

    assert resp.status_code == 303
    # the "broken" photo still gets a blank row -- extract_collectible_details
    # raising is handled inside _process_collectible_photo, not treated as a
    # batch-level save failure (that path is only for disk-write errors).
    assert session.query(CapturedCollectible).count() == 3
    session.expire_all()
    status_resp = client.get(resp.headers["location"])
    assert "3 figure(s) captured from 3 photo(s)" in status_resp.text


def test_bulk_capture_status_page_404_for_unknown_batch(client):
    resp = client.get("/collectibles/capture/bulk/status/does-not-exist")
    assert resp.status_code == 404


def test_bulk_capture_status_page_shows_progress_while_running(client, session):
    from automation_control.models import CaptureBatch, CaptureBatchStatus

    batch = CaptureBatch(id="batch1", total_photos=5, processed_photos=2, cards_created=2, status=CaptureBatchStatus.RUNNING, started_by="testuser")
    session.add(batch)
    session.commit()

    resp = client.get("/collectibles/capture/bulk/status/batch1")
    assert resp.status_code == 200
    assert "2 of 5" in resp.text
    assert "refresh" in resp.text.lower()


# --- one-by-one review queue ------------------------------------------------

def _pending_capture(session, character="Peach", **kw):
    from automation_control.models import CapturedCollectible, CardCaptureStatus

    capture = CapturedCollectible(
        image_path="captured_cards/collectibles/x.jpg", brand="Sonny Angel",
        character=character, status=CardCaptureStatus.PENDING_REVIEW, **kw,
    )
    session.add(capture)
    session.commit()
    return capture


def test_review_entry_point_goes_straight_to_the_first_figure(client, session):
    """Reviewing forty figures only works if you don't have to go via a
    list between each one."""
    first = _pending_capture(session, "Peach")
    _pending_capture(session, "Banana")

    resp = client.get("/collectibles/review", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/collectibles/review/{first.id}"


def test_review_entry_point_reports_an_empty_queue(client, session):
    resp = client.get("/collectibles/review", follow_redirects=False)
    assert resp.status_code == 200
    assert "Queue is empty" in resp.text


def test_confirm_advances_to_the_next_pending_figure(client, session):
    first = _pending_capture(session, "Peach")
    second = _pending_capture(session, "Banana")

    resp = client.post(f"/collectibles/review/{first.id}",
                       data={"action": "confirm", "brand": "Sonny Angel",
                             "character": "Peach", "quantity": "1"},
                       follow_redirects=False)
    assert resp.headers["location"] == f"/collectibles/review/{second.id}"


def test_discard_also_advances(client, session):
    first = _pending_capture(session, "Peach")
    second = _pending_capture(session, "Banana")

    resp = client.post(f"/collectibles/review/{first.id}",
                       data={"action": "reject"}, follow_redirects=False)
    assert resp.headers["location"] == f"/collectibles/review/{second.id}"


def test_skip_advances_without_deciding_anything(client, session):
    """A skip is "not now", not a decision -- the row must stay pending."""
    from automation_control.models import CapturedCollectible, CardCaptureStatus

    first = _pending_capture(session, "Peach")
    second = _pending_capture(session, "Banana")

    resp = client.post(f"/collectibles/review/{first.id}",
                       data={"action": "skip"}, follow_redirects=False)
    assert resp.headers["location"] == f"/collectibles/review/{second.id}"

    session.expire_all()
    assert session.get(CapturedCollectible, first.id).status == CardCaptureStatus.PENDING_REVIEW
    assert session.get(CapturedCollectible, first.id).reviewed_at is None


def test_a_skipped_figure_comes_back_round_rather_than_being_stranded(client, session):
    first = _pending_capture(session, "Peach")
    second = _pending_capture(session, "Banana")

    # skip the last one in the queue -- it should wrap to the first
    resp = client.post(f"/collectibles/review/{second.id}",
                       data={"action": "skip"}, follow_redirects=False)
    assert resp.headers["location"] == f"/collectibles/review/{first.id}"


def test_review_page_shows_position_in_the_queue(client, session):
    first = _pending_capture(session, "Peach")
    _pending_capture(session, "Banana")
    _pending_capture(session, "Melon")

    body = client.get(f"/collectibles/review/{first.id}").text
    assert "Figure 1 of 3" in body
    assert "Skip for now" in body
    assert "Confirm &amp; next" in body


def test_the_full_queue_is_still_listable(client, session):
    _pending_capture(session, "Peach")
    body = client.get("/collectibles/review/all").text
    assert "Peach" in body
    assert "1 awaiting review" in body


def test_cursor_starts_in_the_field_the_ai_left_blank(client, session):
    blank = _pending_capture(session, character=None)
    named = _pending_capture(session, character="Peach")

    assert "autofocus" in client.get(f"/collectibles/review/{blank.id}").text
    # a figure the AI already named doesn't steal focus into a filled field
    assert "autofocus" not in client.get(f"/collectibles/review/{named.id}").text


# --- catalogue list: sorting and eBay prices --------------------------------

def _collectible(session, name, *, brand="Sonny Angel", series=None, secret=False,
                 low=None, median=None, owned=0):
    import uuid as _uuid
    from decimal import Decimal

    from automation_control.models import (
        CardPrice, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
    )

    item_id = str(_uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(catalog_item_id=item_id, brand=brand, series=series,
                                   is_secret=secret))
    for source, value in (("ebay_active_low", low), ("ebay_active_median", median)):
        if value is not None:
            session.add(CardPrice(catalog_item_id=item_id, variant=None, source=source,
                                  price=Decimal(str(value)), currency="AUD"))
    if owned:
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, quantity=owned))
    session.commit()
    return item_id


def _row_order(body: str, names: list[str]) -> list[str]:
    seen = [(body.index(n), n) for n in names if n in body]
    return [n for _, n in sorted(seen)]


def test_list_shows_ebay_low_and_median(client, session):
    _collectible(session, "Fruit Secret", low=60.60, median=421.09)
    body = client.get("/collectibles?show=all").text
    assert "$60.60" in body and "$421.09" in body
    assert "eBay low" in body and "eBay median" in body


def test_list_labels_ebay_figures_as_asking_prices(client, session):
    """These are active listings, not sold comps -- the page must not let
    them read as a valuation."""
    _collectible(session, "Fruit Secret", median=421.09)
    body = client.get("/collectibles").text
    assert "active listing" in body.lower()
    assert "not completed sales" in body.lower()


def test_sort_by_ebay_price_descending(client, session):
    _collectible(session, "Cheap One", median=20.00)
    _collectible(session, "Dear One", median=400.00)
    _collectible(session, "Middle One", median=90.00)
    body = client.get("/collectibles?show=all&sort=ebay_desc").text
    assert _row_order(body, ["Dear One", "Middle One", "Cheap One"]) == [
        "Dear One", "Middle One", "Cheap One"]


def test_sort_by_ebay_price_ascending(client, session):
    _collectible(session, "Cheap One", median=20.00)
    _collectible(session, "Dear One", median=400.00)
    body = client.get("/collectibles?show=all&sort=ebay_asc").text
    assert _row_order(body, ["Cheap One", "Dear One"]) == ["Cheap One", "Dear One"]


def test_unpriced_items_sort_last_not_as_zero(client, session):
    """Treating "no price" as $0 would put every unpriced figure at the top
    of "low to high", burying the ones you can actually act on."""
    _collectible(session, "Unpriced One")
    _collectible(session, "Cheap One", median=20.00)
    body = client.get("/collectibles?show=all&sort=ebay_asc").text
    assert _row_order(body, ["Cheap One", "Unpriced One"]) == ["Cheap One", "Unpriced One"]


def test_sort_by_most_owned(client, session):
    _collectible(session, "One Copy", owned=1)
    _collectible(session, "Five Copies", owned=5)
    body = client.get("/collectibles?sort=owned_desc").text
    assert _row_order(body, ["Five Copies", "One Copy"]) == ["Five Copies", "One Copy"]


def test_default_view_shows_only_owned_figures(client, session):
    """Nic: "just have my items that I own" -- the default landing view,
    not a filter you have to apply."""
    _collectible(session, "Not Owned Yet", owned=0)
    _collectible(session, "Owned One", owned=1)
    body = client.get("/collectibles").text
    assert "Owned One" in body
    assert "Not Owned Yet" not in body


def test_show_all_still_reaches_the_full_catalogue(client, session):
    _collectible(session, "Not Owned Yet", owned=0)
    body = client.get("/collectibles?show=all").text
    assert "Not Owned Yet" in body


def test_owned_figure_with_a_photo_shows_a_thumbnail(client, session, tmp_path):
    from PIL import Image

    from automation_control.models import InventoryItem

    photo_path = tmp_path / "figure.jpg"
    Image.new("RGB", (400, 400), (10, 20, 30)).save(photo_path, "JPEG")
    item_id = _collectible(session, "Photographed Figure", owned=1)
    item = session.query(InventoryItem).filter_by(catalog_item_id=item_id).one()
    item.scan_image_path = str(photo_path)
    session.commit()

    body = client.get("/collectibles").text
    assert f"/collectibles/thumb/{item.id}" in body

    thumb = client.get(f"/collectibles/thumb/{item.id}")
    assert thumb.status_code == 200


def test_owned_figure_with_no_photo_falls_back_to_text(client, session):
    _collectible(session, "No Photo Figure", owned=1)
    body = client.get("/collectibles").text
    assert "No Photo Figure" in body
    assert "/collectibles/thumb/" not in body


def test_thumb_404s_for_an_item_with_no_photo(client, session):
    from automation_control.models import InventoryItem

    item_id = _collectible(session, "No Photo Figure", owned=1)
    item = session.query(InventoryItem).filter_by(catalog_item_id=item_id).one()
    assert client.get(f"/collectibles/thumb/{item.id}").status_code == 404


def test_thumb_404s_for_an_unknown_item(client):
    assert client.get("/collectibles/thumb/does-not-exist").status_code == 404


def test_secrets_first_sort(client, session):
    _collectible(session, "Regular Figure", secret=False)
    _collectible(session, "Secret Figure", secret=True)
    body = client.get("/collectibles?show=all&sort=secret_first").text
    assert _row_order(body, ["Secret Figure", "Regular Figure"]) == [
        "Secret Figure", "Regular Figure"]


def test_filter_by_brand(client, session):
    _collectible(session, "Angel Figure", brand="Sonny Angel")
    _collectible(session, "Smiski Figure", brand="Smiski")
    body = client.get("/collectibles?show=all&brand=Smiski").text
    assert "Smiski Figure" in body
    assert "Angel Figure" not in body


def test_filter_to_secrets_only(client, session):
    _collectible(session, "Regular Figure", secret=False)
    _collectible(session, "Secret Figure", secret=True)
    body = client.get("/collectibles?show=all&secret_only=1").text
    assert "Secret Figure" in body
    assert "Regular Figure" not in body


def test_an_unknown_sort_falls_back_to_name(client, session):
    _collectible(session, "Alpha")
    _collectible(session, "Beta")
    body = client.get("/collectibles?show=all&sort=nonsense").text
    assert _row_order(body, ["Alpha", "Beta"]) == ["Alpha", "Beta"]


def test_the_newest_ebay_price_wins(client, session):
    """card_prices is append-only, so a re-priced item has several rows."""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from sqlalchemy import select

    from automation_control.models import CardPrice

    item_id = _collectible(session, "Repriced", median=100.00)
    old = session.scalars(select(CardPrice).where(CardPrice.catalog_item_id == item_id)).first()
    old.fetched_at = datetime.now(UTC) - timedelta(days=2)
    session.add(CardPrice(catalog_item_id=item_id, variant=None, source="ebay_active_median",
                          price=Decimal("250.00"), currency="AUD"))
    session.commit()

    body = client.get("/collectibles?show=all").text
    assert "$250.00" in body
    assert "$100.00" not in body


# --- filling a pending capture from a product link --------------------------

def _wire_link(monkeypatch, extracted, html="<h1>Sonny Angel</h1>"):
    from automation_control.config import get_settings

    monkeypatch.setattr("automation_control.link_fetch.fetch_text", lambda url, **k: html)
    monkeypatch.setattr("automation_control.card_recognition.extract_collectible_listing",
                        lambda text, api_key, **k: extracted)
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr("automation_control.collectibles.get_settings", lambda: settings)


def _listing(**kw):
    from automation_control.card_recognition import ExtractedCollectibleListing

    values = dict(brand="Sonny Angel", series="Summer Vacation Series", character="",
                  variant="Single Box", is_secret=False, quantity=1, is_sealed=True,
                  asking_price=29.95, currency="AUD", unreadable_fields=["character"])
    values.update(kw)
    return ExtractedCollectibleListing(**values)


def test_the_review_form_offers_to_fill_from_a_link(client, session):
    capture = _pending_capture(session, character=None)
    body = client.get(f"/collectibles/review/{capture.id}").text
    assert "Fill from a product or listing link" in body
    assert f"/collectibles/review/{capture.id}/from-link" in body


def test_a_link_fills_the_blank_fields(client, session, monkeypatch):
    from automation_control.models import CapturedCollectible

    capture = _pending_capture(session, character=None, series=None)
    _wire_link(monkeypatch, [_listing()])

    resp = client.post(f"/collectibles/review/{capture.id}/from-link",
                       data={"url": "https://example.com/p/1"}, follow_redirects=False)
    assert resp.status_code == 303

    session.expire_all()
    filled = session.get(CapturedCollectible, capture.id)
    assert filled.series == "Summer Vacation Series"
    assert filled.variant == "Single Box"


def test_a_sealed_listing_sets_the_blind_box_series(client, session, monkeypatch):
    """A listing has no separate blind-box field; for a sealed box the
    series is the box line, which is the only case where it's known."""
    from automation_control.models import CapturedCollectible

    capture = _pending_capture(session, character=None)
    _wire_link(monkeypatch, [_listing(is_sealed=True, series="Summer Vacation Series")])
    client.post(f"/collectibles/review/{capture.id}/from-link",
                data={"url": "https://example.com/p/1"}, follow_redirects=False)

    session.expire_all()
    assert session.get(CapturedCollectible, capture.id).blind_box_series == "Summer Vacation Series"


def test_a_loose_figure_listing_does_not_set_a_blind_box_series(client, session, monkeypatch):
    from automation_control.models import CapturedCollectible

    capture = _pending_capture(session, character=None)
    _wire_link(monkeypatch, [_listing(is_sealed=False)])
    client.post(f"/collectibles/review/{capture.id}/from-link",
                data={"url": "https://example.com/p/1"}, follow_redirects=False)

    session.expire_all()
    assert session.get(CapturedCollectible, capture.id).blind_box_series is None


def test_a_field_the_link_was_unsure_about_is_left_alone(client, session, monkeypatch):
    """A link that only names the series must not wipe a character the
    photo already got right."""
    from automation_control.models import CapturedCollectible

    capture = _pending_capture(session, character="Peach")
    _wire_link(monkeypatch, [_listing(character="", unreadable_fields=["character"])])
    client.post(f"/collectibles/review/{capture.id}/from-link",
                data={"url": "https://example.com/p/1"}, follow_redirects=False)

    session.expire_all()
    assert session.get(CapturedCollectible, capture.id).character == "Peach"


def test_filling_from_a_link_never_touches_the_catalogue(client, session, monkeypatch):
    from sqlalchemy import func, select

    from automation_control.models import CatalogItem

    capture = _pending_capture(session, character=None)
    before = session.scalar(select(func.count()).select_from(CatalogItem))
    _wire_link(monkeypatch, [_listing()])
    client.post(f"/collectibles/review/{capture.id}/from-link",
                data={"url": "https://example.com/p/1"}, follow_redirects=False)
    assert session.scalar(select(func.count()).select_from(CatalogItem)) == before


def test_the_capture_stays_pending_after_a_fill(client, session, monkeypatch):
    from automation_control.models import CapturedCollectible, CardCaptureStatus

    capture = _pending_capture(session, character=None)
    _wire_link(monkeypatch, [_listing()])
    client.post(f"/collectibles/review/{capture.id}/from-link",
                data={"url": "https://example.com/p/1"}, follow_redirects=False)

    session.expire_all()
    assert session.get(CapturedCollectible, capture.id).status == CardCaptureStatus.PENDING_REVIEW


def test_an_unsafe_link_is_reported_on_the_form(client, session, monkeypatch):
    capture = _pending_capture(session, character=None)

    def refuse(url, **k):
        from automation_control.link_fetch import UnsafeUrl
        raise UnsafeUrl("that link points somewhere internal, so it wasn't fetched")

    monkeypatch.setattr("automation_control.link_fetch.fetch_text", refuse)
    resp = client.post(f"/collectibles/review/{capture.id}/from-link",
                       data={"url": "http://127.0.0.1:8080/"}, follow_redirects=True)
    assert "somewhere internal" in resp.text


def test_a_page_with_nothing_usable_says_so(client, session, monkeypatch):
    capture = _pending_capture(session, character=None)
    _wire_link(monkeypatch, [])
    resp = client.post(f"/collectibles/review/{capture.id}/from-link",
                       data={"url": "https://example.com/p/1"}, follow_redirects=True)
    # the apostrophe is HTML-escaped in the rendered note, as it should be
    assert "look like a listing" in resp.text


# --- series name normalisation ----------------------------------------------

def test_series_names_are_normalised_to_one_form():
    """Names have to match exactly to group or price together, and the
    model returns whatever the packaging says."""
    from scripts.identify_pending_collectibles import normalise_series

    assert normalise_series("Summer Vacation") == "Summer Vacation Series"
    assert normalise_series("Summer Vacation Series") == "Summer Vacation Series"
    assert normalise_series("CRYBABY") == "Crybaby"
    assert normalise_series("  fruit series  ") == "Fruit Series"


def test_an_unknown_series_is_kept_as_written():
    from scripts.identify_pending_collectibles import normalise_series

    assert normalise_series("Sleeping Angel Series") == "Sleeping Angel Series"


def test_a_series_that_says_nothing_becomes_none():
    from scripts.identify_pending_collectibles import normalise_series

    for value in ["", "   ", None, "Unknown", "n/a", "-"]:
        assert normalise_series(value) is None, value


def test_capture_image_404_when_file_is_gone(client, session, tmp_path):
    """Was a 500: the handler passed image_path straight to FileResponse
    with no existence check, so a photo moved or deleted off disk raised
    an unhandled error instead of a missing image. Every other image
    endpoint in the app already checked; this one didn't."""
    capture = CapturedCollectible(
        id="gone-1",
        image_path=str(tmp_path / "never-written.jpg"),
        status=CardCaptureStatus.PENDING_REVIEW,
    )
    session.add(capture)
    session.commit()

    assert client.get("/collectibles/capture/image/gone-1").status_code == 404


def test_capture_image_is_privately_cached(client, session, tmp_path):
    """These sit behind dashboard auth, so a shared proxy must not be
    allowed to keep a copy."""
    photo = tmp_path / "figure.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xd9")
    session.add(CapturedCollectible(
        id="have-1", image_path=str(photo), status=CardCaptureStatus.PENDING_REVIEW,
    ))
    session.commit()

    resp = client.get("/collectibles/capture/image/have-1")
    assert resp.status_code == 200
    assert resp.headers["cache-control"].startswith("private")
