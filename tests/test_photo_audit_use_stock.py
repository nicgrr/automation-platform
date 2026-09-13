"""Falling back to stock art on audit-flagged items, and getting back.

The whole safety of this operation rests on the journal, so most of what's
worth testing is what it excludes and what --revert refuses to clobber.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import InventoryItem, PhotoAuditDismissal
from scripts import photo_audit_use_stock as swap


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _item(session, item_id, path="scan_ingest_data/media/cards/a.jpg"):
    item = InventoryItem(id=item_id, catalog_item_id=None, card_id="sv1-1",
                         condition="NM", quantity=1, scan_image_path=path)
    session.add(item)
    session.commit()
    return item


def _flag(item_id, card_id="sv1-1", looks_like="sv2-9", path="scan_ingest_data/media/cards/a.jpg"):
    return {"item_id": item_id, "card_id": card_id, "looks_like_id": looks_like,
            "filed_label": "Filed As", "looks_like_label": "Looks Like",
            "own_distance": 30, "_scan_image_path": path}


def test_a_dismissed_item_is_left_alone(session, monkeypatch):
    """A dismissal is a human saying "I looked, this is fine". Clearing it
    would throw that verdict away and show stock art for a photo already
    confirmed correct."""
    _item(session, "keep")
    session.add(PhotoAuditDismissal(inventory_item_id="keep", own_distance_at_dismissal=30,
                                    dismissed_by="nic"))
    session.commit()
    monkeypatch.setattr(swap, "find_mismatches", lambda s, t: [_flag("keep")])

    assert swap.candidates(session, 12) == []


def test_an_item_matching_its_own_card_is_left_alone(session, monkeypatch):
    """Closest match *is* the card it's filed under -- a badly-hashing
    photo of the right card, not a mismatch."""
    _item(session, "self")
    monkeypatch.setattr(swap, "find_mismatches", lambda s, t: [_flag("self", looks_like="sv1-1")])

    assert swap.candidates(session, 12) == []


def test_an_item_with_no_photo_is_left_alone(session, monkeypatch):
    monkeypatch.setattr(swap, "find_mismatches", lambda s, t: [_flag("nophoto", path="")])
    assert swap.candidates(session, 12) == []


def test_a_real_mismatch_is_cleared_and_journalled(session, monkeypatch, tmp_path):
    monkeypatch.setattr(swap, "JOURNAL_DIR", tmp_path)
    _item(session, "bad", "scan_ingest_data/media/cards/bad.jpg")

    journal = swap.apply(session, [_flag("bad", path="scan_ingest_data/media/cards/bad.jpg")], "tester")

    session.expire_all()
    assert session.get(InventoryItem, "bad").scan_image_path is None
    entry = json.loads(journal.read_text())["items"][0]
    assert entry["item_id"] == "bad"
    assert entry["scan_image_path"] == "scan_ingest_data/media/cards/bad.jpg"


def test_revert_puts_every_path_back(session, monkeypatch, tmp_path):
    monkeypatch.setattr(swap, "JOURNAL_DIR", tmp_path)
    _item(session, "bad", "scan_ingest_data/media/cards/bad.jpg")
    journal = swap.apply(session, [_flag("bad", path="scan_ingest_data/media/cards/bad.jpg")], "tester")

    assert swap.revert(session, journal, "tester") == 1

    session.expire_all()
    assert session.get(InventoryItem, "bad").scan_image_path == "scan_ingest_data/media/cards/bad.jpg"


def test_revert_never_overwrites_a_photo_added_since(session, monkeypatch, tmp_path):
    """A photo re-taken after the swap is newer than the journal. Restoring
    over it would silently undo the fix the swap was meant to prompt."""
    monkeypatch.setattr(swap, "JOURNAL_DIR", tmp_path)
    _item(session, "bad", "old.jpg")
    journal = swap.apply(session, [_flag("bad", path="old.jpg")], "tester")

    session.get(InventoryItem, "bad").scan_image_path = "rescanned.jpg"
    session.commit()

    assert swap.revert(session, journal, "tester") == 0
    session.expire_all()
    assert session.get(InventoryItem, "bad").scan_image_path == "rescanned.jpg"


def test_the_journal_is_written_before_the_rows_change(session, monkeypatch, tmp_path):
    """A journal that doesn't exist because the process died mid-write is a
    swap nobody can undo, so it must land first."""
    monkeypatch.setattr(swap, "JOURNAL_DIR", tmp_path)
    _item(session, "bad", "old.jpg")
    seen = {}
    real_get = session.get

    def spy(model, ident):
        seen.setdefault("journal_existed", any(tmp_path.glob("photo_audit_stock_swap-*.json")))
        return real_get(model, ident)

    monkeypatch.setattr(session, "get", spy)
    swap.apply(session, [_flag("bad", path="old.jpg")], "tester")

    assert seen["journal_existed"] is True
