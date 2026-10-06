#!/usr/bin/env python3
"""Resolve EzBay catalogue items to already-downloaded TCGCSV products."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from automation_control.database import SessionLocal
from automation_control.pricing.tcgcsv_mapping import resolve_tcgcsv_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="map the full catalogue instead of owned products only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        summary = resolve_tcgcsv_ids(session, only_owned=not args.all)
        print(json.dumps({**asdict(summary), "committed": not args.dry_run}, indent=2))
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
