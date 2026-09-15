"""Seed the release calendar with the two confirmed upcoming English
Pokemon TCG main-series releases as of 2026-09-15.

The release calendar shipped as a flat add-only table with nothing in it
-- 0 rows, no way to fix a typo short of the database (see
business_records.py's own note on this). This is what makes it launch
useful rather than empty.

Cross-checked between two independent release-calendar sources
(tcgradar.eu, pokecottage.com) before landing here, the same rule every
other seeded date in this app follows -- a single search summary had
already gotten this wrong once this session (an invented Sonny Angel
figure that neither of two fetched sources actually listed).

Wholesale/ordered-quantity are deliberately left blank: those are this
seller's own business numbers, not something a release-date lookup can
know. Re-run is safe -- existing rows (matched by product_name) are left
alone rather than duplicated.

    .venv/bin/python -m scripts.seed_release_calendar --dry-run
    .venv/bin/python -m scripts.seed_release_calendar
"""

import argparse
import uuid

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import ReleaseCalendarEntry

# (product_name, release_date, supplier_deadline, retail_price)
# supplier_deadline is the pre-release/pre-order window's start where one
# was reported, so it reads as "order by" rather than left blank; retail
# is the reported English RRP where one was actually given, not guessed.
RELEASES: list[tuple[str, str, str | None, str | None]] = [
    ("Pokemon TCG 30th Celebration Elite Trainer Box", "2026-09-16", None, None),
    ("Pokemon TCG Mega Evolution: Delta Reign Elite Trainer Box", "2026-11-06", "2026-10-24", None),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would be added, write nothing")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        existing = {name for name, in session.execute(select(ReleaseCalendarEntry.product_name))}
        added = 0
        for name, release_date, deadline, retail in RELEASES:
            if name in existing:
                continue
            print(f"  {'would add' if args.dry_run else 'adding'}: {name} -- releases {release_date}"
                  + (f", order by {deadline}" if deadline else ""))
            if not args.dry_run:
                session.add(ReleaseCalendarEntry(
                    id=str(uuid.uuid4()), product_name=name, release_date=release_date,
                    supplier_deadline=deadline, retail_price=retail,
                ))
            added += 1
        if not args.dry_run and added:
            session.commit()
        print(f"\n{'would add' if args.dry_run else 'added'} {added} release(s); "
              f"{len(RELEASES) - added} already on the calendar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
