"""Small, read-only release identity helpers for the Settings page.

The service runs from a source checkout, so the Git commit is the only reliable
application version until a packaged deployment exists.  A dirty suffix is
important operational information: it says the running code cannot be reproduced
from that commit alone.  Schema identity comes from Alembic once baselined; older
databases are reported honestly as unversioned.
"""

from pathlib import Path
import subprocess

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[1]
QUERY_TIMEOUT_SECONDS = 3


def application_revision(repo_root: Path = REPO_ROOT) -> tuple[str, bool | None]:
    """Return (short commit, dirty), or ("unknown", None) outside a checkout."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS, check=False,
        )
        if commit.returncode != 0 or not commit.stdout.strip():
            return "unknown", None

        tracked = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--"], cwd=repo_root,
            capture_output=True, timeout=QUERY_TIMEOUT_SECONDS, check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_root,
            capture_output=True, text=True, timeout=QUERY_TIMEOUT_SECONDS, check=False,
        )
        if tracked.returncode not in (0, 1) or untracked.returncode != 0:
            return commit.stdout.strip(), None
        return commit.stdout.strip(), tracked.returncode == 1 or bool(untracked.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return "unknown", None


def database_revision(session: Session) -> str | None:
    """Current Alembic revision, or None for a pre-baseline database."""
    try:
        return session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
    except SQLAlchemyError:
        return None


def expected_database_revision(repo_root: Path = REPO_ROOT) -> str | None:
    """The single Alembic head shipped with this application checkout."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(repo_root / "alembic.ini"))
        config.set_main_option("script_location", str(repo_root / "migrations"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        # Settings must still render a useful warning if release metadata is
        # damaged. Startup turns this into a hard failure below.
        return None


def require_database_revision(session: Session, expected: str | None = None) -> str:
    """Refuse to run application code against an unknown/wrong schema."""
    expected = expected or expected_database_revision()
    if expected is None:
        raise RuntimeError("could not determine the application's expected database revision")
    current = database_revision(session)
    if current is None:
        raise RuntimeError(
            "database is not Alembic-baselined; run `alembic upgrade head` before starting EZBay"
        )
    if current != expected:
        raise RuntimeError(
            f"database revision {current!r} does not match application revision {expected!r}; "
            "run `alembic upgrade head` or deploy the matching application release"
        )
    return current
