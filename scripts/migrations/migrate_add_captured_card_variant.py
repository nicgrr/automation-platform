"""Add the physical printing selected before capture-to-inventory handoff.

    .venv/bin/python -m scripts.migrations.migrate_add_captured_card_variant --dry-run
    .venv/bin/python -m scripts.migrations.migrate_add_captured_card_variant
"""

import argparse
from sqlalchemy import inspect, text
from automation_control.database import engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    columns = {column["name"] for column in inspect(engine).get_columns("captured_cards")}
    if "variant" in columns:
        print("captured_cards.variant: already present")
        return 0
    print("captured_cards.variant: will add")
    if args.dry_run:
        return 0
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE captured_cards ADD COLUMN variant VARCHAR(18)"))
    print("added captured_cards.variant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
