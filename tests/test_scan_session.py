import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import cv2
import imagehash
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import CardSet, CardVariant, CatalogCard, InventoryItem, ScanSession, ScanSessionStatus
from automation_control.scan_ingest import session as session_mod
from automation_control.scan_ingest.detect import DetectionResult, detect_cards
from automation_control.scan_ingest.identify import Identification, ReferenceCard, Source
from automation_control.scan_ingest.session import Prompter, review_card, run_session, start_or_resume, wait_for_sheet

CARD_W, CARD_H = 660, 460
GAP, MARGIN = 40, 15
BG = (169, 224, 233)


class ScriptedPrompter(Prompter):
    """Replays a fixed list of answers so the interactive loop can be
    driven without a terminal. Runs out of answers -> the loop asked
    something the test didn't anticipate, which is itself a failure."""

    def __init__(self, answers=None, confirms=None):
        self.answers = list(answers or [])
        self.confirms = list(confirms or [])
        self.messages: list[str] = []
        self.questions: list[str] = []

    def ask(self, question: str, default: str = "") -> str:
        self.questions.append(question)
        if not self.answers:
            raise AssertionError(f"unexpected ask(): {question!r}")
        return self.answers.pop(0)

    def confirm(self, question: str, default: bool = True) -> bool:
        self.questions.append(question)
        if not self.confirms:
            raise AssertionError(f"unexpected confirm(): {question!r}")
        return self.confirms.pop(0)

    def notify(self, message: str) -> None:
        self.messages.append(message)

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@dataclass
class FakeSettings:
    scan_watch_dir: str
    scan_archive_dir: str
    scan_media_dir: str
    scan_cache_dir: str
    scan_phash_max_distance: int = 12


@pytest.fixture
def settings(tmp_path):
    return FakeSettings(
        scan_watch_dir=str(tmp_path / "watch"), scan_archive_dir=str(tmp_path / "archive"),
        scan_media_dir=str(tmp_path / "media"), scan_cache_dir=str(tmp_path / "cache"),
    )


def _card_art(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (CARD_H, CARD_W, 3), dtype=np.uint8)


def _make_sheet(path: Path, seeds: list[int], rows: int, cols: int) -> Path:
    height = MARGIN * 2 + rows * CARD_H + (rows - 1) * GAP
    width = MARGIN * 2 + cols * CARD_W + (cols - 1) * GAP
    sheet = np.full((height, width, 3), BG, dtype=np.uint8)
    for index, seed in enumerate(seeds):
        r, c = divmod(index, cols)
        y = MARGIN + r * (CARD_H + GAP)
        x = MARGIN + c * (CARD_W + GAP)
        sheet[y : y + CARD_H, x : x + CARD_W] = _card_art(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), sheet)
    return path


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(CardSet(id="xy11", name="Steam Siege", code="STS", printed_total=114, cached_at=datetime.now(UTC)))
        for n in (1, 2):
            s.add(CatalogCard(id=f"xy11-{n}", set_id="xy11", number=str(n), name=f"Card {n}", phash="0" * 16))
        s.commit()
        yield s


def _references():
    return [ReferenceCard("xy11-1", "1", "Card 1", "0" * 16), ReferenceCard("xy11-2", "2", "Card 2", "f" * 16)]


# --- watch folder ---

def test_wait_for_sheet_ignores_non_images(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "notes.txt").write_text("not a scan")

    assert wait_for_sheet(watch, set(), ScriptedPrompter(), poll_interval=0.01, max_wait=0.05) is None


def test_wait_for_sheet_waits_until_file_stops_growing(tmp_path):
    """A scanner writes incrementally; picking the file up mid-write would
    hand a truncated image to the detector. Driven with a real file grown
    from a background thread rather than a mocked stat(), so it exercises
    the actual filesystem behaviour the loop depends on."""
    import threading

    watch = tmp_path / "watch"
    watch.mkdir()
    sheet = watch / "s.png"
    sheet.write_bytes(b"x" * 100)

    stop = threading.Event()

    # Writer grows the file faster than the poller samples it, so the
    # poller is guaranteed to observe a changing size and reset its
    # stability count rather than latching onto the initial value.
    def grow():
        for size in (200, 400, 800):
            if stop.wait(0.01):
                return
            sheet.write_bytes(b"x" * size)

    writer = threading.Thread(target=grow)
    writer.start()
    try:
        found = wait_for_sheet(watch, set(), ScriptedPrompter(), poll_interval=0.05, max_wait=5.0)
    finally:
        stop.set()
        writer.join()

    assert found == sheet
    # only returned once the writer finished, so the file is complete
    assert sheet.stat().st_size == 800


def test_wait_for_sheet_ignores_a_zero_byte_placeholder(tmp_path):
    """Some scanners create the file before writing to it; an empty file is
    never 'stable', it just hasn't started."""
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "s.png").write_bytes(b"")

    assert wait_for_sheet(watch, set(), ScriptedPrompter(), poll_interval=0.01, max_wait=0.1) is None


def test_wait_for_sheet_skips_already_seen(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    sheet = watch / "s.png"
    sheet.write_bytes(b"x" * 100)

    assert wait_for_sheet(watch, {sheet}, ScriptedPrompter(), poll_interval=0.01, max_wait=0.05) is None


# --- resume ---

def test_start_or_resume_creates_a_new_session_when_none_exists(db):
    prompter = ScriptedPrompter()
    scan_session = start_or_resume(db, "xy11", CardVariant.NORMAL, prompter)

    assert scan_session.status is ScanSessionStatus.RUNNING
    assert scan_session.cards_committed == 0
    assert prompter.questions == []  # nothing to resume, so nothing to ask


def test_start_or_resume_continues_an_open_session(db):
    original = ScanSession(set_id="xy11", batch_variant=CardVariant.NORMAL, sheets_scanned=3, cards_committed=20)
    db.add(original)
    db.commit()

    prompter = ScriptedPrompter(confirms=[True])
    resumed = start_or_resume(db, "xy11", CardVariant.HOLO, prompter)

    assert resumed.id == original.id
    assert resumed.cards_committed == 20  # running count preserved
    assert resumed.batch_variant is CardVariant.HOLO  # but this batch's variant applies
    assert "Resume" in prompter.questions[0]


def test_declining_resume_closes_the_old_session_and_starts_fresh(db):
    original = ScanSession(set_id="xy11", batch_variant=CardVariant.NORMAL, sheets_scanned=3, cards_committed=20)
    db.add(original)
    db.commit()

    fresh = start_or_resume(db, "xy11", CardVariant.NORMAL, ScriptedPrompter(confirms=[False]))

    assert fresh.id != original.id
    assert fresh.cards_committed == 0
    assert db.get(ScanSession, original.id).status is ScanSessionStatus.DONE


def test_resume_only_offers_sessions_for_the_same_set(db):
    db.add(CardSet(id="xy12", name="Evolutions", code="EVO", cached_at=datetime.now(UTC)))
    db.add(ScanSession(set_id="xy12", batch_variant=CardVariant.NORMAL, cards_committed=9))
    db.commit()

    prompter = ScriptedPrompter()
    scan_session = start_or_resume(db, "xy11", CardVariant.NORMAL, prompter)

    assert scan_session.cards_committed == 0
    assert prompter.questions == []


# --- per-card review ---

def test_review_passes_confident_cards_through_without_asking():
    confident = Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)
    prompter = ScriptedPrompter()

    decided, confirmed = review_card(confident, 1, _references(), prompter)

    assert decided is confident
    assert not confirmed
    assert prompter.questions == []  # the whole point: no attention spent


def test_review_accepts_a_flagged_suggestion_on_empty_input():
    flagged = Identification("xy11-1", "1", "Card 1", Source.PHASH, 0.5)
    decided, confirmed = review_card(flagged, 1, _references(), ScriptedPrompter(answers=[""]))

    assert decided.card_id == "xy11-1"
    assert confirmed


def test_review_applies_a_typed_correction():
    wrong = Identification("xy11-1", "1", "Card 1", Source.CONFLICT, 0.5)
    decided, confirmed = review_card(wrong, 1, _references(), ScriptedPrompter(answers=["2"]))

    assert decided.card_id == "xy11-2"
    assert decided.confidence == 1.0  # a human typed it; that's authoritative
    assert decided.note == "corrected by operator"
    assert confirmed


def test_review_correction_tolerates_leading_zeros():
    wrong = Identification("xy11-1", "1", "Card 1", Source.CONFLICT, 0.5)
    decided, _ = review_card(wrong, 1, _references(), ScriptedPrompter(answers=["002"]))
    assert decided.card_id == "xy11-2"


def test_review_rejects_a_number_not_in_the_set_and_reasks():
    wrong = Identification("xy11-1", "1", "Card 1", Source.CONFLICT, 0.5)
    prompter = ScriptedPrompter(answers=["999", "2"])

    decided, _ = review_card(wrong, 1, _references(), prompter)

    assert decided.card_id == "xy11-2"
    assert "No card #999" in prompter.text


def test_review_can_skip_a_card():
    flagged = Identification("xy11-1", "1", "Card 1", Source.PHASH, 0.5)
    decided, confirmed = review_card(flagged, 1, _references(), ScriptedPrompter(answers=["s"]))

    assert decided is None
    assert confirmed


def test_review_will_not_accept_an_unidentified_card_with_empty_input():
    """Enter means 'accept the suggestion', and there isn't one -- so it
    must re-ask rather than commit nothing or crash."""
    unidentified = Identification(None, None, None, Source.NONE, 0.0)
    prompter = ScriptedPrompter(answers=["", "2"])

    decided, _ = review_card(unidentified, 1, _references(), prompter)

    assert decided.card_id == "xy11-2"
    assert "Nothing to accept" in prompter.text


# --- the full loop ---

def _patch_identify(monkeypatch, results):
    calls = {"n": 0}

    def fake(crop, refs, set_total=None, max_phash_distance=12):
        result = results[min(calls["n"], len(results) - 1)]
        calls["n"] += 1
        return result

    monkeypatch.setattr(session_mod, "identify_card", fake)


def test_run_session_commits_confident_cards_and_archives_the_sheet(db, settings, tmp_path, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "sheet1.png", [1, 2], rows=2, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter(confirms=[False])  # don't mark finished
    scan_session = run_session(
        db, "xy11", settings, prompter, variant=CardVariant.NORMAL,
        max_sheets=1, max_wait=0.5, poll_interval=0.01,
    )

    assert scan_session.cards_committed == 2
    assert scan_session.sheets_scanned == 1
    items = db.scalars(select(InventoryItem)).all()
    assert len(items) == 1 and items[0].quantity == 2  # same card twice -> incremented

    # original moved out of the watch folder so it isn't reprocessed
    assert not (Path(settings.scan_watch_dir) / "sheet1.png").exists()
    assert list((Path(settings.scan_archive_dir) / scan_session.id).glob("sheet1.png"))


def test_run_session_leaves_session_resumable_by_default(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    scan_session = run_session(db, "xy11", settings, ScriptedPrompter(confirms=[False]), max_sheets=1, max_wait=0.5, poll_interval=0.01)

    assert scan_session.status is ScanSessionStatus.RUNNING
    assert scan_session.finished_at is None


def test_run_session_can_be_marked_finished(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    scan_session = run_session(db, "xy11", settings, ScriptedPrompter(confirms=[True]), max_sheets=1, max_wait=0.5, poll_interval=0.01)

    assert scan_session.status is ScanSessionStatus.DONE
    assert scan_session.finished_at is not None


def test_run_session_prompts_for_flagged_cards_only(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1, 2], rows=2, cols=1)
    _patch_identify(monkeypatch, [
        Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0),      # confident -> silent
        Identification("xy11-2", "2", "Card 2", Source.PHASH, 0.5),     # flagged -> asks
    ])

    prompter = ScriptedPrompter(answers=[""], confirms=[False])
    run_session(db, "xy11", settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01)

    asks = [q for q in prompter.questions if "accept" in q]
    assert len(asks) == 1  # only the flagged card cost attention


def test_run_session_skipped_card_is_not_committed(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.PHASH, 0.5)])

    scan_session = run_session(db, "xy11", settings, ScriptedPrompter(answers=["s"], confirms=[False]), max_sheets=1, max_wait=0.5, poll_interval=0.01)

    assert scan_session.cards_committed == 0
    assert db.scalars(select(InventoryItem)).all() == []


def test_run_session_applies_operator_correction(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.CONFLICT, 0.5)])

    run_session(db, "xy11", settings, ScriptedPrompter(answers=["2"], confirms=[False]), max_sheets=1, max_wait=0.5, poll_interval=0.01)

    item = db.scalars(select(InventoryItem)).one()
    assert item.card_id == "xy11-2"  # the corrected card, not the machine's guess


def test_run_session_records_batch_variant_on_inventory(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    run_session(db, "xy11", settings, ScriptedPrompter(confirms=[False]), variant=CardVariant.REVERSE_HOLO, max_sheets=1, max_wait=0.5, poll_interval=0.01)

    assert db.scalars(select(InventoryItem)).one().variant is CardVariant.REVERSE_HOLO


def test_run_session_refuses_an_uncached_set(db, settings):
    with pytest.raises(ValueError, match="not cached"):
        run_session(db, "xy99", settings, ScriptedPrompter(), max_sheets=1, max_wait=0.1, poll_interval=0.01)


def test_run_session_refuses_a_set_with_no_reference_hashes(db, settings):
    db.add(CardSet(id="xy12", name="Evolutions", cached_at=datetime.now(UTC)))
    db.add(CatalogCard(id="xy12-1", set_id="xy12", number="1", name="No hash", phash=None))
    db.commit()

    with pytest.raises(ValueError, match="no reference hashes"):
        run_session(db, "xy12", settings, ScriptedPrompter(), max_sheets=1, max_wait=0.1, poll_interval=0.01)


# --- unattended mode: nothing may ever block waiting for a terminal ---

def test_unattended_never_asks_anything_even_with_a_prior_running_session(db, settings, monkeypatch):
    db.add(ScanSession(set_id="xy11", batch_variant=CardVariant.NORMAL, sheets_scanned=3, cards_committed=20))
    db.commit()
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()  # zero answers queued -- any ask/confirm call fails the test
    scan_session = run_session(db, "xy11", settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert prompter.questions == []
    assert scan_session.cards_committed == 21  # resumed the existing session's count, didn't start fresh
    assert scan_session.status is ScanSessionStatus.RUNNING  # never auto-marked finished


def test_unattended_sets_aside_ambiguous_cards_instead_of_asking(db, settings, tmp_path, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    flagged = Identification("xy11-1", "1", "Card 1", Source.PHASH, 0.5)
    _patch_identify(monkeypatch, [flagged])

    prompter = ScriptedPrompter()
    scan_session = run_session(db, "xy11", settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert prompter.questions == []
    assert scan_session.cards_committed == 0
    assert db.scalars(select(InventoryItem)).all() == []  # never guessed into inventory
    set_aside_dir = Path(settings.scan_media_dir) / "needs_review"
    saved = list(set_aside_dir.glob("*.jpg"))
    assert len(saved) == 1
    assert "1-Card 1" in saved[0].name  # traceable back to which card without the log open


def test_unattended_still_commits_confident_cards_normally(db, settings, monkeypatch):
    """The safety gate is per-card, not a global switch -- unattended mode
    must not relax confidence requirements, only the prompting."""
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()
    scan_session = run_session(db, "xy11", settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert prompter.questions == []
    assert scan_session.cards_committed == 1
    assert db.scalars(select(InventoryItem)).one().card_id == "xy11-1"


def test_unattended_skips_a_bad_detection_without_asking(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s.png", [1, 2], rows=2, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])
    monkeypatch.setattr(
        session_mod, "detect_cards",
        lambda *a, **k: DetectionResult(crop_paths=[], count=0, needs_review=True, warnings=["forced for test"]),
    )

    prompter = ScriptedPrompter()
    scan_session = run_session(db, "xy11", settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert prompter.questions == []
    assert scan_session.cards_committed == 0
    # left in the watch folder (or archived depending on flow) but never guessed at
    assert db.scalars(select(InventoryItem)).all() == []


def test_unattended_loops_across_multiple_sheets_without_asking_to_continue(db, settings, monkeypatch):
    _make_sheet(Path(settings.scan_watch_dir) / "s1.png", [1], rows=1, cols=1)
    _make_sheet(Path(settings.scan_watch_dir) / "s2.png", [1], rows=1, cols=1)
    _patch_identify(monkeypatch, [Identification("xy11-1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()
    scan_session = run_session(db, "xy11", settings, prompter, max_sheets=2, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert prompter.questions == []  # no "scan another sheet?" between the two
    assert scan_session.sheets_scanned == 2


# --- auto-detect mode (set_id=None) ---

def _phash_of_crop(path) -> str:
    with Image.open(path) as image:
        return str(imagehash.phash(image.convert("RGB")))


@pytest.fixture
def auto_db(tmp_path):
    """Two cached sets, both with *real* phashes (needed because auto-detect
    mode's routing decision runs the real matching logic, unlike `db`'s
    hand-picked placeholder hashes used by tests that monkeypatch
    identify_card directly)."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auto.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(CardSet(id="setA", name="Set A", printed_total=50, cached_at=datetime.now(UTC)))
        s.add(CardSet(id="setB", name="Set B", printed_total=50, cached_at=datetime.now(UTC)))
        s.commit()
        yield s


def _register_reference(db, set_id: str, card_id: str, crop_path: Path) -> None:
    db.add(CatalogCard(id=card_id, set_id=set_id, number="1", name="Card 1", phash=_phash_of_crop(crop_path)))
    db.commit()


def test_auto_session_routes_a_sheet_to_the_matching_cached_set(auto_db, settings, tmp_path, monkeypatch):
    # detect_cards only reads the sheet (never moves/consumes it), so probing
    # it here to build references and then letting run_session pick up the
    # same still-in-place file afterward is safe.
    sheet_path = _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [1, 2], rows=2, cols=1)
    probe = detect_cards(sheet_path, tmp_path / "probe_setup")
    for crop in probe.crop_paths:
        _register_reference(auto_db, "setB", f"setB-{crop.stem}", crop)
    _patch_identify(monkeypatch, [Identification("setB-card1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session is not None
    assert scan_session.set_id == "setB"
    assert any("matched Set B (setB)" in m for m in prompter.messages)
    items = auto_db.scalars(select(InventoryItem)).all()
    assert len(items) == 1


def test_auto_session_discriminates_between_two_cached_sets(auto_db, settings, tmp_path, monkeypatch):
    sheet_path = _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [10, 11], rows=2, cols=1)
    probe = detect_cards(sheet_path, tmp_path / "probe_setup")
    # setA gets the real references; setB gets unrelated decoy hashes
    for crop in probe.crop_paths:
        _register_reference(auto_db, "setA", f"setA-{crop.stem}", crop)
    decoy = _make_sheet(tmp_path / "decoy.png", [777, 888], rows=2, cols=1)
    decoy_crops = detect_cards(decoy, tmp_path / "decoy_out").crop_paths
    for crop in decoy_crops:
        _register_reference(auto_db, "setB", f"setB-{crop.stem}", crop)
    _patch_identify(monkeypatch, [Identification("setA-card1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session.set_id == "setA"


def test_auto_session_leaves_an_unmatched_sheet_in_the_watch_folder(auto_db, settings, monkeypatch):
    """Nothing cached matches -- must not guess, must not create a session,
    and must leave the file for a human rather than silently discarding it."""
    _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [1, 2], rows=2, cols=1)
    _register_reference(auto_db, "setA", "setA-1", _write_unrelated_reference(settings))

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session is None
    assert any("none confidently match any cached set" in m for m in prompter.messages)
    assert (Path(settings.scan_watch_dir) / "sheet.png").exists()
    assert auto_db.scalars(select(ScanSession)).all() == []


def test_auto_session_caches_an_unknown_set_from_the_printed_total(auto_db, settings, tmp_path, monkeypatch):
    """The bootstrap case: the pile's set isn't cached, so art matching
    cannot possibly work. Reading '/72' off the cards must identify and
    fetch the set, after which the same sheet routes normally -- no
    operator step in between."""
    sheet_path = _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [21, 22], rows=2, cols=1)
    reference_crops = detect_cards(sheet_path, tmp_path / "refs").crop_paths

    monkeypatch.setattr(session_mod, "read_set_totals", lambda paths: [72])
    monkeypatch.setattr(
        session_mod.catalog, "sets_with_printed_total",
        lambda total, api_key=None: [{"id": "newset", "name": "New Set", "printedTotal": total}],
    )

    def fake_cache_set(db, set_id, cache_dir, api_key=None, force=False, progress=None):
        db.add(CardSet(id=set_id, name="New Set", printed_total=72, cached_at=datetime.now(UTC)))
        for index, crop in enumerate(reference_crops, start=1):
            db.add(CatalogCard(id=f"{set_id}-{index}", set_id=set_id, number=str(index),
                               name=f"Card {index}", phash=_phash_of_crop(crop)))
        db.commit()
        return SimpleNamespace(set_name="New Set", cards_total=72, hashed=72, ready=True)

    monkeypatch.setattr(session_mod.catalog, "cache_set", fake_cache_set)
    _patch_identify(monkeypatch, [Identification("newset-1", "1", "Card 1", Source.BOTH, 1.0)])

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session is not None and scan_session.set_id == "newset"
    assert any("reading the set number off the cards" in m for m in prompter.messages)
    assert auto_db.scalars(select(InventoryItem)).all()


def test_auto_session_gives_up_gracefully_when_the_printed_total_is_unreadable(auto_db, settings, monkeypatch):
    """OCR finding nothing must leave the sheet alone, not crash the loop
    or fetch something arbitrary."""
    _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [31, 32], rows=2, cols=1)
    monkeypatch.setattr(session_mod, "read_set_totals", lambda paths: [])

    def explode(*args, **kwargs):
        raise AssertionError("must not look up sets without a printed total")

    monkeypatch.setattr(session_mod.catalog, "sets_with_printed_total", explode)

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session is None
    assert (Path(settings.scan_watch_dir) / "sheet.png").exists()


def test_auto_session_survives_a_failed_set_lookup(auto_db, settings, monkeypatch):
    """The catalog API is measurably flaky; a failed lookup must leave the
    sheet for a retry rather than take the whole background service down."""
    _make_sheet(Path(settings.scan_watch_dir) / "sheet.png", [41, 42], rows=2, cols=1)
    monkeypatch.setattr(session_mod, "read_set_totals", lambda paths: [264])

    def boom(total, api_key=None):
        raise RuntimeError("pokemontcg.io returned 502")

    monkeypatch.setattr(session_mod.catalog, "sets_with_printed_total", boom)

    prompter = ScriptedPrompter()
    scan_session = run_session(auto_db, None, settings, prompter, max_sheets=1, max_wait=0.5, poll_interval=0.01, unattended=True)

    assert scan_session is None
    assert any("could not be fetched" in m for m in prompter.messages)
    assert (Path(settings.scan_watch_dir) / "sheet.png").exists()


def _write_unrelated_reference(settings) -> Path:
    sheet = _make_sheet(Path(settings.scan_watch_dir).parent / "unrelated.png", [999], rows=1, cols=1)
    return detect_cards(sheet, Path(settings.scan_watch_dir).parent / "unrelated_out").crop_paths[0]


# --- review queue -----------------------------------------------------------

def _queue_crop(settings, crop_src: Path, name: str) -> Path:
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    review_dir.mkdir(parents=True, exist_ok=True)
    dest = review_dir / name
    shutil.copy(crop_src, dest)
    return dest


def test_review_commits_an_accepted_suggestion_and_clears_the_crop(auto_db, settings, tmp_path):
    sheet = _make_sheet(tmp_path / "s.png", [7, 8], rows=2, cols=1)
    crops = detect_cards(sheet, tmp_path / "out").crop_paths
    _register_reference(auto_db, "setA", "setA-1", crops[0])
    queued = _queue_crop(settings, crops[0], "sheet-x-card1-unidentified.jpg")

    prompter = ScriptedPrompter(answers=[""])  # Enter = accept
    outcome = session_mod.resolve_review_queue(auto_db, settings, prompter)

    assert outcome.committed == 1
    assert not queued.exists()          # cleared only after a successful commit
    assert outcome.remaining == 0
    assert auto_db.scalars(select(InventoryItem)).one().card_id == "setA-1"


def test_review_skips_leave_the_crop_in_the_queue(auto_db, settings, tmp_path):
    sheet = _make_sheet(tmp_path / "s.png", [9], rows=1, cols=1)
    crop = detect_cards(sheet, tmp_path / "out").crop_paths[0]
    _register_reference(auto_db, "setA", "setA-1", crop)
    queued = _queue_crop(settings, crop, "sheet-x-card1-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=["s"]))

    assert outcome.committed == 0 and outcome.skipped == 1
    assert queued.exists()             # still there for a later pass
    assert auto_db.scalars(select(InventoryItem)).all() == []


def test_review_discard_removes_the_crop_without_committing(auto_db, settings, tmp_path):
    sheet = _make_sheet(tmp_path / "s.png", [11], rows=1, cols=1)
    crop = detect_cards(sheet, tmp_path / "out").crop_paths[0]
    _register_reference(auto_db, "setA", "setA-1", crop)
    queued = _queue_crop(settings, crop, "sheet-x-card1-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=["d"]))

    assert outcome.deleted == 1 and outcome.committed == 0
    assert not queued.exists()
    assert auto_db.scalars(select(InventoryItem)).all() == []


def test_review_applies_a_typed_correction(auto_db, settings, tmp_path):
    """The machine's suggestion is only a suggestion -- a typed number wins
    and is what gets committed."""
    sheet = _make_sheet(tmp_path / "s.png", [13, 14], rows=2, cols=1)
    crops = detect_cards(sheet, tmp_path / "out").crop_paths
    _register_reference(auto_db, "setA", "setA-1", crops[0])
    auto_db.add(CatalogCard(id="setA-2", set_id="setA", number="2", name="Corrected",
                            phash=_phash_of_crop(crops[1])))
    auto_db.commit()
    _queue_crop(settings, crops[0], "sheet-x-card1-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=["2"]))

    assert outcome.committed == 1
    assert auto_db.scalars(select(InventoryItem)).one().card_id == "setA-2"


def test_review_rejects_a_number_not_in_the_set(auto_db, settings, tmp_path):
    sheet = _make_sheet(tmp_path / "s.png", [15], rows=1, cols=1)
    crop = detect_cards(sheet, tmp_path / "out").crop_paths[0]
    _register_reference(auto_db, "setA", "setA-1", crop)
    queued = _queue_crop(settings, crop, "sheet-x-card1-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=["999"]))

    assert outcome.committed == 0 and outcome.skipped == 1
    assert queued.exists()


def test_review_searches_every_cached_set_not_just_one(auto_db, settings, tmp_path):
    """A crop carries no record of which set its sheet was routed to -- and
    for the misrouted-sheet case that routing was wrong -- so the match must
    be found across all cached sets."""
    sheet = _make_sheet(tmp_path / "s.png", [21, 22], rows=2, cols=1)
    crops = detect_cards(sheet, tmp_path / "out").crop_paths
    # decoy references in setA, the real match only in setB
    _register_reference(auto_db, "setA", "setA-1", crops[1])
    _register_reference(auto_db, "setB", "setB-1", crops[0])
    _queue_crop(settings, crops[0], "sheet-x-card1-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=[""]))

    assert outcome.committed == 1
    assert auto_db.scalars(select(InventoryItem)).one().card_id == "setB-1"


def test_review_limit_stops_after_n_cards(auto_db, settings, tmp_path):
    sheet = _make_sheet(tmp_path / "s.png", [31, 32], rows=2, cols=1)
    crops = detect_cards(sheet, tmp_path / "out").crop_paths
    _register_reference(auto_db, "setA", "setA-1", crops[0])
    _queue_crop(settings, crops[0], "sheet-x-card1-unidentified.jpg")
    _queue_crop(settings, crops[1], "sheet-x-card2-unidentified.jpg")

    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter(answers=[""]), limit=1)

    assert outcome.committed == 1
    assert outcome.remaining == 1      # the untouched one is still queued


def test_review_reports_an_empty_queue_without_erroring(auto_db, settings):
    outcome = session_mod.resolve_review_queue(auto_db, settings, ScriptedPrompter())
    assert outcome.committed == 0 and outcome.remaining == 0
