"""Hand-catalogue all 9 cards from sheet-20260924-174904-400694.

All nine are English-language 1996-era Artbox "Dragon Ball Z: Let's have
a mysterious & dangerous adventure through the Galaxy" trading cards --
pure scene artwork with the tagline banner down the left edge, no set
code, card number, or rarity mark printed anywhere on the front (any
numbering would be on the back, which wasn't scanned). They go in the
same "dbz-misc" uncoded bucket created for sheet 304603's lenticular
Trunks insert, keyed by scene/character description since there's no
number to key on. Card 2 and card 8 are both "Gohan (Kid) with sword"
but against different backgrounds -- genuinely two different cards, not
a duplicate scan.

    .venv/bin/python -m scripts.catalog_sheet_400694 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_400694
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

SHEET = "sheet-20260924-174904-400694-073412"


@dataclass(frozen=True)
class Card:
    position: int
    new_id: str
    name: str
    character: str


SET_ID = "dbz-misc"
GAME = "dragon_ball_z"

CARDS = (
    Card(1, "dbz-misc-piccolo-artbox", "Piccolo (Artbox '96)", "Piccolo"),
    Card(2, "dbz-misc-gohan-kid-sword-a", "Son Gohan: Kid, with Sword A (Artbox '96)", "Son Gohan"),
    Card(3, "dbz-misc-goku-krillin-gang", "Goku, Krillin & Friends (Artbox '96)", "Son Goku / Krillin / Yajirobe / Puar"),
    Card(4, "dbz-misc-goku-vs-nappa", "Goku vs Nappa (Artbox '96)", "Son Goku / Nappa"),
    Card(5, "dbz-misc-krillin-power", "Krillin: Power Struggle (Artbox '96)", "Krillin"),
    Card(6, "dbz-misc-nappa-vs-gohan", "Nappa vs Son Gohan: Kid (Artbox '96)", "Nappa / Son Gohan"),
    Card(7, "dbz-misc-vegeta-saiyan-saga", "Vegeta: Saiyan Saga (Artbox '96)", "Vegeta"),
    Card(8, "dbz-misc-gohan-kid-sword-b", "Son Gohan: Kid, with Sword B (Artbox '96)", "Son Gohan"),
    Card(9, "dbz-misc-saiyan-warrior", "Saiyan Warrior (Artbox '96)", "Unidentified Saiyan"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    cache_root = Path(settings.scan_cache_dir) / "manual_dragon_ball"

    with SessionLocal() as session:
        if session.get(CardSet, SET_ID) is None:
            session.add(CardSet(id=SET_ID, name="Dragon Ball Z (Uncoded/Panini)", code=None,
                                series="Dragon Ball Z", game=GAME, cached_at=datetime.now(UTC)))

        for card in CARDS:
            crop = review_dir / f"{SHEET}-card{card.position}-unidentified.jpg"
            if not crop.exists():
                crop = review_dir / f"{SHEET}-card{card.position}-unrouted.jpg"
            if not crop.exists():
                print(f"skip: position {card.position} -- crop not found")
                continue

            print(f"new entry: position {card.position} -> {card.name}")

            if args.dry_run:
                continue

            card_already_existed = session.get(CatalogCard, card.new_id) is not None
            if not card_already_existed:
                dest = cache_root / SET_ID / f"{card.new_id}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(crop, dest)
                phash, art_phash = _compute_phashes(dest)
                session.add(CatalogCard(id=card.new_id, set_id=SET_ID, number="n/a", name=card.name,
                                        rarity=None, supertype="Collectible Card",
                                        local_image_path=str(dest), phash=phash, art_phash=art_phash))
            if session.get(CatalogItem, card.new_id) is None:
                session.add(CatalogItem(id=card.new_id, item_type=CatalogItemType.TCG_CARD, name=card.name))
            if session.get(TcgCard, card.new_id) is None:
                session.add(TcgCard(catalog_item_id=card.new_id, game=GAME, set_id=SET_ID,
                                    set_code=None, number="n/a", character=card.character,
                                    rarity=None, language="English"))

            existing_item = None
            if card_already_existed:
                existing_item = session.query(InventoryItem).filter_by(
                    card_id=card.new_id, variant=CardVariant.HOLO, condition="Near Mint"
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
            session.add(InventoryItem(id=item_id, card_id=card.new_id, catalog_item_id=card.new_id,
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
