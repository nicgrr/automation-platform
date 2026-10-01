"""Hand-catalogue the remaining 9 cards from sheet-20260924-173317-607520.

Three of the nine (positions 1, 6, 9) already have real catalogue entries
from the tcgplayer/cardotaku refresh (SDBH SH2-CP7, UM9-019, UGM5-RCP4) --
phash matching just didn't find them for this specific crop, so those are
committed straight against the existing card_id rather than duplicated.
Positions 4, 5, 8 have legible codes (HJ6-59, UM4-034, UM8-CP6) with no
catalogue coverage at all -- HJ6 doesn't even have a CardSet row yet, UM4
and UM8 do. Positions 2, 3, 7 have no legible set code anywhere on the
card (checked closely), so they go in the same "misc/uncoded SDBH" bucket
used by sheet 321321's position 2, keyed by character + cost circle.

    .venv/bin/python -m scripts.catalog_sheet_607520 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_607520
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

SHEET = "sheet-20260924-173317-607520-073157"


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


CARDS = (
    Card(1, "sdbh-sh2-cp7"),
    Card(2, None, "sdbh-misc-super-baby-2", "sdbh-misc", "Super Dragon Ball Heroes (Uncoded)", None,
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "10", "Super Baby 2", "スーパーベビー2", None),
    Card(3, None, "sdbh-misc-android17-mir", "sdbh-misc", "Super Dragon Ball Heroes (Uncoded)", None,
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "8", "Android 17: MIR", "人造人間17号:MIR", "R"),
    Card(4, None, "sdbh-hj6-59", "sdbh-hj6", "Super Dragon Ball Heroes HJ6", "HJ6",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "HJ6-59", "Dr. Mico", "Dr.ミコ", None),
    Card(5, None, "sdbh-um4-034", "sdbh-um4", "Super Dragon Ball Heroes UM4", "UM4",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "UM4-034", "Ichiboshi Ryu: Bino", "一星龍:ビノ", "HR"),
    Card(6, "sdbh-um9-019"),
    Card(7, None, "sdbh-misc-beerus", "sdbh-misc", "Super Dragon Ball Heroes (Uncoded)", None,
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "8", "Beerus", "ビルス", None),
    Card(8, None, "sdbh-um8-cp6", "sdbh-um8", "Super Dragon Ball Heroes UM8", "UM8",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "UM8-CP6", "Broly: BR", "ブロリー:BR", None),
    Card(9, "sdbh-ugm5-rcp4"),
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
                                        rarity=card.rarity, language="Japanese"))

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
