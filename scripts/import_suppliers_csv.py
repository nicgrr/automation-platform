"""Bulk-import suppliers from a CSV file -- the import half of Module 20
(export lives in the app itself at /export; import is a CLI script, same
convention as import_tcg_catalog_csv.py, since this is an occasional
back-office task rather than something done from the dashboard).

CSV columns (header row required; everything but name is optional and may
be blank):

    name,contact,website,categories,account_status,wholesale_discount_pct,minimum_order
    Big Wholesale Co,sales@bigwholesale.com,https://bigwholesale.com,TCG,active,15,250

account_status must be one of: researching, apply, application_sent,
approved, active, paused, declined (case-insensitive) -- defaults to
"researching" if blank.

Matching is by exact name (case-insensitive) -- re-importing the same
supplier by name updates it instead of creating a duplicate. Suppliers
have no other natural key to key off (unlike catalogue items, which have
game/set/number).

    .venv/bin/python -m scripts.import_suppliers_csv --file suppliers.csv --dry-run
    .venv/bin/python -m scripts.import_suppliers_csv --file suppliers.csv
"""

import argparse
import csv
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import func, select

from automation_control.database import SessionLocal
from automation_control.models import Supplier, SupplierStatus

REQUIRED_COLUMNS = {"name"}


def _dec(value: str) -> Decimal | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV is missing required column(s): {sorted(missing)}")
        return [row for row in reader if any((row.get(k) or "").strip() for k in row)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, type=Path, help="path to the CSV file")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    if not args.file.exists():
        raise SystemExit(f"no such file: {args.file}")
    rows = _read_rows(args.file)
    if not rows:
        print("no data rows found")
        return 0

    created = updated = skipped = 0
    with SessionLocal() as session:
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                print(f"  skip (missing name): {row}")
                skipped += 1
                continue

            status_raw = (row.get("account_status") or "").strip().lower()
            try:
                account_status = SupplierStatus(status_raw) if status_raw else SupplierStatus.RESEARCHING
            except ValueError:
                print(f"  skip (invalid account_status {status_raw!r}): {name}")
                skipped += 1
                continue

            existing = session.scalar(select(Supplier).where(func.lower(Supplier.name) == name.lower()))
            action = "update" if existing else "create"
            print(f"  {action}: {name}")
            if args.dry_run:
                continue

            if existing is None:
                existing = Supplier(id=str(uuid.uuid4()), name=name)
                session.add(existing)
                created += 1
            else:
                updated += 1

            existing.name = name
            existing.contact = (row.get("contact") or "").strip() or None
            existing.website = (row.get("website") or "").strip() or None
            existing.categories = (row.get("categories") or "").strip() or None
            existing.account_status = account_status
            existing.wholesale_discount_pct = _dec(row.get("wholesale_discount_pct") or "")
            existing.minimum_order = _dec(row.get("minimum_order") or "")

        if args.dry_run:
            print(f"\n--dry-run: {len(rows)} row(s) read, nothing written.")
            return 0

        session.commit()
    print(f"\nDone: {created} created, {updated} updated, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
