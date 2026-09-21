"""Cache the Japanese-print Pokemon reference catalogue for the scan service.

Pulls every set from TCGdex (api.tcgdex.net), downloads each card's
reference image, and pHashes it -- see
automation_control/scan_ingest/tcgdex_catalog.py. Once cached, Japanese
Pokemon sheets identify like any other game: press the scanner button.

Re-runnable anytime: it refreshes names in place and only downloads images
it doesn't already have.

    .venv/bin/python -m scripts.cache_tcgdex_scan_catalog
    .venv/bin/python -m scripts.cache_tcgdex_scan_catalog --set SV7
"""

import argparse
from pathlib import Path

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.tcgdex_catalog import HEADERS, cache_set, fetch_sets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", help="only this TCGdex set id, e.g. SV7")
    parser.add_argument("--force", action="store_true", help="re-download images already on disk")
    args = parser.parse_args(argv)

    cache_dir = Path(get_settings().scan_cache_dir)
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(timeout=30.0, headers=HEADERS, limits=limits) as client, SessionLocal() as session:
        sets = fetch_sets(client)
        if args.set:
            sets = [s for s in sets if s.get("id", "").upper() == args.set.upper()]
            if not sets:
                raise SystemExit(f"no such TCGdex set: {args.set!r}")

        print(f"Pokémon (Japanese): {len(sets)} set(s)")
        cards = hashed = 0
        for i, card_set in enumerate(sets, start=1):
            try:
                result = cache_set(session, card_set["id"], cache_dir, client, force=args.force)
            except Exception as exc:
                session.rollback()
                print(f"  [{i}/{len(sets)}] {card_set.get('id', ''):<10} FAILED: {exc}")
                continue
            cards += result.cards_total
            hashed += result.hashed
            print(f"  [{i}/{len(sets)}] {card_set.get('id', ''):<10} {result.set_name[:44]:<44} "
                  f"{result.cards_total:>4} cards, {result.hashed:>4} hashed, {result.images_downloaded:>4} downloaded")
        print(f"Pokémon (Japanese): {cards} cards, {hashed} hashed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
