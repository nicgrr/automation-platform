"""Prove a previous release can boot with its matching pre-release snapshot.

This never changes the checkout or production database. It exports the requested
tag and copies the snapshot beneath a private temporary directory, then starts the
tagged application through its real lifespan/database-revision gate.

    .venv/bin/python -m scripts.release_rollback_drill \
        --tag ezbay-v2026.10.01.5 \
        --snapshot backups/automation-pre-inventory-canonical-20261002T053056.sqlite
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from scripts.restore_drill import PROJECT_ROOT, _inspect_database, _latest_snapshot


def _command(command: list[str], *, cwd: Path, environment: dict | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"command exited {result.returncode}")
    return result.stdout.strip()


def _resolve_tag(tag: str) -> str:
    if not tag or tag.startswith("-"):
        raise RuntimeError("tag must be a release tag name")
    commit = _command(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=PROJECT_ROOT,
    )
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError(f"could not resolve release tag {tag!r}")
    return commit


def run(tag: str, snapshot: Path) -> dict:
    commit = _resolve_tag(tag)
    snapshot = snapshot.resolve()
    if not snapshot.is_file():
        raise RuntimeError(f"snapshot does not exist: {snapshot}")

    with tempfile.TemporaryDirectory(prefix="ezbay-rollback-drill.") as directory:
        drill_dir = Path(directory)
        archive = drill_dir / "release.tar"
        source = drill_dir / "release"
        restored = drill_dir / "automation.db"
        source.mkdir()
        shutil.copy2(snapshot, restored)
        database = _inspect_database(restored)

        _command(
            ["git", "archive", "--format=tar", f"--output={archive}", commit],
            cwd=PROJECT_ROOT,
        )
        with tarfile.open(archive) as bundle:
            bundle.extractall(source, filter="data")

        environment = os.environ.copy()
        environment["AUTOMATION_DATABASE_URL"] = f"sqlite:///{restored}"
        environment.pop("PYTHONPATH", None)
        health_program = """\
from fastapi.testclient import TestClient
from automation_control.api import app
with TestClient(app) as client:
    response = client.get('/health')
    assert response.status_code == 200, response.text
"""
        _command([sys.executable, "-c", health_program], cwd=source, environment=environment)
        after = _inspect_database(restored)
        if database["counts"] != after["counts"]:
            raise RuntimeError("row counts changed while booting the rollback release")

    return {
        "tag": tag,
        "commit": commit,
        "database_revision": database["database_revision"],
        "counts": database["counts"],
        "application_health": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test a tagged EZBay release rollback")
    parser.add_argument("--tag", required=True, help="previous annotated release tag")
    parser.add_argument("--snapshot", type=Path, help="matching pre-release database snapshot")
    args = parser.parse_args(argv)
    try:
        result = run(args.tag, args.snapshot or _latest_snapshot())
    except Exception as exc:
        print(f"Release rollback drill failed: {exc}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{name}={count}" for name, count in result["counts"].items())
    print(
        f"Release rollback drill passed: {result['tag']} at database "
        f"{result['database_revision']} ({counts})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
