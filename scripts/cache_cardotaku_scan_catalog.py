"""Cache a cardotaku.com-sourced reference catalogue for the scan service.

Pulls every single-card listing from cardotaku.com's public Shopify
catalogue for one game, downloads each card's photo, and pHashes it --
see automation_control/scan_ingest/cardotaku_catalog.py for why this
source (no official API exists for either game, and Super Dragon Ball
Heroes's one fan database sits behind a bot-challenge with no API).
Once cached, that game's sheets identify like any other: press the
scanner button.

Re-runnable anytime: it refreshes prices in place and only downloads
images it doesn't already have.

    .venv/bin/python -m scripts.cache_cardotaku_scan_catalog --game super_dragon_ball_heroes
    .venv/bin/python -m scripts.cache_cardotaku_scan_catalog --game dragon_ball_super_divers
"""

import argparse
from pathlib import Path

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.cardotaku_catalog import GAMES, HEADERS, cache_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True, choices=[*GAMES])
    parser.add_argument("--force", action="store_true", help="re-download images already on disk")
    args = parser.parse_args(argv)

    game = GAMES[args.game]
    cache_dir = Path(get_settings().scan_cache_dir)
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(timeout=30.0, headers=HEADERS, limits=limits) as client, SessionLocal() as session:
        print(f"{game.series_label}: fetching the catalogue...")
        results = cache_catalog(session, game, cache_dir, client, force=args.force)
        if not results:
            print("No single-card listings found.")
            return 0
        print(results[0].set_name)
        for result in results:
            print(f"  {result.set_id:<14} {result.cards_total:>4} cards")
        cards = sum(r.cards_total for r in results)
        print(f"\n{len(results)} set(s), {cards} cards total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
