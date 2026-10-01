"""Add the idempotent capture-to-Bulk-Upload handoff table.

    .venv/bin/python -m scripts.migrations.migrate_add_capture_promotions --dry-run
    .venv/bin/python -m scripts.migrations.migrate_add_capture_promotions
"""

import argparse

from sqlalchemy import inspect

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine


TABLE = "capture_promotions"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    exists = TABLE in inspect(engine).get_table_names()
    print(f"{TABLE}: {'already present' if exists else 'will create'}")
    if args.dry_run or exists:
        return 0
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[TABLE]])
    print("created capture_promotions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
