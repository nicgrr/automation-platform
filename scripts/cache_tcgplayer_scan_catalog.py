"""Cache One Piece / Dragon Ball reference catalogues for the scan service.

Pulls every TCGplayer group (set) of a game from tcgcsv.com, downloads each
card's reference image, and pHashes it -- see
automation_control/scan_ingest/tcgplayer_catalog.py. Once a game is cached,
its sheets identify like any Pokemon sheet: press the scanner button.

Re-runnable anytime: it refreshes names and prices in place and only
downloads images it doesn't already have. Groups are always processed
oldest first (it decides which set a reprint's id belongs to), so
`--group` is for refreshing one set of a game that's already cached, not
for a first import.

    .venv/bin/python -m scripts.cache_tcgplayer_scan_catalog --game dragon_ball_fusion_world
    .venv/bin/python -m scripts.cache_tcgplayer_scan_catalog --game all
    .venv/bin/python -m scripts.cache_tcgplayer_scan_catalog --game one_piece --group OP01
"""

import argparse
from pathlib import Path

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.tcgplayer_catalog import GAMES, HEADERS, cache_group, fetch_groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game", required=True, choices=[*GAMES, "all"])
    parser.add_argument("--group", help="only this group abbreviation, e.g. FB01")
    parser.add_argument("--force", action="store_true", help="re-download images already on disk")
    args = parser.parse_args(argv)

    cache_dir = Path(get_settings().scan_cache_dir)
    games = list(GAMES.values()) if args.game == "all" else [GAMES[args.game]]
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(timeout=30.0, headers=HEADERS, limits=limits) as client, SessionLocal() as session:
        for game in games:
            groups = fetch_groups(client, game)
            if args.group:
                groups = [g for g in groups if (g.get("abbreviation") or "").upper() == args.group.upper()]
                if not groups:
                    raise SystemExit(f"{game.key}: no group with abbreviation {args.group!r}")
            print(f"\n{game.label}: {len(groups)} group(s)")
            cards = hashed = 0
            for i, group in enumerate(groups, start=1):
                try:
                    result = cache_group(session, game, group, cache_dir, client, force=args.force)
                except Exception as exc:
                    session.rollback()
                    print(f"  [{i}/{len(groups)}] {group.get('abbreviation') or '':<10} FAILED: {exc}")
                    continue
                cards += result.cards_total
                hashed += result.hashed
                print(f"  [{i}/{len(groups)}] {group.get('abbreviation') or '':<10} {result.set_name[:44]:<44} "
                      f"{result.cards_total:>4} cards, {result.hashed:>4} hashed, {result.images_downloaded:>4} downloaded")
            print(f"{game.label}: {cards} cards, {hashed} hashed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
