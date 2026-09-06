"""Import TCG cards into the generic catalogue from a CSV file.

There's no verified, ready-to-wire API for One Piece (or most other TCGs)
the way pokemontcg.io serves Pokémon -- rather than build against an
unverified scraper or a source with unclear licensing, this is the "manual
data/import until a real API is added" path the pricing engine already uses
(see scan_ingest/pricing.py's module docstring for the same philosophy).
Games beyond Pokémon get their catalogue in via this until a legitimate API
is identified and adopted.

CSV columns (header row required; character/rarity/variant/parallel/language/
release_date/artist/image_url are optional and may be blank):

    game,set_id,set_code,number,name,character,rarity,variant,parallel,language,release_date,artist,image_url
    one_piece,op17,OP17,062,Kaido,Kaido,Super Alternate Art,,,English,2025-11-14,,

Each row's id is derived deterministically from (game, set_code or set_id,
number) -- not a random UUID -- so re-importing the same CSV after fixing a
typo updates the existing row instead of creating a duplicate. This is the
whole of this script's duplicate detection: same (game, set, number) is the
same card.

    .venv/bin/python -m scripts.import_tcg_catalog_csv --file one_piece_op17.csv --dry-run
    .venv/bin/python -m scripts.import_tcg_catalog_csv --file one_piece_op17.csv
"""

import argparse
import csv
import re
from pathlib import Path

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, TcgCard

REQUIRED_COLUMNS = {"game", "number", "name"}


def _slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


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

    created = updated = 0
    with SessionLocal() as session:
        for row in rows:
            game = (row.get("game") or "").strip()
            number = (row.get("number") or "").strip()
            name = (row.get("name") or "").strip()
            if not (game and number and name):
                print(f"  skip (missing game/number/name): {row}")
                continue
            set_code = (row.get("set_code") or "").strip()
            set_id = (row.get("set_id") or "").strip()
            item_id = _slug(game, set_code or set_id or "na", number)

            existing_item = session.get(CatalogItem, item_id)
            action = "update" if existing_item else "create"
            print(f"  {action}: {item_id}  ({game} {set_code or set_id} #{number} {name})")

            if args.dry_run:
                continue

            image_url = (row.get("image_url") or "").strip() or None
            if existing_item is None:
                existing_item = CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name, image_url=image_url)
                session.add(existing_item)
                created += 1
            else:
                existing_item.name = name
                existing_item.image_url = image_url or existing_item.image_url
                updated += 1

            detail = session.get(TcgCard, item_id)
            if detail is None:
                detail = TcgCard(catalog_item_id=item_id)
                session.add(detail)
            detail.game = game
            detail.set_id = set_id or None
            detail.set_code = set_code or None
            detail.number = number
            detail.character = (row.get("character") or "").strip() or name
            detail.rarity = (row.get("rarity") or "").strip() or None
            detail.variant = (row.get("variant") or "").strip() or None
            detail.parallel = (row.get("parallel") or "").strip() or None
            detail.language = (row.get("language") or "").strip() or "English"
            detail.release_date = (row.get("release_date") or "").strip() or None
            detail.artist = (row.get("artist") or "").strip() or None

        if args.dry_run:
            print(f"\n--dry-run: {len(rows)} row(s) read, nothing written.")
            return 0

        session.commit()
    print(f"\nDone: {created} created, {updated} updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
