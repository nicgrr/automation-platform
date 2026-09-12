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

    assert [item["link"] for item in items] == ["https://example.test/a"]
    assert captured["url"] == "https://api.apify.com/v2/actors/borderline~google-lens/run-sync-get-dataset-items"
    assert captured["params"] == {"token": "tok"}
    assert "imageUrls" not in captured["json"]
    encoded = captured["json"]["imagesBase64"][0]
    assert encoded.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(encoded.split(",", 1)[1]) == b"fake-jpeg-bytes"


# The shape below is copied from a real `borderline~google-lens` dataset
# (run rhBOdm9akFHi1TX3k): one item per *image*, matches nested under the
# key named by searchType. Every test here used to hand the code a flat
# list it never actually receives, which is exactly why the feature could
# ship returning nothing but nulls.
REAL_DATASET_ITEM = {
    "searchType": "visual-match",
    "visual-match": {
        "inputUrl": "base64:0",
        "results": [
            {"position": 1, "title": "Sonny Angel Flower Series Lily Bell Figure",
             "link": "https://www.ebay.com/itm/127296897043", "thumbnail": "data:image/jpeg;base64,AAAA",
             "source": "ebay.com", "price": "$24.00"},
            {"position": 2, "title": "Sonny Angel Flower Series Blind Box",
             "link": "https://whoopea.com/products/sonny-angel-flower-series", "thumbnail": "https://cdn.test/t.jpg",
             "source": "whoopea.com"},
        ],
    },
}


async def _run_with_dataset(photo, dataset_items):
    async def fake_post(self, url, params=None, json=None):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = dataset_items
        return response

    with patch("httpx.AsyncClient.post", new=fake_post):
        return await search_image(photo, token="tok", actor_id="borderline~google-lens")


@pytest.mark.asyncio
async def test_search_image_flattens_the_per_image_dataset_item_into_matches(photo):
    """The regression this guards: the actor returns one dataset item per
    image with the matches nested a level down. Treating an item as a
    match yields a single all-null row, and the review page then reports
    that Lens "found nothing usable" for a photo it matched correctly."""
    matches = await _run_with_dataset(photo, [REAL_DATASET_ITEM])

    assert len(matches) == 2
    assert matches[0]["title"] == "Sonny Angel Flower Series Lily Bell Figure"
    assert matches[0]["link"] == "https://www.ebay.com/itm/127296897043"
    assert matches[0]["source"] == "ebay.com"
    assert matches[0]["price"] == "$24.00"
    assert matches[1]["thumbnail"] == "https://cdn.test/t.jpg"


@pytest.mark.asyncio
async def test_search_image_still_accepts_an_already_flat_match(photo):
    """A different actor version shouldn't silently break this again."""
    matches = await _run_with_dataset(photo, [{"title": "Flat", "url": "https://example.test/flat"}])
    assert matches == [{"title": "Flat", "link": "https://example.test/flat", "thumbnail": None,
                        "source": None, "price": None}]


@pytest.mark.asyncio
async def test_search_image_drops_matches_with_no_usable_or_unsafe_link(photo):
    """Titles and links here are scraped from third-party pages, so they
    are untrusted text going straight into an href."""
    dataset = [{"searchType": "visual-match", "visual-match": {"results": [
        {"title": "No link at all"},
        {"title": "Script link", "link": "javascript:alert(1)"},
        {"title": "Real", "link": "https://example.test/ok"},
    ]}}]
    matches = await _run_with_dataset(photo, dataset)
    assert [m["title"] for m in matches] == ["Real"]


@pytest.mark.asyncio
async def test_search_image_caps_the_long_tail_of_matches(photo):
    """A figure photo draws 50+ matches, each carrying an inline base64
    thumbnail -- the tail is reposts and lookalikes, and it is all page
    weight and cache size."""
    from automation_control.reverse_image_search import MAX_MATCHES

    many = [{"title": f"m{i}", "link": f"https://example.test/{i}"} for i in range(MAX_MATCHES + 15)]
    matches = await _run_with_dataset(photo, [{"searchType": "visual-match", "visual-match": {"results": many}}])
    assert len(matches) == MAX_MATCHES


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


def test_review_page_offers_the_master_list_as_series_options(client, session, tmp_path):
    """The series is the field a photo can't answer, so the form should
    offer the vocabulary instead of an empty box."""
    capture_id = _make_capture(session, tmp_path)

    page = client.get(f"/collectibles/review/{capture_id}")

    assert "<datalist id='series-options'>" in page.text
    assert "Snack Series" in page.text        # absent from the old hardcoded list
    assert "Pumpkin Patch" in page.text       # a limited series, year prefix stripped
    assert "LATEST" not in page.text


def test_review_page_shows_what_the_lens_matches_agree_the_series_is(client, session, tmp_path):
    """Three listings naming Flower Series and one naming Marine Series is
    the shape of a real run -- the page should rank them and show the
    tally, not assert a winner."""
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    fake_items = [
        {"title": "Sonny Angel Flower Series Lily Bell", "link": "https://example.test/1"},
        {"title": "Sonny Angel Flower Series Blind Box", "link": "https://example.test/2"},
        {"title": "Sonny Angel Flower Series secret", "link": "https://example.test/3"},
        {"title": "Sonny Angel Marine Series Dolphin", "link": "https://example.test/4"},
        {"title": "just a depop post", "link": "https://example.test/5"},
    ]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post(f"/collectibles/review/{capture_id}/reverse-image-search")

    page = client.get(f"/collectibles/review/{capture_id}")
    assert "Named by these matches" in page.text
    assert "data-series='Flower Series'" in page.text
    assert "Strong agreement" in page.text
    # Ranked, so the agreed series comes before the lone dissenter.
    assert page.text.index("data-series='Flower Series'") < page.text.index("data-series='Marine Series'")


def test_a_pop_mart_capture_gets_a_character_suggestion_with_its_secret_flag(client, session, tmp_path):
    """A Pop Mart set page names every figure, so the suggestion can offer
    the character and its series -- and tick the chase box for a secret."""
    app.state.settings.apify_token = "fake-token"
    capture = CapturedCollectible(id="cap-pm", image_path=str(tmp_path / "p.jpg"),
                                  status=CardCaptureStatus.PENDING_REVIEW, brand="Pop Mart")
    (tmp_path / "p.jpg").write_bytes(b"x")
    session.add(capture)
    session.commit()

    fake_items = [
        {"title": "POP MART Twinkle Twinkle Chili Sauce super secret", "link": "https://example.test/1"},
        {"title": "Twinkle Twinkle Create Your Taste Chili Sauce", "link": "https://example.test/2"},
        {"title": "popmart twinkle twinkle chili sauce figure", "link": "https://example.test/3"},
    ]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post("/collectibles/review/cap-pm/reverse-image-search")

    page = client.get("/collectibles/review/cap-pm")
    assert "data-character='Chili Sauce'" in page.text
    assert "data-series='Twinkle Twinkle Create Your Taste Series'" in page.text
    assert "data-secret='1'" in page.text


def test_the_datalist_is_scoped_to_the_captures_brand(client, session, tmp_path):
    """Sonny Angel and Pop Mart both have a Mustard. A Pop Mart capture
    must not be offered Sonny Angel's series, or vice versa."""
    (tmp_path / "p.jpg").write_bytes(b"x")
    session.add(CapturedCollectible(id="cap-sa", image_path=str(tmp_path / "p.jpg"),
                                    status=CardCaptureStatus.PENDING_REVIEW, brand="Sonny Angel"))
    session.commit()

    page = client.get("/collectibles/review/cap-sa")
    assert "Flower Series" in page.text
    assert "Twinkle Twinkle" not in page.text


def test_a_lens_series_suggestion_still_writes_nothing_to_the_capture(client, session, tmp_path):
    """The consensus fills the form field in the browser. It must not
    become a server-side write -- same rule as every other Lens result."""
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    fake_items = [{"title": "Sonny Angel Flower Series Lily Bell", "link": "https://example.test/1"}]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post(f"/collectibles/review/{capture_id}/reverse-image-search")

    session.expire_all()
    assert session.get(CapturedCollectible, capture_id).series is None


def test_a_character_and_its_own_series_count_as_one_answer(client, session, tmp_path):
    """Taken separately, "CRYBABY Crying Again Series" and "The Robber"
    look like a 7-6 split. They are the same answer at two levels of
    detail, and calling that weak sends you to re-check a correct call."""
    app.state.settings.apify_token = "fake-token"
    (tmp_path / "p.jpg").write_bytes(b"x")
    session.add(CapturedCollectible(id="cap-fam", image_path=str(tmp_path / "p.jpg"),
                                    status=CardCaptureStatus.PENDING_REVIEW, brand="POP MART"))
    session.commit()

    fake_items = [{"title": t, "link": f"https://example.test/{i}"} for i, t in enumerate([
        "Crybaby Crying Again Series blind box",
        "POP MART Crybaby Crying Again Series",
        "popmart crying again series figures",
        "Crybaby Crying Again Series The Robber",
        "Crybaby The Robber figure",
        "crying again the robber popmart",
    ])]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post("/collectibles/review/cap-fam/reverse-image-search")

    page = client.get("/collectibles/review/cap-fam")
    assert "Strong agreement" in page.text
    assert "data-character='The Robber'" in page.text


def test_weak_agreement_is_labelled_as_weak(client, session, tmp_path):
    """One match naming a series is a coincidence, and the page should say
    so rather than dressing it up as an identification."""
    app.state.settings.apify_token = "fake-token"
    capture_id = _make_capture(session, tmp_path)

    fake_items = [
        {"title": "Sonny Angel Marine Series Dolphin", "link": "https://example.test/1"},
        {"title": "Sonny Angel Flower Series Lily Bell", "link": "https://example.test/2"},
    ]
    with patch("automation_control.collectibles.search_image", new=AsyncMock(return_value=fake_items)):
        client.post(f"/collectibles/review/{capture_id}/reverse-image-search")

    page = client.get(f"/collectibles/review/{capture_id}")
    assert "Weak agreement" in page.text


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
