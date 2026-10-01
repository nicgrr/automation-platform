"""One-off: hand-catalogue the 4 Simplified Chinese-market Pokemon cards
from sheet-20260921-185330-479988 (cards 4-7) -- pokemontcg.io has no
Chinese-market card data, so these get the same treatment as the Super
Dragon Ball Heroes sheet: CatalogItem/TcgCard/InventoryItem created
directly from what's printed on each card. Unlike the Dragon Ball cards,
game stays "pokemon" here (it's the same franchise) -- TcgCard.language is
what distinguishes a Chinese print from an English one.

    .venv/bin/python -m scripts.commit_chinese_sheet_479988
"""

import shutil
import uuid
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogItem, CatalogItemType, InventoryItem, TcgCard

REVIEW_DIR = Path("scan_ingest_data/media/needs_review")
SHEET = "sheet-20260921-185330-479988"

# (crop card#, English-ish name, character (Chinese), rarity, set_code, number)
CARDS = [
    (4, "Ivory Pig ex", "象牙猪ex", "RR", "csv10C", "104/222"),
    (5, "Team Rocket's Crobat ex", "火箭队的叉字蝠ex", "RR", "csv10C", "130/222"),
    (6, "Excadrill ex", "土龙节节ex", "RR", "csv10C", "179/222"),
    (7, "Yanma", "蜻蜓", "AR", "csv10C", "223/222"),
]


def main() -> int:
    settings = get_settings()
    dest_dir = Path(settings.scan_media_dir) / "chinese_market"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as session:
        committed = 0
        for card_num, name, character, rarity, set_code, number in CARDS:
            crop = REVIEW_DIR / f"{SHEET}-card{card_num}-unidentified.jpg"
            if not crop.exists():
                print(f"skip: {name} -- crop not found ({crop})")
                continue

            item_id = str(uuid.uuid4())
            dest_path = dest_dir / f"{item_id}.jpg"
            shutil.move(str(crop), str(dest_path))

            session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
            session.add(TcgCard(
                catalog_item_id=item_id, game="pokemon", set_code=set_code,
                number=number, character=character, rarity=rarity, language="Chinese",
            ))
            session.add(InventoryItem(
                catalog_item_id=item_id, card_id=None, variant=CardVariant.HOLO,
                condition="Near Mint", quantity=1, scan_image_path=str(dest_path),
            ))
            print(f"committed: {name} ({character})")
            committed += 1
        session.commit()

    print(f"\ncommitted {committed}/{len(CARDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
