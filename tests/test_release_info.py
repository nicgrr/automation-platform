import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from automation_control.release_info import expected_database_revision, require_database_revision


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    return Session(engine)


def test_repository_has_one_expected_alembic_head():
    assert expected_database_revision() == "20261002_08"


def test_unversioned_database_is_rejected():
    with _session() as session:
        with pytest.raises(RuntimeError, match="not Alembic-baselined"):
            require_database_revision(session, "20261001_02")


def test_wrong_database_revision_is_rejected():
    with _session() as session:
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        session.execute(text("INSERT INTO alembic_version VALUES ('old_revision')"))
        with pytest.raises(RuntimeError, match="does not match"):
            require_database_revision(session, "20261001_02")


def test_matching_database_revision_is_accepted():
    with _session() as session:
        session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        session.execute(text("INSERT INTO alembic_version VALUES ('20261001_02')"))
        assert require_database_revision(session, "20261001_02") == "20261001_02"
