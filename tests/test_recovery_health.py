import json
from datetime import UTC, datetime, timedelta

from automation_control.recovery_health import recovery_statuses


NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


def _write(path, **value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_recent_backup_and_restore_evidence_are_healthy(tmp_path):
    backup = tmp_path / "backup.json"
    restore = tmp_path / "restore.json"
    _write(backup, status="success", finished_at=(NOW - timedelta(hours=2)).timestamp())
    _write(restore, status="success", finished_at=(NOW - timedelta(days=10)).isoformat())

    statuses = recovery_statuses(NOW, backup_path=backup, restore_drill_path=restore)

    assert all(status.healthy for status in statuses)
    assert statuses[0].detail == "last success 2h ago"
    assert statuses[1].detail == "last success 10d ago"


def test_old_backup_success_is_attention_worthy(tmp_path):
    backup = tmp_path / "backup.json"
    restore = tmp_path / "restore.json"
    _write(backup, status="success", finished_at=(NOW - timedelta(hours=40)).timestamp())
    _write(restore, status="success", finished_at=NOW.timestamp())

    statuses = recovery_statuses(NOW, backup_path=backup, restore_drill_path=restore)

    assert statuses[0].needs_attention
    assert statuses[0].detail == "last success was 40h ago"


def test_failed_or_missing_status_is_attention_worthy(tmp_path):
    backup = tmp_path / "backup.json"
    _write(backup, status="failed", finished_at=NOW.timestamp())

    statuses = recovery_statuses(
        NOW, backup_path=backup, restore_drill_path=tmp_path / "missing.json"
    )

    assert statuses[0].detail == "last attempt failed"
    assert statuses[1].detail == "no status has been recorded"
    assert all(status.needs_attention for status in statuses)


def test_invalid_status_does_not_crash_health_reporting(tmp_path):
    backup = tmp_path / "backup.json"
    restore = tmp_path / "restore.json"
    backup.write_text("not-json", encoding="utf-8")
    _write(restore, status="success", finished_at="not-a-time")

    statuses = recovery_statuses(NOW, backup_path=backup, restore_drill_path=restore)

    assert statuses[0].detail == "status file is unreadable"
    assert statuses[1].detail == "successful status has no valid completion time"
