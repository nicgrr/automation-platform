from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import AuditEvent, CardSet, CardVariant, CatalogCard, InventoryItem, ScanSession
from automation_control.scan_ingest.commit import CommitRefused, commit_card, inventory_for_set, inventory_totals
from automation_control.scan_ingest.identify import Identification, Source


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CardSet(id="xy11", name="Steam Siege", code="STS", printed_total=114))
        db.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
        db.add(CatalogCard(id="xy11-29", set_id="xy11", number="29", name="Gastrodon"))
        db.commit()
        yield db


def _scan_image(tmp_path, name="scan.jpg", size=(750, 1050)):
    path = tmp_path / name
    Image.fromarray(np.full((size[1], size[0], 3), 128, dtype=np.uint8)).save(path)
    return path


def _confident(card_id="xy11-31") -> Identification:
    return Identification(card_id=card_id, number="31", name="Dewott", source=Source.BOTH, confidence=1.0)


def _needs_confirmation() -> Identification:
    return Identification(card_id="xy11-31", number="31", name="Dewott", source=Source.PHASH, confidence=0.5)


# --- the safety contract ---

def test_commit_refuses_unidentified_card(session, tmp_path):
    unidentified = Identification(None, None, None, Source.NONE, 0.0)
    with pytest.raises(CommitRefused, match="unidentified"):
        commit_card(session, unidentified, _scan_image(tmp_path), tmp_path / "media")


def test_commit_refuses_low_confidence_by_default(session, tmp_path):
    """The enforcement point: a caller that forgets to check the flag still
    cannot write a guess into inventory."""
    with pytest.raises(CommitRefused, match="needs confirmation"):
        commit_card(session, _needs_confirmation(), _scan_image(tmp_path), tmp_path / "media")

    assert session.scalars(select(InventoryItem)).all() == []


def test_commit_allows_low_confidence_once_a_human_confirms(session, tmp_path):
    result = commit_card(session, _needs_confirmation(), _scan_image(tmp_path), tmp_path / "media", allow_unconfirmed=True)
    assert result.created
    assert result.quantity == 1


def test_commit_refuses_card_missing_from_catalog(session, tmp_path):
    orphan = Identification(card_id="xy11-999", number="999", name="Ghost", source=Source.BOTH, confidence=1.0)
    with pytest.raises(CommitRefused, match="no catalog card"):
        commit_card(session, orphan, _scan_image(tmp_path), tmp_path / "media")


# --- create vs increment ---

def test_commit_creates_new_inventory_row(session, tmp_path):
    result = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media")

    assert result.created
    assert result.quantity == 1
    assert result.card_name == "Dewott"

    items = session.scalars(select(InventoryItem)).all()
    assert len(items) == 1
    assert items[0].card_id == "xy11-31"
    assert items[0].variant is CardVariant.NORMAL
    assert items[0].condition == "Near Mint"


def test_commit_increments_instead_of_duplicating(session, tmp_path):
    for _ in range(3):
        result = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media")

    assert not result.created
    assert result.quantity == 3
    items = session.scalars(select(InventoryItem)).all()
    assert len(items) == 1  # one holding, quantity 3 -- not three rows
    assert items[0].quantity == 3


def test_commit_separates_rows_by_variant(session, tmp_path):
    """Same card, different printing -- distinct holdings with distinct
    values, so they must not merge."""
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", variant=CardVariant.NORMAL)
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", variant=CardVariant.REVERSE_HOLO)

    items = session.scalars(select(InventoryItem)).all()
    assert len(items) == 2
    assert {i.variant for i in items} == {CardVariant.NORMAL, CardVariant.REVERSE_HOLO}
    assert all(i.quantity == 1 for i in items)


def test_commit_separates_rows_by_condition(session, tmp_path):
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", condition="Near Mint")
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", condition="Played")

    assert len(session.scalars(select(InventoryItem)).all()) == 2


def test_commit_separates_rows_by_card(session, tmp_path):
    commit_card(session, _confident("xy11-31"), _scan_image(tmp_path), tmp_path / "media")
    gastrodon = Identification(card_id="xy11-29", number="29", name="Gastrodon", source=Source.BOTH, confidence=1.0)
    commit_card(session, gastrodon, _scan_image(tmp_path), tmp_path / "media")

    assert len(session.scalars(select(InventoryItem)).all()) == 2


# --- scan image handling ---

def test_commit_stores_image_under_predictable_name(session, tmp_path):
    result = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media")

    item = session.get(InventoryItem, result.item_id)
    assert item.scan_image_path.endswith("STS-31-normal-near-mint.jpg")  # set, number, variant, condition
    assert (tmp_path / "media" / "cards" / "STS-31-normal-near-mint.jpg").exists()


def test_different_conditions_do_not_share_an_image_file(session, tmp_path):
    """Regression: keying the filename on card+variant only meant a Played
    scan silently overwrote the Near Mint photo of the same card, leaving
    two inventory rows pointing at one (wrong) image."""
    near_mint = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", condition="Near Mint")
    played = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", condition="Played")

    nm_path = session.get(InventoryItem, near_mint.item_id).scan_image_path
    pl_path = session.get(InventoryItem, played.item_id).scan_image_path
    assert nm_path != pl_path
    assert Path(nm_path).exists() and Path(pl_path).exists()


def test_different_variants_do_not_share_an_image_file(session, tmp_path):
    normal = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", variant=CardVariant.NORMAL)
    holo = commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", variant=CardVariant.HOLO)

    assert session.get(InventoryItem, normal.item_id).scan_image_path != session.get(InventoryItem, holo.item_id).scan_image_path


def test_replacing_an_image_overwrites_in_place_without_orphans(session, tmp_path):
    """A better rescan should replace the stored photo, not accumulate a
    second file that nothing references."""
    commit_card(session, _confident(), _scan_image(tmp_path, "low.jpg", size=(375, 525)), tmp_path / "media")
    result = commit_card(session, _confident(), _scan_image(tmp_path, "high.jpg", size=(1500, 2100)), tmp_path / "media")

    assert result.image_replaced
    files = list((tmp_path / "media" / "cards").iterdir())
    assert len(files) == 1  # replaced in place
    with Image.open(files[0]) as image:
        assert image.size == (1500, 2100)


def test_commit_replaces_image_when_rescan_is_higher_resolution(session, tmp_path):
    """A 600 DPI rescan should win over an earlier low-DPI capture, since
    this image is what a listing ends up using."""
    low = _scan_image(tmp_path, "low.jpg", size=(375, 525))
    commit_card(session, _confident(), low, tmp_path / "media")

    high = _scan_image(tmp_path, "high.jpg", size=(1500, 2100))
    result = commit_card(session, _confident(), high, tmp_path / "media")

    assert result.image_replaced
    item = session.get(InventoryItem, result.item_id)
    with Image.open(item.scan_image_path) as image:
        assert image.size == (1500, 2100)


def test_commit_keeps_image_when_rescan_is_lower_resolution(session, tmp_path):
    high = _scan_image(tmp_path, "high.jpg", size=(1500, 2100))
    commit_card(session, _confident(), high, tmp_path / "media")

    low = _scan_image(tmp_path, "low.jpg", size=(375, 525))
    result = commit_card(session, _confident(), low, tmp_path / "media")

    assert not result.image_replaced
    item = session.get(InventoryItem, result.item_id)
    with Image.open(item.scan_image_path) as image:
        assert image.size == (1500, 2100)


# --- session accounting and audit ---

def test_commit_updates_session_running_count(session, tmp_path):
    scan_session = ScanSession(set_id="xy11", batch_variant=CardVariant.NORMAL)
    session.add(scan_session)
    session.commit()

    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", scan_session=scan_session)
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", scan_session=scan_session)

    assert scan_session.cards_committed == 2
    item = session.scalars(select(InventoryItem)).one()
    assert item.session_id == scan_session.id


def test_commit_records_an_audit_event(session, tmp_path):
    commit_card(session, _confident(), _scan_image(tmp_path), tmp_path / "media", user="nic")

    events = session.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.commit")).all()
    assert len(events) == 1
    assert events[0].actor_id == "nic"
    assert events[0].outcome == "success"
    assert events[0].details["card_name"] == "Dewott"
    assert events[0].details["created"] is True


def test_refused_commit_writes_nothing_at_all(session, tmp_path):
    with pytest.raises(CommitRefused):
        commit_card(session, _needs_confirmation(), _scan_image(tmp_path), tmp_path / "media")

    assert session.scalars(select(InventoryItem)).all() == []
    assert session.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.commit")).all() == []


# --- queries ---

def test_inventory_totals_counts_holdings_and_copies(session, tmp_path):
    commit_card(session, _confident("xy11-31"), _scan_image(tmp_path), tmp_path / "media")
    commit_card(session, _confident("xy11-31"), _scan_image(tmp_path), tmp_path / "media")
    gastrodon = Identification(card_id="xy11-29", number="29", name="Gastrodon", source=Source.BOTH, confidence=1.0)
    commit_card(session, gastrodon, _scan_image(tmp_path), tmp_path / "media")

    holdings, copies = inventory_totals(session, "xy11")
    assert (holdings, copies) == (2, 3)


def test_inventory_for_set_is_ordered_and_scoped(session, tmp_path):
    commit_card(session, _confident("xy11-31"), _scan_image(tmp_path), tmp_path / "media")
    gastrodon = Identification(card_id="xy11-29", number="29", name="Gastrodon", source=Source.BOTH, confidence=1.0)
    commit_card(session, gastrodon, _scan_image(tmp_path), tmp_path / "media")

    items = inventory_for_set(session, "xy11")
    assert [i.card.number for i in items] == ["29", "31"]
    assert inventory_for_set(session, "xy12") == []
