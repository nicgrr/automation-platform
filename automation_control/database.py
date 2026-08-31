from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

# Several processes share this database at once: the web app, the
# always-on scan-ingest service, and one-off scripts (cache-set, backfills).
# SQLite's defaults do not cope with that -- a long write, such as caching a
# set's few hundred cards, blocked every other writer and surfaced as
# "database is locked" (a 500 on the review page while the bulk cache ran).
#
# WAL lets readers carry on during a write instead of blocking, and
# busy_timeout makes a writer wait its turn rather than failing instantly.
# Thirty seconds comfortably covers the longest write here.
SQLITE_BUSY_TIMEOUT_MS = 30_000


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    database_url = url or get_settings().automation_database_url
    is_sqlite = database_url.startswith("sqlite")
    kwargs = {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000} if is_sqlite else {}
    created = create_engine(database_url, connect_args=kwargs)

    if is_sqlite:
        @event.listens_for(created, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            # busy_timeout first, and it must be: switching journal mode
            # needs an exclusive lock, so on a database that is already busy
            # the switch itself fails instantly without a timeout set.
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            try:
                # WAL is a property of the database file, not the connection,
                # so setting it on any connection is enough -- done here so a
                # fresh database gets it without a separate migration step.
                cursor.execute("PRAGMA journal_mode=WAL")
                # NORMAL is the documented pairing with WAL: still crash-safe,
                # without an fsync on every commit.
                cursor.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                # Couldn't switch (another process holds it, or the file is on
                # a filesystem that won't support WAL). Not fatal: busy_timeout
                # alone already turns "database is locked" into "wait your
                # turn", which is the failure this exists to prevent.
                pass
            cursor.close()

    return created


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
