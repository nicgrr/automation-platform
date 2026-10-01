"""Cache Pokemon special reprint sets missing from pokemontcg.io.

These are normal TCGplayer groups, but they live outside EzBay's primary
Pokemon catalogue. Keeping the allow-list here prevents an accidental import
of TCGplayer's complete, duplicate Pokemon catalogue.

    .venv/bin/python -m scripts.cache_pokemon_special_scan_catalog
    .venv/bin/python -m scripts.cache_pokemon_special_scan_catalog --group TTBB
"""

import argparse
from pathlib import Path

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.scan_ingest.tcgplayer_catalog import (
    HEADERS,
    POKEMON_SPECIAL_GAME,
    cache_group,
    fetch_groups,
)


ALLOWED_GROUPS = {"TTBB", "TTBB23", "TTBB24"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group", choices=sorted(ALLOWED_GROUPS), help="cache just one special set")
    parser.add_argument("--force", action="store_true", help="re-download images already on disk")
    args = parser.parse_args(argv)

    wanted = {args.group} if args.group else ALLOWED_GROUPS
    cache_dir = Path(get_settings().scan_cache_dir)
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    with httpx.Client(timeout=30.0, headers=HEADERS, limits=limits) as client, SessionLocal() as session:
        groups = [
            group for group in fetch_groups(client, POKEMON_SPECIAL_GAME)
            if (group.get("abbreviation") or "").upper() in wanted
        ]
        missing = wanted - {(group.get("abbreviation") or "").upper() for group in groups}
        if missing:
            raise SystemExit(f"Pokemon: group(s) not found: {', '.join(sorted(missing))}")

        for index, group in enumerate(groups, start=1):
            result = cache_group(
                session, POKEMON_SPECIAL_GAME, group, cache_dir, client, force=args.force,
            )
            print(
                f"[{index}/{len(groups)}] {group.get('abbreviation') or '':<8} "
                f"{result.set_name}: {result.cards_total} cards, "
                f"{result.hashed} hashed, {result.images_downloaded} downloaded"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
