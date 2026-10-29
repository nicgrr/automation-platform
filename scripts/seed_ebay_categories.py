"""One-off seed: which eBay category a card belongs to, per marketplace.

ebay_publish.py looks this up as (marketplace, item_type, game), falling
back to (marketplace, item_type, None) -- see EbayCategoryMapping's own
docstring. Only rows confirmed against eBay's own category tree are
seeded here; a game with no row simply can't be published yet (a clear
503 from ebay_publish.py, not a silent miscategorization) until someone
adds the real id, e.g. via eBay's Taxonomy API `getCategorySuggestions`
or Seller Hub's own category picker.

Confirmed against eBay's production Taxonomy/Browse APIs for EBAY_AU on
2026-10-06: Pokemon, One Piece, Dragon Ball Fusion World, Super Dragon
Ball Heroes, Dragon Ball Super Divers, and Dragon Ball Super Card Game
single-card searches resolve to 183454, "Collectable Card Games > CCG
Individual Cards".  Carddass' free-text taxonomy suggestion is too broad,
so that mapping was checked against live Australian Carddass single-card
results instead; the matching singles overwhelmingly use the same 183454
leaf. Keeping one row per game still prevents an unknown game from silently
inheriting a category that was never checked.

    .venv/bin/python -m scripts.seed_ebay_categories --dry-run
    .venv/bin/python -m scripts.seed_ebay_categories
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import EbayCategoryMapping

# (marketplace_id, catalog_item_type, game, ebay_category_id)
SEED_ROWS = [
    ("EBAY_AU", "tcg_card", "pokemon", "183454"),
    ("EBAY_AU", "tcg_card", "one_piece", "183454"),
    ("EBAY_AU", "tcg_card", "dragon_ball_fusion_world", "183454"),
    ("EBAY_AU", "tcg_card", "super_dragon_ball_heroes", "183454"),
    ("EBAY_AU", "tcg_card", "dragon_ball_super_divers", "183454"),
    ("EBAY_AU", "tcg_card", "dragon_ball_super", "183454"),
    ("EBAY_AU", "tcg_card", "dragon_ball_carddass", "183454"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would be seeded, write nothing")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        created = skipped = 0
        for marketplace_id, catalog_item_type, game, ebay_category_id in SEED_ROWS:
            existing = session.scalar(
                select(EbayCategoryMapping).where(
                    EbayCategoryMapping.marketplace_id == marketplace_id,
                    EbayCategoryMapping.catalog_item_type == catalog_item_type,
                    EbayCategoryMapping.game == game,
                )
            )
            if existing:
                print(f"  skip (already mapped): {marketplace_id}/{catalog_item_type}/{game} -> {existing.ebay_category_id}")
                skipped += 1
                continue
            print(f"  {'would seed' if args.dry_run else 'seeded'}: {marketplace_id}/{catalog_item_type}/{game} -> {ebay_category_id}")
            created += 1
            if not args.dry_run:
                session.add(EbayCategoryMapping(marketplace_id=marketplace_id, catalog_item_type=catalog_item_type, game=game, ebay_category_id=ebay_category_id))

        if not args.dry_run:
            session.commit()

    print(f"\n{'would create' if args.dry_run else 'created'} {created}, skipped {skipped} already-mapped.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
