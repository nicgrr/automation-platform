import os
import subprocess
import sys

from scripts import restore_drill
from automation_control.release_info import expected_database_revision


def _blank_database_at_head(path):
    environment = os.environ.copy()
    environment["AUTOMATION_DATABASE_URL"] = f"sqlite:///{path}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=restore_drill.PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )


def test_restore_drill_checks_migrates_and_boots_a_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot.sqlite"
    status_file = tmp_path / "status.json"
    _blank_database_at_head(snapshot)

    result = restore_drill.run(snapshot, status_file)

    assert result["status"] == "success"
    assert result["database_revision"] == expected_database_revision()
    assert result["integrity"] == "ok"
    assert result["foreign_key_violations"] == 0
    assert result["application_health"] == "ok"
    assert status_file.is_file()


def test_restore_drill_records_a_corrupt_snapshot_failure(tmp_path):
    snapshot = tmp_path / "corrupt.sqlite"
    status_file = tmp_path / "status.json"
    snapshot.write_text("not a database", encoding="utf-8")

    result = restore_drill.run(snapshot, status_file)

    assert result["status"] == "failed"
    assert result["error"]
    assert status_file.is_file()


def test_latest_snapshot_selects_the_newest_database_file(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    new = tmp_path / "new.sqlite"
    ignored = tmp_path / "notes.txt"
    for path in (old, new, ignored):
        path.write_text("x", encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    os.utime(ignored, (3, 3))
    monkeypatch.setattr(restore_drill, "BACKUP_DIR", tmp_path)

    assert restore_drill._latest_snapshot() == new
