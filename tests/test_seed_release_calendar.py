from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from automation_control.database import Base
from automation_control.models import ReleaseCalendarEntry
from scripts import seed_release_calendar


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_seeds_both_confirmed_releases(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(seed_release_calendar, "SessionLocal", factory)

    assert seed_release_calendar.main([]) == 0

    with factory() as session:
        names = {e.product_name for e in session.scalars(select(ReleaseCalendarEntry))}
    assert "Pokemon TCG 30th Celebration Elite Trainer Box" in names
    assert "Pokemon TCG Mega Evolution: Delta Reign Elite Trainer Box" in names


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(seed_release_calendar, "SessionLocal", factory)

    seed_release_calendar.main(["--dry-run"])

    with factory() as session:
        assert session.scalars(select(ReleaseCalendarEntry)).all() == []


def test_re_running_does_not_duplicate(tmp_path, monkeypatch):
    factory = _session_factory(tmp_path)
    monkeypatch.setattr(seed_release_calendar, "SessionLocal", factory)

    seed_release_calendar.main([])
    seed_release_calendar.main([])

    with factory() as session:
        count = len(session.scalars(select(ReleaseCalendarEntry)).all())
    assert count == len(seed_release_calendar.RELEASES)
