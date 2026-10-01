#!/usr/bin/env python
"""Add generic collectible watches, listing matches and alert-email ledger.

The migration is additive. Existing market listings are preserved; Sonny
Angel rows are linked to a seeded watch so the former dedicated dashboard
continues as the first generic collectible watch.

    .venv/bin/python -m scripts.migrations.migrate_add_market_discovery --dry-run
    .venv/bin/python -m scripts.migrations.migrate_add_market_discovery
"""

import argparse

from sqlalchemy import inspect, select

from automation_control import models  # noqa: F401 -- register metadata
from automation_control.database import Base, SessionLocal, engine
from automation_control.models import MarketListing, MarketListingMatch, MarketWatchTerm


NEW_TABLES = ["market_watch_terms", "market_listing_matches", "market_alert_imports"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    existing = set(inspect(engine).get_table_names())
    to_create = [name for name in NEW_TABLES if name not in existing]
    print(f"tables to create: {to_create or 'none'}")
    print("seed/backfill: Sonny Angel AU/JP watch and links to existing matching listings")
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0

    Base.metadata.create_all(
        engine,
        tables=[table for table in Base.metadata.sorted_tables if table.name in to_create],
    )
    with SessionLocal.begin() as session:
        term = session.scalar(select(MarketWatchTerm).where(MarketWatchTerm.query == "Sonny Angel"))
        if term is None:
            term = MarketWatchTerm(
                name="Sonny Angel",
                query="Sonny Angel",
                category="Figures",
                countries=["AU", "JP"],
                sources=["ebay", "public_web"],
                active=True,
            )
            session.add(term)
            session.flush()

        linked = 0
        listings = session.scalars(
            select(MarketListing).where(MarketListing.title.ilike("%sonny angel%")),
        ).all()
        for listing in listings:
            key = {"watch_term_id": term.id, "market_listing_id": listing.id}
            if session.get(MarketListingMatch, (term.id, listing.id)) is None:
                session.add(MarketListingMatch(**key, matched_by="migration"))
                linked += 1
    print(f"seeded watch: Sonny Angel; linked listings: {linked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
