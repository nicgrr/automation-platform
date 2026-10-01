"""One-off: hand-catalogue the 9 Super Dragon Ball Heroes cards from
sheet-20260921-192456-112823 -- Module N/A, no automated data source exists
for this game (pokemontcg.io is Pokemon-only), so this creates the
CatalogItem/TcgCard/InventoryItem rows directly from what's printed on each
card, the same way /collectibles and /accessories handle a manual add.

TcgCard.game is exactly what makes this generic across games (see its own
docstring) -- no CardSet row is needed, since TcgCard.set_id/set_code are
plain indexed strings, not a foreign key, and already carry character/
rarity/language columns of their own.

Two of the nine (Gohan: Boyhood and Gohan: SH) show a legible set-card
code (ABS-11, UGM5-002); the rest show only a rarity letter (R/I/B) and a
small cost/number circle, not a real set code, so those are left as
set_code=None rather than a fabricated one. variant is CardVariant.NORMAL
across the board -- this game doesn't have Pokemon's Normal/Reverse-Holo/
Holo distinction (SDBH cards are foil by default design), so none of the
existing enum values would mean anything real here; NORMAL is the same
placeholder /collectibles and /accessories already use for non-Pokemon
items.

    .venv/bin/python -m scripts.commit_sdbh_sheet_112823
"""

import shutil
import uuid
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogItem, CatalogItemType, InventoryItem, TcgCard

REVIEW_DIR = Path("scan_ingest_data/media/needs_review")
SHEET = "sheet-20260921-192456-112823"

# (crop card#, name, character (Japanese), rarity letter, set_code, number)
CARDS = [
    (1, "Son Goku: BR", "孫悟空:BR", "R", None, "11"),
    (2, "Trunks: Future", "トランクス:未来", "I", None, "9"),
    (3, "Son Gohan: Boyhood", "孫悟飯:少年期", None, "ABS-11", None),
    (4, "Son Gohan: Boyhood (SSJ2)", "孫悟飯:少年期", None, "UGM5-002", None),
    (5, "Son Goku", "孫悟空", "I", None, "7"),
    (6, "Whis", "ウイス", "B", None, "9"),
    (7, "Son Gohan: SH", "孫悟飯:SH", "B", None, "9"),
    (8, "Broly: BR", "ブロリー:BR", None, None, "12"),
    (9, "Piccolo", "ピッコロ", "R", None, "7"),
]


def main() -> int:
    settings = get_settings()
    dest_dir = Path(settings.scan_media_dir) / "dragonball_heroes"
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
                catalog_item_id=item_id, game="dragonball_heroes", set_code=set_code,
                number=number, character=character, rarity=rarity, language="Japanese",
            ))
            session.add(InventoryItem(
                catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL,
                condition="Near Mint", quantity=1, scan_image_path=str(dest_path),
            ))
            print(f"committed: {name} ({character})")
            committed += 1
        session.commit()

    print(f"\ncommitted {committed}/{len(CARDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
