#!/usr/bin/env python3
"""Import TCGCSV's daily bulk pricing into EzBay's local database."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import TcgCard
from automation_control.pricing.providers.tcgcsv import CATEGORY_NAMES, TcgCsvClient
from automation_control.pricing.tcgcsv_mapping import resolve_tcgcsv_ids
from automation_control.pricing.tcgcsv_sync import sync_tcgcsv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--games",
        help="comma-separated game keys; defaults to supported games already present in EzBay's catalogue",
    )
    parser.add_argument("--max-groups", type=int, help="limit groups for a smoke test")
    parser.add_argument("--force", action="store_true", help="resync even if the upstream build timestamp is unchanged")
    parser.add_argument("--dry-run", action="store_true", help="perform downloads and validation, then roll back database changes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with SessionLocal() as session:
        if args.games:
            games = {value.strip() for value in args.games.split(",") if value.strip()}
        else:
            games = set(session.scalars(select(TcgCard.game).distinct())) & set(CATEGORY_NAMES)
        if not games:
            raise SystemExit("No supported TCGCSV games found; pass --games explicitly")
        with TcgCsvClient() as client:
            summary = sync_tcgcsv(
                session,
                client,
                game_keys=games,
                force=args.force,
                max_groups=args.max_groups,
                commit_batches=not args.dry_run,
            )
        # This is entirely local and intentionally runs even when TCGCSV's
        # build is unchanged: cards scanned since yesterday can still gain a
        # stable provider ID without another bulk download or external call.
        mapping = resolve_tcgcsv_ids(session, only_owned=True)
        print(json.dumps({
            "skipped": summary.skipped,
            "source_updated_at": summary.source_updated_at.isoformat(),
            "categories": summary.categories,
            "groups": summary.groups,
            "products": summary.products,
            "quotes_added": summary.quotes_added,
            "requests": summary.requests,
            "failures": summary.failures,
            "games": sorted(games),
            "mapping": asdict(mapping),
            "committed": not args.dry_run,
        }, indent=2))
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
        return 0 if summary.failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
