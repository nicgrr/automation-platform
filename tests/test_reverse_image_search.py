import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CapturedCollectible, CardCaptureStatus
from automation_control.reverse_image_search import ReverseImageSearchError, search_image


# --- search_image: the Apify HTTP call itself ------------------------------

@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "figure.jpg"
    path.write_bytes(b"fake-jpeg-bytes")
    return path


@pytest.mark.asyncio
async def test_search_image_sends_the_photo_as_base64_not_a_url(photo):
    """This app is Tailscale-only -- Apify's cloud actor can never fetch an
    image back from a URL here, so the photo must go in the request body,
    not be referenced by a link it can't reach."""
    captured = {}

    async def fake_post(self, url, params=None, json=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [{"title": "A Figure", "link": "https://example.test/a"}]
        return response

    with patch("httpx.AsyncClient.post", new=fake_post):
        items = await search_image(photo, token="tok", actor_id="borderline~google-lens")

    assert items == [{"title": "A Figure", "link": "https://example.test/a"}]
    assert captured["url"] == "https://api.apify.com/v2/actors/borderline~google-lens/run-sync-get-dataset-items"
    assert captured["params"] == {"token": "tok"}
    assert "imageUrls" not in captured["json"]
    encoded = captured["json"]["imagesBase64"][0]
    assert encoded.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(encoded.split(",", 1)[1]) == b"fake-jpeg-bytes"


@pytest.mark.asyncio
async def test_search_image_raises_a_clear_error_on_a_failed_run(photo):
    async def fake_post(self, url, params=None, json=None):
        response = MagicMock()
        response.status_code = 402
        response.text = "insufficient balance"
        return response

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(ReverseImageSearchError, match="402"):
            await search_image(photo, token="tok", actor_id="borderline~google-lens")


@pytest.mark.asyncio
async def test_search_image_raises_on_a_network_failure(photo):
    import httpx

    async def fake_post(self, url, params=None, json=None):
        raise httpx.ConnectError("no route to host")

    with patch("httpx.AsyncClient.post", new=fake_post):
        with pytest.raises(ReverseImageSearchError, match="could not reach Apify"):
            await search_image(photo, token="tok", actor_id="borderline~google-lens")


# --- the review-page route: caches results, never touches the catalogue ----

@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, tmp_path):
    original_dir = app.state.settings.captured_cards_dir
    original_token = app.state.settings.apify_token
    app.state.settings.captured_cards_dir = str(tmp_path / "captured_cards")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.captured_cards_dir = original_dir
    app.state.settings.apify_token = original_token


def _make_capture(session, tmp_path) -> str:
    photo = tmp_path / "capture.jpg"
    photo.write_bytes(b"fake-bytes")
    capture = CapturedCollectible(id="cap-1", image_path=str(photo), status=CardCaptureStatus.PENDING_REVIEW)
    session.add(capture)
    session.commit()
    return capture.id


def test_route_503_style_message_when_apify_is_not_configured(client, session, tmp_path):
    app.state.settings.apify_token = None
    capture_id = _make_capture(session, tmp_path)

    resp = client.post(f"/collectibles/review/{capture_id}/reverse-image-search", follow_redirects=False)
    assert resp.status_code == 303

    page = client.get(f"/collectibles/review/{capture_id}")
    assert "not configured" in page.text


def test_route_caches_results_and_the_review_page_renders_them(client, session, tmp_path):
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    fake_items = [{"title": "Sonny Angel Cherry", "link": "https://example.test/product", "thumbnail": "https://example.test/thumb.jpg"}]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        resp = client.post(f"/collectibles/review/{capture_id}/reverse-image-search", follow_redirects=False)
    assert resp.status_code == 303

    page = client.get(f"/collectibles/review/{capture_id}")
    assert "Sonny Angel Cherry" in page.text
    assert "https://example.test/product" in page.text
    assert "data-url='https://example.test/product'" in page.text


def test_route_never_writes_to_the_catalogue(client, session, tmp_path):
    """A Lens result is a link for a human to read, not structured fields
    -- unlike from-link, this must never mutate the pending capture row."""
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    fake_items = [{"title": "Something", "link": "https://example.test/x"}]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post(f"/collectibles/review/{capture_id}/reverse-image-search")

    session.expire_all()
    capture = session.get(CapturedCollectible, capture_id)
    assert capture.brand is None
    assert capture.character is None


def test_route_records_a_clear_error_when_the_search_fails(client, session, tmp_path):
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    with patch("automation_control.collectibles.search_image", new=AsyncMock(side_effect=ReverseImageSearchError("Apify run failed (402): insufficient balance"))):
        client.post(f"/collectibles/review/{capture_id}/reverse-image-search")

    page = client.get(f"/collectibles/review/{capture_id}")
    assert "Google Lens search failed" in page.text
    assert "402" in page.text


def test_unconfirmed_route_404s_for_an_unknown_capture(client):
    resp = client.post("/collectibles/review/does-not-exist/reverse-image-search")
    assert resp.status_code == 404
