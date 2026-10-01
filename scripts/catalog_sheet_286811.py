"""Hand-catalogue the remaining 3 cards from sheet-20260924-175117-286811.

Six of the nine are ordinary English Dragon Ball Super Card Game EX25
cards already in the catalogue from the tcgplayer refresh -- phash
matching just didn't find them for these crops, so they're committed
straight against the existing card_ids. Position 7 is another uncoded
1996 Artbox card (Goku standing), going in the same dbz-misc bucket as
sheet 400694. Positions 8 and 9 are the exact same Artbox "Other World"
ensemble card (Tien, Piccolo, Yamcha, Chiaotzu with halos) -- a genuine
duplicate physical copy, not two different cards, so position 9 bumps
quantity on position 8's new entry rather than creating a second one.

    .venv/bin/python -m scripts.catalog_sheet_286811 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_286811
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

SHEET = "sheet-20260924-175117-286811-073437"
SET_ID = "dbz-misc"
GAME = "dragon_ball_z"


@dataclass(frozen=True)
class Card:
    position: int
    card_id: str | None
    new_id: str | None = None
    name: str | None = None
    character: str | None = None


CARDS = (
    Card(1, "dragon-ball-super-ex25-07"),
    Card(2, "dragon-ball-super-ex25-05"),
    Card(3, "dragon-ball-super-ex25-38"),
    Card(4, "dragon-ball-super-ex25-37"),
    Card(5, "dragon-ball-super-ex25-18"),
    Card(6, "dragon-ball-super-ex25-34"),
    Card(7, None, "dbz-misc-goku-standing", "Son Goku: Standing (Artbox '96)", "Son Goku"),
    Card(8, None, "dbz-misc-otherworld-fighters", "Other World Fighters (Artbox '96)", "Tien Shinhan / Piccolo / Yamcha / Chiaotzu"),
    Card(9, None, "dbz-misc-otherworld-fighters", "Other World Fighters (Artbox '96)", "Tien Shinhan / Piccolo / Yamcha / Chiaotzu"),
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
                print(f"new/dup entry: position {card.position} -> {card.name}")

            if args.dry_run:
                continue

            card_already_existed = session.get(CatalogCard, final_id) is not None
            if card.card_id is None and not card_already_existed:
                if session.get(CardSet, SET_ID) is None:
                    session.add(CardSet(id=SET_ID, name="Dragon Ball Z (Uncoded/Panini)", code=None,
                                        series="Dragon Ball Z", game=GAME, cached_at=datetime.now(UTC)))
                dest = cache_root / SET_ID / f"{final_id}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(crop, dest)
                phash, art_phash = _compute_phashes(dest)
                session.add(CatalogCard(id=final_id, set_id=SET_ID, number="n/a", name=card.name,
                                        rarity=None, supertype="Collectible Card",
                                        local_image_path=str(dest), phash=phash, art_phash=art_phash))
                session.add(CatalogItem(id=final_id, item_type=CatalogItemType.TCG_CARD, name=card.name))
                session.add(TcgCard(catalog_item_id=final_id, game=GAME, set_id=SET_ID,
                                    set_code=None, number="n/a", character=card.character,
                                    rarity=None, language="English"))

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
