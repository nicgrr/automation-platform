import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control import scan_ingest_status
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CardSet, CardVariant, ScanSession, ScanSessionStatus
from automation_control.scan_ingest_status import _parse_sheets

LOG_SAMPLE = """Scanning Cosmic Eclipse (sm12) as normal, condition Near Mint.
Inventory so far: 9 distinct card(s), 9 total.
Watching scan_ingest_data/watch -- scan a sheet to begin.

Sheet sheet-20260830-085647-569289.png: 8 card(s) detected (4x2)
  Card 1: Vaporeon [normal] added -- est. 2.27 USD (pokemontcg_tcgplayer_market)
  Card 5: (unidentified) needs confirmation (none, 0.00) -- set aside: needs_review/card5.jpg

  Sheet done: 4 committed, 0 skipped, 4 set aside for review.
  Session total: 1 sheet(s), 4 card(s).
  Cosmic Eclipse inventory: 4 distinct, 4 total.

Sheet sheet-20260830-090509-553953.png: 8 card(s) detected (4x2)
  WARNING: card(s) [8] are not card-shaped -- possibly two cards touching, or a mis-split
  Sheet skipped -- left in the watch folder for a rescan.

  Sheet done: 0 committed, 8 skipped, 0 set aside for review.
  Session total: 2 sheet(s), 4 card(s).
  Cosmic Eclipse inventory: 4 distinct, 4 total.
"""


def test_parse_sheets_groups_newest_first_with_preamble_separated():
    preamble, sheets = _parse_sheets(LOG_SAMPLE)

    assert preamble == [
        "Scanning Cosmic Eclipse (sm12) as normal, condition Near Mint.",
        "Inventory so far: 9 distinct card(s), 9 total.",
        "Watching scan_ingest_data/watch -- scan a sheet to begin.",
    ]
    assert [s.name for s in sheets] == ["sheet-20260830-090509-553953.png", "sheet-20260830-085647-569289.png"]


def test_parse_sheets_extracts_detected_and_outcome_counts():
    _, sheets = _parse_sheets(LOG_SAMPLE)
    committed_sheet = next(s for s in sheets if s.name == "sheet-20260830-085647-569289.png")

    assert committed_sheet.detected == 8
    assert committed_sheet.grid == "4x2"
    assert (committed_sheet.committed, committed_sheet.skipped, committed_sheet.set_aside) == (4, 0, 4)
    assert "Card 1: Vaporeon" in "\n".join(committed_sheet.lines)


def test_parse_sheets_flags_a_fully_skipped_sheet_distinctly():
    _, sheets = _parse_sheets(LOG_SAMPLE)
    skipped_sheet = next(s for s in sheets if s.name == "sheet-20260830-090509-553953.png")

    assert skipped_sheet.status == "skipped -- needs rescan"


def test_parse_sheets_derives_a_human_timestamp_from_the_filename():
    _, sheets = _parse_sheets(LOG_SAMPLE)
    assert sheets[-1].when == "2026-08-30 08:56:47"


def test_parse_sheets_handles_empty_log():
    preamble, sheets = _parse_sheets("")
    assert preamble == []
    assert sheets == []


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr(scan_ingest_status, "_service_state", lambda: "active")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_status_page_shows_a_running_session(client, session):
    session.add(CardSet(id="sm12", name="Cosmic Eclipse"))
    session.add(ScanSession(set_id="sm12", batch_variant=CardVariant.NORMAL, status=ScanSessionStatus.RUNNING, sheets_scanned=2, cards_committed=9))
    session.commit()

    response = client.get("/scan-ingest")

    assert response.status_code == 200
    assert "Cosmic Eclipse" in response.text
    assert "active" in response.text
