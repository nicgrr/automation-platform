"""Hand-catalogue the remaining 9 cards from sheet-20260924-174347-107329.

Position 8 already has a real catalogue entry (SDBH PUMS14-25, Trunks:
Youth) -- phash matching just didn't find it for this crop, so it's
committed straight against the existing card_id. Position 7 is a Dragon
Ball Super Divers PJ promo (SDVPJ-036) with no existing set at all.

Positions 1-6 and 9 are vintage 1990s Japanese Carddass cards -- numbered
per-card (549, 550, 552, 554, 556, 558, and a separate "29" on a
different-looking sub-line with its own "DP1400" rating format). The
existing db-carddass-hondan-9/12/15 sets are single-card stubs at
numbers 338/480/597 from the tcgplayer refresh, giving no reliable way
to tell which numbered "Part" release 549-558 actually belongs to, so
rather than fabricate a Part number those six go in a
"Part Unconfirmed" bucket keyed by their own (real, legible) card
numbers. Card 9's "DP1400" card is a visually distinct sub-line (orange
side border, quote text, no POWER LEVEL gauge) and gets its own separate
uncertain-part bucket rather than being lumped in with the other six.

    .venv/bin/python -m scripts.catalog_sheet_107329 --dry-run
    .venv/bin/python -m scripts.catalog_sheet_107329
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

SHEET = "sheet-20260924-174347-107329-073343"


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
    Card(1, None, "db-carddass-hondan-549", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "549", "Vegeta: Potara Fusion Secret Technique", "ベジータ", None),
    Card(2, None, "db-carddass-hondan-550", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "550", "Vegeta & Fortune Teller Baba: Revival from the Afterlife", "ベジータ&占いババ", None),
    Card(3, None, "db-carddass-hondan-552", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "552", "Majin Buu (w/ Gotenks): Spiral Attack", "ブウ", None),
    Card(4, None, "db-carddass-hondan-554", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "554", "Super Buu: Evil Punch", "ブウ", None),
    Card(5, None, "db-carddass-hondan-556", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "556", "Super Buu: Human Absorption", "ブウ", None),
    Card(6, None, "db-carddass-hondan-558", "db-carddass-hondan-buu", "Dragon Ball Carddass Hondan (Buu Saga, Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "558", "Super Buu: Regeneration Beam", "ブウ", None),
    Card(7, None, "dbsd-sdvpj-036", "dbsd-sdvpj", "Dragon Ball Super Divers PJ", "SDVPJ",
        "Dragon Ball Super Divers", "dragon_ball_super_divers", "SDVPJ-036", "Son Gohan: Youth", "孫悟飯:少年期", "PR"),
    Card(8, "sdbh-pums14-25"),
    Card(9, None, "db-carddass-dp-029", "db-carddass-dp-misc", "Dragon Ball Carddass DP Series (Part Unconfirmed)", None,
        "Dragon Ball Z", "dragon_ball_carddass", "29", "Piccolo", "ピッコロ", None),
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
