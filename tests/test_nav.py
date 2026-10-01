"""The navigation contract: nothing built is unreachable, the drawer and
the dashboard agree, and counting how much work is waiting never costs a
page load more than it should."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CapturedCard,
    CardVariant,
    CapturedCollectible,
    CardCaptureStatus,
    FoilLabel,
    PricingStatus,
)
from automation_control.nav_counts import queue_counts
from automation_control.ui import (
    ADMIN_NAV,
    HIDDEN_NAV,
    NAV_COUNT_HREFS,
    PHOTO_FIELD_SCRIPT,
    TOOL_NAV,
    page,
    photo_field,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'nav.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, tmp_path):
    original = app.state.settings.scan_media_dir
    app.state.settings.scan_media_dir = str(tmp_path / "media")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.scan_media_dir = original


# --------------------------------------------------------------- structure


def test_every_nav_href_is_a_real_route():
    """A nav entry pointing at a route that doesn't exist is a dead link
    that only shows up when someone clicks it."""
    # via openapi() rather than app.routes: this FastAPI version keeps
    # included routers behind a lazy _IncludedRouter wrapper, so
    # app.routes lists only the handful defined directly on the app
    routes = set(app.openapi()["paths"])
    for _title, links, _collapsed in ADMIN_NAV:
        for href, _label in links:
            assert href in routes, href


def test_every_palette_tool_is_a_real_route():
    routes = set(app.openapi()["paths"])
    for _title, links, _collapsed in TOOL_NAV:
        for href, _label in links:
            assert href in routes, href


def test_no_href_appears_in_two_groups():
    """"Review queue" used to appear twice, in two groups, pointing at two
    different queues. One home per destination."""
    seen: dict[str, str] = {}
    for title, links, _collapsed in ADMIN_NAV:
        for href, _label in links:
            assert href not in seen, f"{href} in both {seen.get(href)} and {title}"
            seen[href] = title


def test_labels_are_unique():
    labels = [label for _t, links, _c in ADMIN_NAV for _h, label in links]
    assert len(labels) == len(set(labels)), "two nav entries share a label"


def test_drawer_is_the_condensed_eleven_destination_map():
    assert sum(len(links) for _title, links, _collapsed in ADMIN_NAV) == 11
    assert [title for title, _links, _collapsed in ADMIN_NAV] == [
        "Home", "Stock", "Selling", "Buying & research", "System",
    ]


def test_count_badges_only_on_routes_that_are_counted(session, tmp_path):
    """NAV_COUNT_HREFS must be real on both ends: every href in it needs
    an actual count from queue_counts() (a badge with no number behind
    it), AND needs to actually be a drawer link (a badge span that could
    never exist in the rendered DOM at all) -- real incident, 2026-09-29:
    this used to assert equality with queue_counts()'s full key set,
    which stayed passing even after ADMIN_NAV was condensed and seven of
    those hrefs stopped being drawer links at all, silently making their
    badge slots permanently dead."""
    app.state.settings.scan_media_dir = str(tmp_path / "media")
    countable = set(queue_counts(session, app.state.settings))
    drawer_hrefs = {h for _t, links, _c in ADMIN_NAV for h, _l in links}
    assert NAV_COUNT_HREFS <= countable
    assert NAV_COUNT_HREFS <= drawer_hrefs
    # and the reverse: any drawer link that genuinely has a real count
    # available should actually show its badge, not silently skip it
    assert (drawer_hrefs & countable) <= NAV_COUNT_HREFS


def test_the_live_capture_pipeline_is_in_the_condensed_navigation():
    """Daily capture and review remain discoverable after drawer condensation."""
    drawer = {h for _t, links, _c in ADMIN_NAV for h, _l in links}
    tools = {h for _t, links, _c in TOOL_NAV for h, _l in links}
    assert {"/scan-add", "/review-centre"} <= drawer
    for path in ["/collectibles/capture", "/collectibles/review", "/inventory-audit"]:
        assert path in tools, path


# ------------------------------------------------------------------ counts


def test_queue_counts_reports_pending_work(session, tmp_path):
    app.state.settings.scan_media_dir = str(tmp_path / "media")
    session.add_all([
        CapturedCard(front_image_path="a.jpg", status=CardCaptureStatus.PENDING_REVIEW),
        CapturedCard(front_image_path="b.jpg", status=CardCaptureStatus.PENDING_REVIEW),
        CapturedCard(front_image_path="c.jpg", status=CardCaptureStatus.REVIEWED,
                     pricing_status=PricingStatus.PENDING_PRICE_REVIEW),
        CapturedCollectible(image_path="d.jpg", status=CardCaptureStatus.PENDING_REVIEW),
    ])
    session.commit()

    counts = queue_counts(session, app.state.settings)
    assert counts["/cards/review"] == 2
    assert counts["/cards/pricing"] == 1
    assert counts["/collectibles/review"] == 1
    assert counts["/review-centre"] == 4


def test_scan_crop_count_is_a_directory_listing(session, tmp_path):
    media = tmp_path / "media"
    (media / "needs_review").mkdir(parents=True)
    for name in ("one.jpg", "two.jpg", "notes.txt"):
        (media / "needs_review" / name).write_bytes(b"x")
    app.state.settings.scan_media_dir = str(media)

    assert queue_counts(session, app.state.settings)["/review"] == 2


def test_uncached_queues_report_none_not_zero(session, tmp_path):
    """foil and photo-audit recompute by hashing every inventory photo
    (~30s). With no cache present the honest answer is "don't know" -- a
    badge is never worth stalling a page load, and reporting 0 would hide
    a real backlog."""
    app.state.settings.scan_media_dir = str(tmp_path / "empty")
    counts = queue_counts(session, app.state.settings)
    assert counts["/foil-review"] is None
    assert counts["/inventory-audit"] is None


def test_foil_count_excludes_already_labelled(session, tmp_path):
    media = tmp_path / "media"
    media.mkdir(parents=True)
    (media / "foil_candidates_cache.json").write_text(json.dumps([
        {"item_id": "keep-1"}, {"item_id": "keep-2"}, {"item_id": "done-1"},
    ]))
    app.state.settings.scan_media_dir = str(media)
    session.add(FoilLabel(
        inventory_item_id="done-1", card_id="c1",
        filed_variant=CardVariant.NORMAL, suggested_variant=CardVariant.HOLO,
        actual_variant=CardVariant.HOLO, confirmed=True, labeled_by="t",
    ))
    session.commit()

    assert queue_counts(session, app.state.settings)["/foil-review"] == 2


def test_nav_counts_endpoint_returns_the_same_numbers(client, session):
    session.add(CapturedCard(front_image_path="a.jpg", status=CardCaptureStatus.PENDING_REVIEW))
    session.commit()

    body = client.get("/nav/counts").json()
    assert body["counts"]["/cards/review"] == 1


def test_nav_counts_requires_login():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/nav/counts", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


@pytest.mark.parametrize(("path", "heading"), [
    ("/scan-add", "Scan &amp; add"),
    ("/review-centre", "Review centre"),
    ("/ebay", "eBay"),
    ("/nl-vault", "NL Vault shop"),
    ("/market-tools", "Market tools"),
])
def test_condensed_navigation_hubs_render(client, path, heading):
    response = client.get(path)
    assert response.status_code == 200
    assert heading in response.text


# ------------------------------------------------------------- photo field


def test_photo_field_offers_both_camera_and_album():
    """One input, two ways in: the camera path is what makes this usable
    for a find away from the desk, the album is what keeps it usable at
    one. Two inputs would mean two form fields to reconcile."""
    html = photo_field("front", "Front photo", required=True)
    assert html.count("data-pf-input") == 1
    assert "data-pf-camera" in html
    assert "data-pf-browse" in html
    assert "Take photo" in html
    assert "From album" in html
    assert "name='front'" in html
    assert "required" in html


def test_photo_field_buttons_are_equal_peers():
    """Neither way in is the fallback: which one is right depends on
    whether you're at the desk or out, not on what the page thinks."""
    html = photo_field("front", "Front photo")
    assert html.count("class='pf-btn'") == 2


def test_multi_shot_field_says_shots_accumulate():
    html = photo_field("photos", "Front photos", multiple=True)
    assert "one shot at a time" in html
    assert "multiple" in html


def test_multi_shot_capture_accumulates_rather_than_replacing():
    """A camera capture returns one photo and replaces the input's
    contents, so without this a bulk form would upload the last shot
    only, however many were taken."""
    assert "DataTransfer" in PHOTO_FIELD_SCRIPT
    assert "picked.concat(incoming)" in PHOTO_FIELD_SCRIPT


def test_photo_field_does_not_hardcode_capture_attribute():
    """`capture` is set by the camera button immediately before opening
    the picker. Baked into the markup it would remove the library path
    entirely on mobile."""
    assert "capture=" not in photo_field("front", "Front photo")


def test_photo_field_script_ships_with_the_page():
    rendered = page("t", photo_field("front", "Front photo"))
    assert "data-pf-camera" in rendered
    assert "URL.createObjectURL" in rendered
    assert ".photo-field{" in rendered


def test_hidden_nav_entries_are_still_real_routes():
    """HIDDEN_NAV's promise is "hidden, not deleted" -- a typed or
    bookmarked URL still has to answer. A route deleted out from under an
    entry here would break that silently, since nothing links to it."""
    routes = set(app.openapi()["paths"])
    for href, _label in HIDDEN_NAV:
        assert href in routes, href


def test_hidden_nav_and_visible_tools_do_not_overlap():
    """A destination is either listed or hidden, never both."""
    listed = {href for _t, links, _c in ADMIN_NAV for href, _l in links}
    listed.update(href for _t, links, _c in TOOL_NAV for href, _l in links)
    for href, _label in HIDDEN_NAV:
        assert href not in listed, href
