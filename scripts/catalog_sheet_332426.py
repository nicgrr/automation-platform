"""Hand-catalogue the remaining 9 cards from sheet-20260924-174126-332426.

Turns out 7 of the 9 are ordinary English Dragon Ball Super Card Game
cards (EX25/BT30) that were already in the catalogue from the tcgplayer
refresh -- phash matching just didn't find them for these crops, so
they're committed straight against the existing card_ids rather than
duplicated. Only 2 are genuinely uncatalogued: position 2 is a "SKILLS"
app-tie-in arcade promo (Capsule Corp branded, same family as SDBH's Hero
Avatar Cards but its own sub-line), and position 3 is another Hero
Avatar Card like sheet 288941's Gogeta one, this time Dr. Gero.

    .venv/bin/python -m scripts.catalog_sheet_332426 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_332426
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

SHEET = "sheet-20260924-174126-332426-073317"


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
    Card(1, "dragon-ball-super-ex25-28"),
    Card(2, None, "sdbh-skills-goku-ui", "sdbh-skills", "Super Dragon Ball Heroes Skills App Promo", "SKILLS",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "n/a",
        "Son Goku (Ultra Instinct) - SKILLS Promo", "孫悟空", None),
    Card(3, None, "sdbh-hero-avatar-dr-gero", "sdbh-hero-avatar", "Super Dragon Ball Heroes Hero Avatar Cards", "HERO-AVATAR",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "n/a",
        "Dr. Gero (Hero Avatar Card)", "ドクターゲロ", None),
    Card(4, "dragon-ball-super-ex25-39"),
    Card(5, "dragon-ball-super-ex25-25"),
    Card(6, "dragon-ball-super-ex25-23"),
    Card(7, "dragon-ball-super-bt30-119"),
    Card(8, "dragon-ball-super-bt30-016"),
    Card(9, "dragon-ball-super-ex25-40"),
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

            existing_item = None
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
            else:
                existing_item = session.query(InventoryItem).filter_by(
                    card_id=final_id, variant=CardVariant.HOLO, condition="Near Mint"
                ).one_or_none()

            if existing_item is not None:
                existing_item.quantity += 1
                dup_dest = Path(settings.scan_media_dir) / "dragonball_manual" / f"{existing_item.id}-copy{existing_item.quantity}.jpg"
                dup_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(crop), str(dup_dest))
                print(f"  (duplicate physical copy -- bumped quantity to {existing_item.quantity})")
                continue

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
