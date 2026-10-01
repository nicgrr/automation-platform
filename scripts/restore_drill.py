"""Validate a real EZBay snapshot without touching production.

The newest manual SQLite snapshot is copied to a temporary directory, checked,
migrated to the current Alembic head, checked again, and booted through the
application health endpoint. A small status record is written atomically for the
dashboard and CRM health report.

    .venv/bin/python -m scripts.restore_drill
    .venv/bin/python -m scripts.restore_drill --snapshot backups/example.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = PROJECT_ROOT / "backups"
STATUS_FILE = PROJECT_ROOT / "scan_ingest_data/status/last-restore-drill.json"
SNAPSHOT_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
COUNT_TABLES = ("catalog_items", "inventory_items", "card_prices", "shop_listings")


def _latest_snapshot() -> Path:
    candidates = [
        path for path in BACKUP_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SNAPSHOT_SUFFIXES
    ]
    if not candidates:
        raise RuntimeError(f"no SQLite snapshots found in {BACKUP_DIR}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _inspect_database(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in COUNT_TABLES if table in tables
        }
        revision_row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone() if "alembic_version" in tables else None
    if integrity != "ok":
        raise RuntimeError(f"integrity_check returned {integrity!r}")
    if foreign_key_violations:
        raise RuntimeError(f"foreign_key_check found {foreign_key_violations} violation(s)")
    return {
        "integrity": integrity,
        "foreign_key_violations": foreign_key_violations,
        "counts": counts,
        "database_revision": revision_row[0] if revision_row else None,
    }


def _run_checked(command: list[str], database_url: str) -> None:
    environment = os.environ.copy()
    environment["AUTOMATION_DATABASE_URL"] = database_url
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"command exited {result.returncode}")


def _write_status(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.chmod(0o644)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run(snapshot: Path, status_file: Path = STATUS_FILE) -> dict:
    started_at = int(time.time())
    status: dict = {
        "status": "failed",
        "started_at": started_at,
        "finished_at": started_at,
        "source_snapshot": snapshot.name,
    }
    try:
        if not snapshot.is_file():
            raise RuntimeError(f"snapshot does not exist: {snapshot}")
        with tempfile.TemporaryDirectory(prefix="ezbay-restore-drill.") as directory:
            restored = Path(directory) / "restored.sqlite"
            shutil.copy2(snapshot, restored)
            before = _inspect_database(restored)
            database_url = f"sqlite:///{restored}"
            _run_checked([sys.executable, "-m", "alembic", "upgrade", "head"], database_url)
            after = _inspect_database(restored)
            if before["counts"] != after["counts"]:
                raise RuntimeError("row counts changed while migrating the restored snapshot")
            health_program = (
                "from fastapi.testclient import TestClient; "
                "from automation_control.api import app; "
                "response=TestClient(app).get('/health'); "
                "assert response.status_code == 200, response.text"
            )
            _run_checked([sys.executable, "-c", health_program], database_url)

        status.update({
            "status": "success",
            "finished_at": int(time.time()),
            "database_revision_before": before["database_revision"],
            "database_revision": after["database_revision"],
            "integrity": after["integrity"],
            "foreign_key_violations": after["foreign_key_violations"],
            "counts": after["counts"],
            "application_health": "ok",
        })
    except Exception as exc:
        status.update({"finished_at": int(time.time()), "error": str(exc)[:500]})
    _write_status(status_file, status)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a non-destructive EZBay restore drill")
    parser.add_argument("--snapshot", type=Path, help="snapshot to test; defaults to the newest backup")
    parser.add_argument("--status-file", type=Path, default=STATUS_FILE)
    args = parser.parse_args(argv)

    try:
        snapshot = args.snapshot.resolve() if args.snapshot else _latest_snapshot()
        result = run(snapshot, args.status_file)
    except Exception as exc:
        print(f"Restore drill could not start: {exc}", file=sys.stderr)
        return 1

    if result["status"] == "success":
        counts = ", ".join(f"{name}={count}" for name, count in result["counts"].items())
        print(f"Restore drill passed at {result['database_revision']} ({counts}).")
        return 0
    print(f"Restore drill failed: {result.get('error', 'unknown error')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
