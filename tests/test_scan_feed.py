"""The feed answers one question between sheets -- 'can I scan the next
eight?' -- so the readiness signal and the image routes are what matter."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from automation_control import scan_feed
from automation_control.api import app
from automation_control.auth import require_dashboard_user

LOG = """Auto-detecting set and rotation per sheet.
Watching scan_ingest_data/watch -- scan a sheet to begin.

Sheet sheet-20260901-101500-000001.png: matched Paldean Fates (sv4pt5), rotation 270 deg

Sheet sheet-20260901-101500-000001.png: 8 card(s) detected (4x2)
  Card 1: Pineco [normal] added -- est. 0.04 USD

  Sheet done: 7 committed, 0 skipped, 1 set aside for review.
"""


@pytest.fixture
def tree(tmp_path, monkeypatch):
    media = tmp_path / "media"
    watch = tmp_path / "watch"
    archive = tmp_path / "archive" / "session"
    logs = tmp_path / "logs"
    for d in (media, watch, archive, logs):
        d.mkdir(parents=True)
    (logs / "scan-ingest.log").write_text(LOG)

    settings = type("S", (), {
        "scan_media_dir": str(media), "scan_watch_dir": str(watch),
        "scan_archive_dir": str(tmp_path / "archive"),
        "scan_log_file": str(logs / "scan-ingest.log"),
    })()
    monkeypatch.setattr(scan_feed, "get_settings", lambda: settings)
    monkeypatch.setattr(scan_feed, "_service_state", lambda: "active")

    import automation_control.scan_ingest_status as status
    monkeypatch.setattr(status, "get_settings", lambda: settings)
    return tmp_path, media, watch, archive


@pytest.fixture
def client(tree):
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sheet_image(archive: Path, stem: str, size=(400, 560)):
    path = archive / f"{stem}.png"
    Image.new("RGB", size, (30, 90, 160)).save(path)
    return path


def _crops(media: Path, stem: str, count: int):
    d = media / "work" / "session" / stem
    d.mkdir(parents=True)
    for n in range(1, count + 1):
        Image.new("RGB", (750, 1050), (200, 60, 40)).save(d / f"card{n}.jpg")
    return d


STEM = "sheet-20260901-101500-000001"


# --- the readiness signal --------------------------------------------------

def test_says_ready_when_nothing_is_waiting(client, tree):
    resp = client.get("/feed")
    assert resp.status_code == 200
    assert "Ready — scan the next eight" in resp.text


def test_says_busy_while_a_sheet_is_still_queued(client, tree):
    _, _, watch, _ = tree
    Image.new("RGB", (10, 10)).save(watch / "sheet-pending.png")

    resp = client.get("/feed")

    assert "Working through 1 sheet" in resp.text
    assert "Ready — scan the next eight" not in resp.text


def test_a_sheet_left_behind_does_not_block_scanning(client, tree):
    """A sheet the service examined and couldn't match sits in the watch
    folder indefinitely. Counting it as work-in-progress would pin the feed
    to 'wait' forever -- the one question it exists to answer."""
    import os
    import time

    _, _, watch, _ = tree
    stale = watch / "sheet-stuck.png"
    Image.new("RGB", (10, 10)).save(stale)
    old = time.time() - scan_feed.STUCK_AFTER_SECONDS - 60
    os.utime(stale, (old, old))

    resp = client.get("/feed")

    assert "Ready — scan the next eight" in resp.text
    assert "1 earlier sheet" in resp.text


def test_a_stuck_sheet_is_still_mentioned_while_busy(client, tree):
    import os
    import time

    _, _, watch, _ = tree
    Image.new("RGB", (10, 10)).save(watch / "sheet-fresh.png")
    stale = watch / "sheet-stuck.png"
    Image.new("RGB", (10, 10)).save(stale)
    old = time.time() - scan_feed.STUCK_AFTER_SECONDS - 60
    os.utime(stale, (old, old))

    resp = client.get("/feed")

    assert "Working through 1 sheet" in resp.text
    assert "1 earlier sheet" in resp.text


def test_says_so_when_the_service_is_down(client, tree, monkeypatch):
    """Silence here would read as 'ready' when in fact nothing is being
    processed at all."""
    monkeypatch.setattr(scan_feed, "_service_state", lambda: "inactive")
    resp = client.get("/feed")
    assert "Scanner service is not running" in resp.text


# --- what each sheet shows -------------------------------------------------

def test_lists_the_sheet_with_its_outcome(client, tree):
    resp = client.get("/feed")
    assert "7 committed" in resp.text
    assert "8 card(s) detected" in resp.text


def test_shows_the_scan_itself_when_archived(client, tree):
    _, _, _, archive = tree
    _sheet_image(archive, STEM)
    resp = client.get("/feed")
    assert f"/feed/sheet/{STEM}" in resp.text


def test_says_when_the_scan_is_not_archived(client, tree):
    resp = client.get("/feed")
    assert "sheet not archived" in resp.text


def test_shows_each_card_cut_from_the_sheet(client, tree):
    _, media, _, _ = tree
    _crops(media, STEM, 8)
    resp = client.get("/feed")
    for n in (1, 8):
        assert f"/feed/crop/{STEM}/{n}" in resp.text


# --- images ----------------------------------------------------------------

def test_sheet_is_served_resized(client, tree):
    """An archived sheet is a ~70MB PNG; serving it raw would make the feed
    unusable on the phone it's read on."""
    import io

    _, _, _, archive = tree
    source = _sheet_image(archive, STEM, size=(2000, 2800))

    resp = client.get(f"/feed/sheet/{STEM}")

    assert resp.status_code == 200
    assert Image.open(io.BytesIO(resp.content)).width == scan_feed.SHEET_THUMB_WIDTH
    assert len(resp.content) < source.stat().st_size


def test_crop_is_served_resized(client, tree):
    import io

    _, media, _, _ = tree
    _crops(media, STEM, 2)
    resp = client.get(f"/feed/crop/{STEM}/1")
    assert resp.status_code == 200
    assert Image.open(io.BytesIO(resp.content)).width == scan_feed.CROP_THUMB_WIDTH


def test_unknown_sheet_404s(client, tree):
    assert client.get("/feed/sheet/sheet-does-not-exist").status_code == 404


def test_card_index_out_of_range_404s(client, tree):
    _, media, _, _ = tree
    _crops(media, STEM, 2)
    assert client.get(f"/feed/crop/{STEM}/9").status_code == 404


def test_sheet_name_cannot_walk_the_filesystem():
    """The stem comes from the URL, so anything that isn't a plain sheet
    name is refused rather than used as a path. Asserted on the guard
    itself: a client normalises '..' out of the path before it is sent, so
    going through the route would test the client, not this."""
    from fastapi import HTTPException

    for bad in ("..", "../../etc/passwd", "sheet-../../x", "sheet-a/b", "passwd"):
        with pytest.raises(HTTPException) as raised:
            scan_feed._safe_stem(bad)
        assert raised.value.status_code == 404

    assert scan_feed._safe_stem("sheet-20260901-101500-000001") == "sheet-20260901-101500-000001"


def test_a_well_formed_but_unknown_sheet_404s_through_the_route(client, tree):
    assert client.get("/feed/sheet/sheet-19990101-000000-000000").status_code == 404


def test_feed_requires_auth(tree):
    app.dependency_overrides.clear()
    assert TestClient(app).get("/feed").status_code in (401, 403, 307, 302)
