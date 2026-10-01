"""Cache the previously-unavailable cards from mixed Dragon Ball sheet 541721.

The 2026-09-24 sheet contains several unrelated Dragon Ball product lines.
Three vintage Japanese Carddass Hondan cards and one explicitly unofficial
DBHA art card do not exist in EzBay's upstream catalogues.  Add minimal,
truthfully-labelled reference records for those four cards and use copies of
their actual scans as reference images.  The review crops themselves stay in
the queue until the operator accepts them.

The operation is idempotent and does not create inventory holdings:

    .venv/bin/python -m scripts.cache_mixed_dragon_ball_sheet_541721 --dry-run
    .venv/bin/python -m scripts.cache_mixed_dragon_ball_sheet_541721
"""

import argparse
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardSet, CatalogCard, CatalogItem, CatalogItemType, TcgCard
from automation_control.scan_ingest.catalog import _compute_phashes


SHEET = "sheet-20260924-053203-541721"


@dataclass(frozen=True)
class ManualCard:
    position: int
    card_id: str
    set_id: str
    set_name: str
    set_code: str
    series: str
    game: str
    number: str
    name: str
    character: str
    rarity: str
    language: str
    release_date: str
    parallel: str | None = None


CARDS = (
    ManualCard(
        1, "db-carddass-hondan-12-480", "db-carddass-hondan-12",
        "Dragon Ball Carddass Hondan Part 12", "Hondan 12",
        "Dragon Ball Carddass Hondan", "dragon_ball_carddass", "480",
        "Krillin", "クリリン", "Normal", "Japanese", "1992-08-01",
    ),
    ManualCard(
        2, "dbha10-unofficial-08", "dbha10-unofficial",
        "Dragon Ball Heroes Inspired Art Cards DBHA10 (Unofficial)", "DBHA10",
        "SDBH Inspired Art Cards (Unofficial)", "dragon_ball_art_card", "DBHA10-08",
        "Turles", "ターレス", "Unofficial Art Card", "Japanese", "2022",
        "Foil Art Card",
    ),
    ManualCard(
        5, "db-carddass-hondan-9-338", "db-carddass-hondan-9",
        "Dragon Ball Carddass Hondan Part 9", "Hondan 9",
        "Dragon Ball Carddass Hondan", "dragon_ball_carddass", "338",
        "Son Goku", "孫悟空", "Normal", "Japanese", "1991-11-01",
    ),
    ManualCard(
        6, "db-carddass-hondan-15-597", "db-carddass-hondan-15",
        "Dragon Ball Carddass Hondan Part 15", "Hondan 15",
        "Dragon Ball Carddass Hondan", "dragon_ball_carddass", "597",
        "Vegeta", "ベジータ", "Normal", "Japanese", "1993-06",
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    cache_root = Path(settings.scan_cache_dir) / "manual_dragon_ball"

    missing = [
        review_dir / f"{SHEET}-card{card.position}-unidentified.jpg"
        for card in CARDS
        if not (review_dir / f"{SHEET}-card{card.position}-unidentified.jpg").exists()
    ]
    if missing:
        raise SystemExit("missing review crop(s): " + ", ".join(str(path) for path in missing))

    with SessionLocal() as session:
        for card in CARDS:
            source = review_dir / f"{SHEET}-card{card.position}-unidentified.jpg"
            destination = cache_root / card.set_id / f"{card.card_id}.jpg"
            action = "already cached" if session.get(CatalogCard, card.card_id) else "add"
            print(f"{action}: card {card.position} -> {card.name} #{card.number} ({card.set_name})")
            if args.dry_run:
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
            phash, art_phash = _compute_phashes(destination)

            card_set = session.get(CardSet, card.set_id)
            if card_set is None:
                session.add(CardSet(
                    id=card.set_id,
                    name=card.set_name,
                    code=card.set_code,
                    series=card.series,
                    release_date=card.release_date,
                    cached_at=datetime.now(UTC),
                    game=card.game,
                ))

            catalog_card = session.get(CatalogCard, card.card_id)
            if catalog_card is None:
                session.add(CatalogCard(
                    id=card.card_id,
                    set_id=card.set_id,
                    number=card.number,
                    name=card.name,
                    rarity=card.rarity,
                    supertype="Art Card" if card.game == "dragon_ball_art_card" else "Collectible Card",
                    local_image_path=str(destination),
                    phash=phash,
                    art_phash=art_phash,
                ))
            else:
                catalog_card.local_image_path = str(destination)
                catalog_card.phash = phash
                catalog_card.art_phash = art_phash

            if session.get(CatalogItem, card.card_id) is None:
                session.add(CatalogItem(
                    id=card.card_id,
                    item_type=CatalogItemType.TCG_CARD,
                    name=card.name,
                ))
            if session.get(TcgCard, card.card_id) is None:
                session.add(TcgCard(
                    catalog_item_id=card.card_id,
                    game=card.game,
                    set_id=card.set_id,
                    set_code=card.set_code,
                    number=card.number,
                    character=card.character,
                    rarity=card.rarity,
                    variant="Unofficial" if card.game == "dragon_ball_art_card" else None,
                    parallel=card.parallel,
                    language=card.language,
                    release_date=card.release_date,
                ))

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        session.commit()

    print(f"\nCached {len(CARDS)} manual references; review queue was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
