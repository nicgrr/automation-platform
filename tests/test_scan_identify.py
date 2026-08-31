from unittest.mock import patch

import imagehash
import numpy as np
import pytest
from PIL import Image

from automation_control.scan_ingest import identify as identify_mod
from automation_control.scan_ingest.identify import Identification, ReferenceCard, Source, detect_sheet_set_and_rotation, identify_card, read_card_number


def _write_image(path, seed: int, size=(750, 1050)):
    """Deterministic noise art -- distinct seeds give distinct hashes."""
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(array).save(path)
    return path


def _phash_of(path) -> str:
    with Image.open(path) as image:
        return str(imagehash.phash(image.convert("RGB")))


@pytest.fixture
def scan_and_refs(tmp_path):
    """A scan that is pixel-identical to reference card #31, plus a set of
    other references that are all clearly different."""
    scan = _write_image(tmp_path / "scan.jpg", seed=31)
    refs = [ReferenceCard(f"xy11-{n}", str(n), f"Card {n}", _phash_of(_write_image(tmp_path / f"ref{n}.png", seed=n))) for n in (10, 20, 31, 40)]
    return scan, refs


# --- number OCR ---

def test_read_card_number_parses_and_normalises(tmp_path):
    image = _write_image(tmp_path / "c.jpg", 1)
    with patch.object(identify_mod.pytesseract, "image_to_string", return_value=" 071/114 "):
        number, raw = read_card_number(image, set_total=114)
    assert number == "71"  # leading zero stripped so it matches catalog numbering
    assert raw == "071/114"


def test_read_card_number_rejects_out_of_range(tmp_path):
    """An in-format but impossible number is a misread, not a discovery."""
    image = _write_image(tmp_path / "c.jpg", 1)
    with patch.object(identify_mod.pytesseract, "image_to_string", return_value="912/114"):
        number, raw = read_card_number(image, set_total=114)
    assert number is None
    assert raw == "912/114"


def test_read_card_number_returns_none_for_unparseable_text(tmp_path):
    image = _write_image(tmp_path / "c.jpg", 1)
    with patch.object(identify_mod.pytesseract, "image_to_string", return_value="////"):
        number, raw = read_card_number(image, set_total=114)
    assert number is None


def test_read_card_number_survives_missing_tesseract(tmp_path):
    """A broken OCR install must degrade to pHash-only, not crash the scan."""
    image = _write_image(tmp_path / "c.jpg", 1)
    with patch.object(identify_mod.pytesseract, "image_to_string", side_effect=OSError("tesseract not found")):
        number, raw = read_card_number(image, set_total=114)
    assert number is None and raw is None


def test_read_card_number_handles_unreadable_file(tmp_path):
    assert read_card_number(tmp_path / "missing.jpg") == (None, None)


# --- reconciliation ---

def test_identify_both_signals_agreeing_is_maximum_confidence(scan_and_refs):
    scan, refs = scan_and_refs
    with patch.object(identify_mod, "read_card_number", return_value=("31", "31/114")):
        result = identify_card(scan, refs, set_total=114)

    assert result.source is Source.BOTH
    assert result.card_id == "xy11-31"
    assert result.confidence == 1.0
    assert not result.needs_confirmation


def test_identify_prefers_phash_when_signals_conflict(scan_and_refs):
    """Real low-DPI scans produced well-formed but *wrong* OCR numbers that
    would have overwritten correct art matches, so pHash is the suggestion
    on a conflict -- and it always goes to a human either way."""
    scan, refs = scan_and_refs
    with patch.object(identify_mod, "read_card_number", return_value=("20", "20/114")):
        result = identify_card(scan, refs, set_total=114)

    assert result.source is Source.CONFLICT
    assert result.card_id == "xy11-31"  # the art match, not the OCR read
    assert result.needs_confirmation
    assert "OCR read #20" in result.note


def test_identify_falls_back_to_phash_when_ocr_silent(scan_and_refs):
    scan, refs = scan_and_refs
    with patch.object(identify_mod, "read_card_number", return_value=(None, None)):
        result = identify_card(scan, refs, set_total=114)

    assert result.source is Source.PHASH
    assert result.card_id == "xy11-31"
    assert result.phash_distance == 0


def test_identify_uses_ocr_when_art_match_is_out_of_threshold(tmp_path):
    scan = _write_image(tmp_path / "scan.jpg", seed=999)
    refs = [ReferenceCard(f"xy11-{n}", str(n), f"Card {n}", _phash_of(_write_image(tmp_path / f"r{n}.png", seed=n))) for n in (1, 2)]
    with patch.object(identify_mod, "read_card_number", return_value=("2", "2/114")):
        result = identify_card(scan, refs, set_total=114, max_phash_distance=2)

    assert result.source is Source.OCR
    assert result.card_id == "xy11-2"


def test_identify_returns_none_when_nothing_matches(tmp_path):
    scan = _write_image(tmp_path / "scan.jpg", seed=999)
    refs = [ReferenceCard(f"xy11-{n}", str(n), f"Card {n}", _phash_of(_write_image(tmp_path / f"r{n}.png", seed=n))) for n in (1, 2)]
    with patch.object(identify_mod, "read_card_number", return_value=(None, None)):
        result = identify_card(scan, refs, set_total=114, max_phash_distance=2)

    assert result.source is Source.NONE
    assert result.card_id is None
    assert result.needs_confirmation
    assert "distance" in result.note


def test_identify_reports_when_set_has_no_reference_hashes(tmp_path):
    scan = _write_image(tmp_path / "scan.jpg", seed=1)
    refs = [ReferenceCard("xy11-1", "1", "Card 1", "")]
    with patch.object(identify_mod, "read_card_number", return_value=(None, None)):
        result = identify_card(scan, refs)

    assert result.card_id is None
    assert "cached" in result.note


# --- the confidence contract ---

def test_low_confidence_always_needs_confirmation():
    """The module's core safety promise: never auto-commit a weak match."""
    weak = Identification(card_id="x", number="1", name="n", source=Source.PHASH, confidence=0.74)
    strong = Identification(card_id="x", number="1", name="n", source=Source.PHASH, confidence=0.75)
    assert weak.needs_confirmation
    assert not strong.needs_confirmation


def test_conflict_always_needs_confirmation_even_at_high_confidence():
    conflicted = Identification(card_id="x", number="1", name="n", source=Source.CONFLICT, confidence=1.0)
    assert conflicted.needs_confirmation


def test_unidentified_always_needs_confirmation():
    assert Identification(None, None, None, Source.NONE, 1.0).needs_confirmation


def test_phash_confidence_rewards_closeness_and_separation():
    close_clear = identify_mod._phash_confidence(distance=4, margin=10, max_distance=12)
    close_ambiguous = identify_mod._phash_confidence(distance=4, margin=1, max_distance=12)
    far_clear = identify_mod._phash_confidence(distance=12, margin=10, max_distance=12)

    # a near-tie between two lookalike cards must score below a clear win
    assert close_ambiguous < close_clear
    assert far_clear < close_clear
    # an ambiguous match must land below the auto-accept bar
    assert close_ambiguous < 0.75


# --- sheet-level set + rotation auto-detection ---

_PIL_CLOCKWISE = {0: None, 90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def _upright_and_placed(tmp_path, seed: int, placed_clockwise_degrees: int, name: str):
    """An `upright` reference image plus a `crop` that's how it would look
    if the physical card were placed on the bed rotated by
    `placed_clockwise_degrees` before scanning -- so the correct fix is
    rotating the crop back by the same amount."""
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 255, (1050, 750, 3), dtype=np.uint8)
    upright = Image.fromarray(array)
    upright_path = tmp_path / f"{name}-upright.png"
    upright.save(upright_path)

    transpose = _PIL_CLOCKWISE[placed_clockwise_degrees]
    placed = upright if transpose is None else upright.transpose(transpose)
    crop_path = tmp_path / f"{name}-crop.jpg"
    placed.save(crop_path)

    return crop_path, _phash_of(upright_path)


def test_detect_sheet_set_and_rotation_recovers_a_sideways_placement(tmp_path):
    """The exact real-world failure this exists to catch: cards placed
    rotated on the bed threw off identification against the *correct*
    catalog entirely. One set, cards placed at 270 clockwise."""
    catalog = {"setA": []}
    for n in range(1, 5):
        crop_path, ref_hash = _upright_and_placed(tmp_path, seed=n, placed_clockwise_degrees=270, name=f"a{n}")
        catalog["setA"].append(ReferenceCard(f"setA-{n}", str(n), f"Card {n}", ref_hash))

    crops = [tmp_path / f"a{n}-crop.jpg" for n in range(1, 5)]
    result = detect_sheet_set_and_rotation(crops, catalog)

    # placed at 270 clockwise -> undoing it takes a 90 clockwise correction
    assert result == ("setA", 90)


def test_detect_sheet_set_and_rotation_picks_the_set_most_crops_agree_on(tmp_path):
    """Two cached sets; the sheet's cards belong to the second one. Must
    not just grab whichever set happens to be checked first."""
    catalog = {"setA": [], "setB": []}
    crops = []
    for n in range(1, 5):
        # setA gets unrelated reference art (never matches these crops)
        _, other_hash = _upright_and_placed(tmp_path, seed=100 + n, placed_clockwise_degrees=0, name=f"decoy{n}")
        catalog["setA"].append(ReferenceCard(f"setA-{n}", str(n), f"Decoy {n}", other_hash))

        crop_path, ref_hash = _upright_and_placed(tmp_path, seed=n, placed_clockwise_degrees=90, name=f"b{n}")
        catalog["setB"].append(ReferenceCard(f"setB-{n}", str(n), f"Card {n}", ref_hash))
        crops.append(crop_path)

    result = detect_sheet_set_and_rotation(crops, catalog)

    # placed at 90 clockwise -> undoing it takes a 270 clockwise correction
    assert result == ("setB", 270)


def test_detect_sheet_set_and_rotation_returns_none_when_nothing_matches(tmp_path):
    catalog = {"setA": [ReferenceCard("setA-1", "1", "Card 1", _phash_of(_write_image(tmp_path / "ref.png", seed=1)))]}
    unrelated_crop = _write_image(tmp_path / "unrelated.jpg", seed=999)

    assert detect_sheet_set_and_rotation([unrelated_crop], catalog) is None


def test_detect_sheet_set_and_rotation_requires_more_than_one_lucky_match(tmp_path):
    """A sheet with several cards where only one happens to match by chance
    shouldn't route the whole sheet -- that's how a wrong-set false
    positive would slip through."""
    catalog = {"setA": []}
    crops = []
    # one genuine match
    crop_path, ref_hash = _upright_and_placed(tmp_path, seed=1, placed_clockwise_degrees=0, name="real")
    catalog["setA"].append(ReferenceCard("setA-1", "1", "Card 1", ref_hash))
    crops.append(crop_path)
    # three cards that don't match anything in the catalog
    for n in range(2, 5):
        crops.append(_write_image(tmp_path / f"noise{n}.jpg", seed=500 + n))

    assert detect_sheet_set_and_rotation(crops, catalog) is None


def test_detect_sheet_set_and_rotation_handles_empty_catalog(tmp_path):
    crop = _write_image(tmp_path / "c.jpg", seed=1)
    assert detect_sheet_set_and_rotation([crop], {}) is None


def test_detect_sheet_set_and_rotation_ignores_matches_pinned_to_the_distance_limit(monkeypatch, tmp_path):
    """The real misroute this guards against: the sheet's actual set wasn't
    cached yet, and a wrong set scraped together three matches sitting
    *exactly* at the distance limit (12, 12, 12) -- which is what "nothing
    here really matches" looks like -- and won the sheet on count alone.
    Only matches comfortably inside the limit may carry a routing decision.

    Distances are stubbed rather than conjured from real art, because the
    point under test is the accept/reject rule at specific distances, not
    the hashing itself."""
    crops = [_write_image(tmp_path / f"c{n}.jpg", seed=n) for n in range(1, 5)]
    catalog = {"wrongset": [ReferenceCard("wrongset-1", "1", "Card 1", "0" * 16)]}

    # every crop/rotation lands exactly on the limit
    monkeypatch.setattr(identify_mod.imagehash, "hex_to_hash", lambda h: h)
    monkeypatch.setattr(identify_mod.imagehash, "phash", lambda image: _FakeHash(12))

    assert detect_sheet_set_and_rotation(crops, catalog, max_phash_distance=12) is None


def test_detect_sheet_set_and_rotation_accepts_matches_inside_the_limit(monkeypatch, tmp_path):
    """Flip side of the above: comfortably-inside-the-limit matches (the
    correct set in that same real case scored 6/6/10/10) must still route."""
    crops = [_write_image(tmp_path / f"c{n}.jpg", seed=n) for n in range(1, 5)]
    catalog = {"rightset": [ReferenceCard("rightset-1", "1", "Card 1", "0" * 16)]}

    monkeypatch.setattr(identify_mod.imagehash, "hex_to_hash", lambda h: h)
    monkeypatch.setattr(identify_mod.imagehash, "phash", lambda image: _FakeHash(6))

    assert detect_sheet_set_and_rotation(crops, catalog, max_phash_distance=12) == ("rightset", 0)


def test_read_card_number_finds_the_swsh_era_bottom_left_corner(tmp_path):
    """Real Vivid Voltage scans read nothing at the XY/SM-era bottom-right
    position and "151/185" cleanly at bottom-left. Both corners must be
    tried, or a whole era of cards silently loses its OCR signal."""
    image = _write_image(tmp_path / "c.jpg", 1)
    calls = []

    def fake_ocr(_binarized, config=""):
        calls.append(1)
        return "" if len(calls) == 1 else "151/185"  # right corner blank, left corner good

    with patch.object(identify_mod.pytesseract, "image_to_string", fake_ocr):
        number, raw = read_card_number(image, set_total=185)

    assert number == "151"
    assert raw == "151/185"


def test_read_card_number_keeps_looking_after_an_out_of_range_corner(tmp_path):
    """A number that parses but is out of range may just be the wrong
    corner for this card's era, not a misread of the right one."""
    image = _write_image(tmp_path / "c.jpg", 1)
    answers = iter(["999/999", "42/185"])

    with patch.object(identify_mod.pytesseract, "image_to_string", lambda *a, **k: next(answers)):
        number, raw = read_card_number(image, set_total=185)

    assert number == "42"


def test_read_set_totals_ranks_by_agreement_across_the_sheet(tmp_path):
    """The denominator is how an *uncached* set gets identified, so it's
    read across the whole sheet: correct reads agree, misreads scatter."""
    crops = [_write_image(tmp_path / f"c{n}.jpg", n) for n in range(1, 5)]
    answers = iter(["12/264", "40/264", "7/999", "88/264"])

    with patch.object(identify_mod.pytesseract, "image_to_string", lambda *a, **k: next(answers)):
        totals = identify_mod.read_set_totals(crops)

    assert totals[0] == 264  # 3 agreeing reads beat the single 999
    assert 999 in totals


def test_read_set_totals_is_empty_when_nothing_is_legible(tmp_path):
    crops = [_write_image(tmp_path / "c.jpg", 1)]
    with patch.object(identify_mod.pytesseract, "image_to_string", lambda *a, **k: ""):
        assert identify_mod.read_set_totals(crops) == []


class _FakeHash:
    """Stands in for an imagehash object whose subtraction always yields a
    fixed distance, so accept/reject thresholds can be tested directly."""

    def __init__(self, distance: int):
        self.distance = distance

    def __sub__(self, other):
        return self.distance
