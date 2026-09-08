"""One-time migration: add social_links and contact_messages.

Seeds three social_links rows (Instagram, eBay store, Whatnot) with a
blank url -- the footer skips any link with no url rather than rendering
one that goes nowhere, so this is safe to run before the real links are
known. Fill them in with scripts/set_social_link.py once you have them.

    .venv/bin/python -m scripts.migrate_add_social_and_contact --dry-run
    .venv/bin/python -m scripts.migrate_add_social_and_contact
"""

import argparse

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from automation_control import models  # noqa: F401
from automation_control.database import Base, engine
from automation_control.models import SocialLink

NEW_TABLES = ["social_links", "contact_messages"]

DEFAULT_PLATFORMS = [
    ("instagram", "Instagram", 0),
    ("ebay", "eBay store", 1),
    ("whatnot", "Whatnot", 2),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [t for t in NEW_TABLES if t not in existing]
    print(f"tables to create: {to_create or 'none'}")
    if args.dry_run:
        print(f"would seed {len(DEFAULT_PLATFORMS)} social platform placeholder(s) if social_links is new.")
        print("\n--dry-run: nothing written.")
        return 0

    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in to_create])

    with Session(engine) as session:
        if session.scalar(select(SocialLink).limit(1)) is None:
            for platform, label, sort_order in DEFAULT_PLATFORMS:
                session.add(SocialLink(platform=platform, label=label, url=None,
                                       sort_order=sort_order, is_active=True))
            session.commit()
            print(f"seeded {len(DEFAULT_PLATFORMS)} platform placeholder(s) -- no url yet, so none will render.")
        else:
            print("social_links already has rows; left untouched.")

    print("done. Set the real URLs with:\n"
          "  .venv/bin/python -m scripts.set_social_link instagram https://instagram.com/yourhandle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
