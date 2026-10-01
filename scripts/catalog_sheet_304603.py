"""Hand-catalogue the remaining 6 cards from sheet-20260924-173638-304603.

Position 1 already has a real catalogue entry (Dragon Ball Super Divers
SDV4-PUR3) -- phash matching just didn't find it for this crop, so it's
committed straight against the existing card_id rather than duplicated.
A phash nearest-neighbour check against the full SDBH/Divers catalogue
confirmed the other five genuinely have no match anywhere (best hits were
all distance >=12, far above a real match).

Positions 3 and 4 are Simplified Chinese cards with legible printed codes
(CP-307, LZ03-038) but no existing catalogue coverage for either the
codes or their sets -- new CardSet rows for both. Position 5 and 8 are
two different printings of the same Piccolo card (same move, same cost)
with no legible code anywhere, so they go in a new "uncoded Divers" misc
bucket, keyed by character + cost circle, same pattern as sdbh-misc.
Position 6 is an English lenticular "TRUNKS" insert with no code, number,
or rarity at all -- Panini-era Dragon Ball Z, own misc bucket.

    .venv/bin/python -m scripts.catalog_sheet_304603 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_304603
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

SHEET = "sheet-20260924-173638-304603"


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
    Card(1, "dbsd-sdv4-pur3"),
    Card(3, None, "sdbh-cp-zh-307", "sdbh-cp-zh", "Super Dragon Ball Heroes China Promo (CP)", "CP",
        "Super Dragon Ball Heroes", "super_dragon_ball_heroes", "CP-307",
        "Jiren & Son Goku (SSB)", "吉连 孙悟空", None, "Chinese"),
    Card(4, None, "dbsd-lz03-038", "dbsd-lz03", "Dragon Ball Super Divers LZ03 (China)", "LZ03",
        "Dragon Ball Super Divers", "dragon_ball_super_divers", "LZ03-038",
        "Krillin", "克林", "SR", "Chinese"),
    Card(5, None, "dbsd-misc-piccolo-buppa", "dbsd-misc", "Dragon Ball Super Divers (Uncoded)", None,
        "Dragon Ball Super Divers", "dragon_ball_super_divers", "7", "Piccolo", "ピッコロ", None, "Japanese"),
    Card(6, None, "dbz-misc-trunks-lenticular", "dbz-misc", "Dragon Ball Z (Uncoded/Panini)", None,
        "Dragon Ball Z", "dragon_ball_z", "n/a", "Trunks", "Trunks", None, "English"),
    Card(8, None, "dbsd-misc-piccolo-pr", "dbsd-misc", "Dragon Ball Super Divers (Uncoded)", None,
        "Dragon Ball Super Divers", "dragon_ball_super_divers", "7", "Piccolo (PR)", "ピッコロ", None, "Japanese"),
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
