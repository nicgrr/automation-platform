import os
import subprocess
import sys

import pytest

from scripts import release_rollback_drill
from scripts.restore_drill import PROJECT_ROOT


def _blank_database_at_revision(path, revision):
    environment = os.environ.copy()
    environment["AUTOMATION_DATABASE_URL"] = f"sqlite:///{path}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )


def test_tagged_release_boots_against_its_matching_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot.sqlite"
    _blank_database_at_revision(snapshot, "20261002_03")

    result = release_rollback_drill.run("ezbay-v2026.10.02.2", snapshot)

    assert result["tag"] == "ezbay-v2026.10.02.2"
    assert result["application_health"] == "ok"
    assert result["database_revision"] == "20261002_03"


def test_unknown_release_tag_is_rejected(tmp_path):
    snapshot = tmp_path / "snapshot.sqlite"
    snapshot.touch()

    with pytest.raises(RuntimeError):
        release_rollback_drill.run("not-a-real-release", snapshot)


def test_option_shaped_tag_is_rejected_before_git(tmp_path):
    with pytest.raises(RuntimeError, match="release tag name"):
        release_rollback_drill.run("--help", tmp_path / "snapshot.sqlite")
