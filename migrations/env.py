from __future__ import annotations

from logging.config import fileConfig
import os

from alembic import context
import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

from automation_control.database import Base
import automation_control.models  # noqa: F401 -- registers every ORM table

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Match the application's environment-controlled database rather than silently
# migrating alembic.ini's development default in production.
database_url = os.environ.get("AUTOMATION_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def compare_type(migration_context, _inspected_column, _metadata_column,
                 inspected_type, metadata_type):
    """Ignore representation-only SQLite differences.

    Historical hand-written migrations used VARCHAR for Python enums and
    SQLite reflects TIMESTAMP/DateTime variants differently. The ORM still
    performs enum conversion and SQLite gives both date spellings the same
    affinity, so these are not schema changes and must not trigger a table
    rebuild. Other type differences retain Alembic's normal comparison.
    """
    if migration_context.dialect.name == "sqlite":
        if isinstance(metadata_type, sa.Enum) and isinstance(inspected_type, sa.String):
            return False
        if isinstance(metadata_type, sa.DateTime) and isinstance(inspected_type, sa.DateTime):
            return False
    return None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=compare_type,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=compare_type)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
