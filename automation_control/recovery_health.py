"""Truthful backup and restore-drill freshness for operator health views."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


BACKUP_STATUS_PATH = Path("/var/lib/restic-status/last-backup.json")
RESTORE_DRILL_STATUS_PATH = Path("scan_ingest_data/status/last-restore-drill.json")
BACKUP_MAX_AGE = timedelta(hours=36)
RESTORE_DRILL_MAX_AGE = timedelta(days=45)


@dataclass(frozen=True)
class RecoveryStatus:
    label: str
    healthy: bool
    detail: str
    finished_at: datetime | None = None

    @property
    def needs_attention(self) -> bool:
        return not self.healthy


def _read_status(path: Path) -> tuple[dict | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no status has been recorded"
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None, "status file is unreadable"
    if not isinstance(value, dict):
        return None, "status file has an invalid shape"
    return value, None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    return None


def _age_words(age: timedelta) -> str:
    seconds = max(0, int(age.total_seconds()))
    if seconds < 2 * 60 * 60:
        return f"{max(1, seconds // 60)} min ago"
    if seconds < 2 * 24 * 60 * 60:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _status(
    label: str,
    path: Path,
    max_age: timedelta,
    now: datetime,
) -> RecoveryStatus:
    value, problem = _read_status(path)
    if problem:
        return RecoveryStatus(label, False, problem)
    assert value is not None

    finished_at = _timestamp(value.get("finished_at"))
    if value.get("status") != "success":
        return RecoveryStatus(label, False, "last attempt failed", finished_at)
    if finished_at is None:
        return RecoveryStatus(label, False, "successful status has no valid completion time")

    age = now - finished_at
    if age < timedelta(minutes=-5):
        return RecoveryStatus(label, False, "completion time is in the future", finished_at)
    age = max(age, timedelta())
    age_text = _age_words(age)
    if age > max_age:
        return RecoveryStatus(label, False, f"last success was {age_text}", finished_at)
    return RecoveryStatus(label, True, f"last success {age_text}", finished_at)


def recovery_statuses(
    now: datetime | None = None,
    *,
    backup_path: Path = BACKUP_STATUS_PATH,
    restore_drill_path: Path = RESTORE_DRILL_STATUS_PATH,
) -> list[RecoveryStatus]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return [
        _status("Encrypted backup", backup_path, BACKUP_MAX_AGE, now),
        _status("Restore drill", restore_drill_path, RESTORE_DRILL_MAX_AGE, now),
    ]
