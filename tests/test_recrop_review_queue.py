"""The re-crop tool rewrites files in the live review queue, so its safety
properties are worth pinning down: never resurrect a settled crop, and never
replace a crop with a worse one."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts import recrop_review_queue as rc


def _art(seed: int, size=(245, 342)) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))


class _Ref:
    def __init__(self, phash, art_phash=None):
        self.phash = phash
        self.art_phash = art_phash


def _matcher_for(tmp_path, seed):
    import imagehash

    from automation_control.scan_ingest.identify import art_phash

    image = _art(seed)
    path = tmp_path / f"ref{seed}.png"
    image.save(path)
    with Image.open(path) as opened:
        rgb = opened.convert("RGB")
        return rc.Matcher({"s": [_Ref(str(imagehash.phash(rgb)), str(art_phash(rgb)))]})


def test_identical_image_scores_zero(tmp_path):
    matcher = _matcher_for(tmp_path, 5)
    same = tmp_path / "same.png"
    _art(5).save(same)
    assert matcher.best_distance(same) == 0


def test_unrelated_image_scores_far(tmp_path):
    matcher = _matcher_for(tmp_path, 5)
    other = tmp_path / "other.png"
    _art(999).save(other)
    assert matcher.best_distance(other) > 12


def test_unreadable_file_scores_no_match_rather_than_raising(tmp_path):
    """A corrupt crop must not abort a whole pass."""
    matcher = _matcher_for(tmp_path, 5)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assert matcher.best_distance(broken) == rc.NO_MATCH


def test_a_crop_settled_mid_run_is_not_recreated(tmp_path, monkeypatch, capsys):
    """The queue is worked by hand while this runs. A crop accepted or
    discarded after the run started must stay gone -- writing it back would
    resurrect a card already dealt with and invite a second commit."""
    media = tmp_path / "media"
    review = media / "needs_review"
    review.mkdir(parents=True)
    archive = tmp_path / "archive"
    archive.mkdir()

    settled = review / "sheet-20260830-075159-030428-card1-unidentified.jpg"
    _art(3).save(settled)

    monkeypatch.setattr(rc, "get_settings", lambda: type("S", (), {
        "scan_media_dir": str(media), "scan_archive_dir": str(archive),
        "scan_phash_max_distance": 12})())

    class _NullSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(rc, "SessionLocal", lambda: _NullSession())

    # The crop is present when the run lists the queue, and settled by the
    # operator immediately after -- the exact race this guards against.
    def _catalogs_then_settle(db):
        settled.unlink()
        return {"s": [_Ref("0" * 16, "0" * 16)]}
    monkeypatch.setattr(rc, "_load_all_catalogs", _catalogs_then_settle)

    def _never(*args, **kwargs):
        raise AssertionError("looked for a sheet for a crop that no longer exists")
    monkeypatch.setattr(rc, "detect_cards", _never)

    assert rc.main([]) == 0
    assert not settled.exists(), "a settled crop was recreated"
    assert "1 were settled while this ran" in capsys.readouterr().out


def test_empty_queue_is_reported_not_an_error(tmp_path, monkeypatch, capsys):
    media = tmp_path / "media"
    (media / "needs_review").mkdir(parents=True)
    monkeypatch.setattr(rc, "get_settings", lambda: type("S", (), {
        "scan_media_dir": str(media), "scan_archive_dir": str(tmp_path),
        "scan_phash_max_distance": 12})())
    assert rc.main([]) == 0
    assert "Review queue is empty" in capsys.readouterr().out
