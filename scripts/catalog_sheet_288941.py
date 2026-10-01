"""Hand-catalogue the remaining 8 cards from sheet-20260924-173918-288941.

Position 1 already has a real catalogue entry (Fusion World FB03-009,
Jiren) -- phash matching just didn't find it for this crop, so it's
committed straight against the existing card_id rather than duplicated.
Positions 3, 4, 6, 7, 8 have legible codes (UGM8-CP5, HGD6-CP3, SH1-CP8,
BM7-CP6, PUMS12-17) with no existing catalogue coverage. HGD6 doesn't
even have a CardSet row yet; the other four sets already exist. Position
2 is another "Hero Avatar Card" promo like sheet 321321's position 7, but
for a different character (Gogeta) -- given a distinct new id rather than
reused against that generic entry, since the art is genuinely different.
Position 5 (Majin Buu: Evil) has no legible code anywhere on the card,
and a phash nearest-neighbour check against the full SDBH catalogue found
nothing closer than distance 18 (not a real match) -- goes in the
"uncoded SDBH" misc bucket, keyed by character + cost circle.

    .venv/bin/python -m scripts.catalog_sheet_288941 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_288941
"""

import argparse
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType, InventoryItem, TcgCard
from automation_control.scan_ingest.catalog import _compute_phashes

SHEET = "sheet-20260924-173918-288941-073235"


@dataclass(frozen=True)
class Card:
    position: int
    card_id: str | None  # None means create a new manual entry
    new_id: str | None = None
    set_id: str | None = None
    set_name: str | None = None
    set_code: str | None = None
    series: str | None = None
    game: str | None = None
    number: str | None = None
    name: str | None = None
    character: str | None = None
    rarity: str | None = None
    language: str = "Japanese"


CARDS = (
    Card(1, "dragon-ball-fusion-world-fb03-009"),
    Card(2, None, "sdbh-hero-avatar-gogeta", "sdbh-hero-avatar", "Super Dragon Ball Heroes Hero Avatar Cards", "HERO-AVATAR",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "n/a", "Gogeta (Hero Avatar Card)", "ゴジータ", None),
    Card(3, None, "sdbh-ugm8-cp5", "sdbh-ugm8", "Super Dragon Ball Heroes UGM8", "UGM8",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "UGM8-CP5", "Paragus", "パラガス", None),
    Card(4, None, "sdbh-hgd6-cp3", "sdbh-hgd6", "Super Dragon Ball Heroes HGD6", "HGD6",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "HGD6-CP3", "Son Goku (SSJ)", "孫悟空", None),
    Card(5, None, "sdbh-misc-majin-buu-evil", "sdbh-misc", "Super Dragon Ball Heroes (Uncoded)", None,
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "9", "Majin Buu: Evil", "魔人ブウ:悪", None),
    Card(6, None, "sdbh-sh1-cp8", "sdbh-sh1", "Super Dragon Ball Heroes SH1", "SH1",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "SH1-CP8", "Zamasu", "ザマス", "HR"),
    Card(7, None, "sdbh-bm7-cp6", "sdbh-bm7", "Super Dragon Ball Heroes BM7", "BM7",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "BM7-CP6", "Kefla", "ケフラ", None),
    Card(8, None, "sdbh-pums12-17", "sdbh-pums12", "Super Dragon Ball Heroes PUMS12", "PUMS12",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "PUMS12-17", "Son Goku (SSJ)", "孫悟空", None),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    cache_root = Path(settings.scan_cache_dir) / "manual_dragon_ball"

    with SessionLocal() as session:
        for card in CARDS:
            crop = review_dir / f"{SHEET}-card{card.position}-unidentified.jpg"
            if not crop.exists():
                crop = review_dir / f"{SHEET}-card{card.position}-unrouted.jpg"
            if not crop.exists():
                print(f"skip: position {card.position} -- crop not found")
                continue

            if card.card_id is not None:
                existing = session.get(CatalogCard, card.card_id)
                if existing is None:
                    print(f"MISSING catalogue entry for {card.card_id!r} -- skipping position {card.position}")
                    continue
                final_id = card.card_id
                print(f"reuse existing: position {card.position} -> {existing.name} ({final_id})")
            else:
                final_id = card.new_id
                print(f"new entry: position {card.position} -> {card.name} #{card.number} ({card.set_name})")

            if args.dry_run:
                continue

            if card.card_id is None:
                if session.get(CardSet, card.set_id) is None:
                    session.add(CardSet(id=card.set_id, name=card.set_name, code=card.set_code,
                                        series=card.series, game=card.game, cached_at=datetime.now(UTC)))
                if session.get(CatalogCard, final_id) is None:
                    dest = cache_root / card.set_id / f"{final_id}.jpg"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(crop, dest)
                    phash, art_phash = _compute_phashes(dest)
                    session.add(CatalogCard(id=final_id, set_id=card.set_id, number=card.number, name=card.name,
                                            rarity=card.rarity, supertype="Collectible Card",
                                            local_image_path=str(dest), phash=phash, art_phash=art_phash))
                if session.get(CatalogItem, final_id) is None:
                    session.add(CatalogItem(id=final_id, item_type=CatalogItemType.TCG_CARD, name=card.name))
                if session.get(TcgCard, final_id) is None:
                    session.add(TcgCard(catalog_item_id=final_id, game=card.game, set_id=card.set_id,
                                        set_code=card.set_code, number=card.number, character=card.character,
                                        rarity=card.rarity, language=card.language))

            item_id = str(uuid.uuid4())
            inv_dest = Path(settings.scan_media_dir) / "dragonball_manual" / f"{item_id}.jpg"
            inv_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(crop), str(inv_dest))
            session.add(InventoryItem(id=item_id, card_id=final_id, catalog_item_id=final_id,
                                      variant=CardVariant.HOLO, condition="Near Mint", quantity=1,
                                      scan_image_path=str(inv_dest)))

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0
        session.commit()

    print(f"\ndone: {len(CARDS)} position(s) processed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
