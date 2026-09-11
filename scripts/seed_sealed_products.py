"""Seed the catalog with Elite Trainer Boxes and booster boxes -- Pokemon
and One Piece -- so Deal Check has something to match sealed-product
listings against (see deal_check.py's match_sealed_product).

There is no live pricing feed for sealed product anywhere in this app
(pokemontcg.io and optcgapi.com are both card-only, no TCGplayer key is
configured) -- these RRPs are AUD estimates from general market knowledge,
not a queried source. Treat them as a starting reference, the same way
sealed_economics.py already expects RRPs to be corrected once a real
number is known, not as a verified price feed. Re-run is safe: existing
rows (matched by name) are left alone rather than duplicated.

    .venv/bin/python -m scripts.seed_sealed_products --dry-run
    .venv/bin/python -m scripts.seed_sealed_products
"""

import argparse
import uuid
from decimal import Decimal

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, SealedProduct

# (name, game, product_type, rrp_aud, units_per_display)
# units_per_display is packs-per-box, where confidently known; left None
# rather than guessed for anything less certain (case pack counts vary by
# retailer/region and aren't needed for Deal Check's own matching).
PRODUCTS: list[tuple[str, str, str, str, int | None]] = [
    # --- Pokemon TCG: Scarlet & Violet era Elite Trainer Boxes ---
    ("Pokemon TCG Scarlet & Violet Base Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Paldea Evolved Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Obsidian Flames Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG 151 Elite Trainer Box", "pokemon", "etb", "84.95", None),
    ("Pokemon TCG Paradox Rift Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Paldean Fates Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Temporal Forces Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Twilight Masquerade Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Shrouded Fable Elite Trainer Box", "pokemon", "etb", "59.95", None),
    ("Pokemon TCG Stellar Crown Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Surging Sparks Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Prismatic Evolutions Elite Trainer Box", "pokemon", "etb", "94.95", None),
    ("Pokemon TCG Journey Together Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Destined Rivals Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG Black Bolt Elite Trainer Box", "pokemon", "etb", "79.95", None),
    ("Pokemon TCG White Flare Elite Trainer Box", "pokemon", "etb", "79.95", None),

    # --- Pokemon TCG: Sword & Shield era Elite Trainer Boxes ---
    ("Pokemon TCG Sword & Shield Base Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Rebel Clash Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Darkness Ablaze Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Vivid Voltage Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Battle Styles Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Chilling Reign Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Evolving Skies Elite Trainer Box", "pokemon", "etb", "84.95", None),
    ("Pokemon TCG Fusion Strike Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Brilliant Stars Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Astral Radiance Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Lost Origin Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Silver Tempest Elite Trainer Box", "pokemon", "etb", "69.95", None),
    ("Pokemon TCG Crown Zenith Elite Trainer Box", "pokemon", "etb", "69.95", None),

    # --- Pokemon TCG: Scarlet & Violet era booster boxes (36 packs) ---
    ("Pokemon TCG Scarlet & Violet Base Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Paldea Evolved Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Obsidian Flames Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG 151 Booster Box", "pokemon", "booster_box", "189.95", 36),
    ("Pokemon TCG Paradox Rift Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Temporal Forces Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Twilight Masquerade Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Shrouded Fable Booster Box", "pokemon", "booster_box", "139.95", 36),
    ("Pokemon TCG Stellar Crown Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Surging Sparks Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Prismatic Evolutions Booster Box", "pokemon", "booster_box", "249.95", 36),
    ("Pokemon TCG Journey Together Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Destined Rivals Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG Black Bolt Booster Box", "pokemon", "booster_box", "169.95", 36),
    ("Pokemon TCG White Flare Booster Box", "pokemon", "booster_box", "169.95", 36),

    # --- Pokemon TCG: Sword & Shield era booster boxes (36 packs) ---
    ("Pokemon TCG Sword & Shield Base Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Rebel Clash Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Darkness Ablaze Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Vivid Voltage Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Battle Styles Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Chilling Reign Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Evolving Skies Booster Box", "pokemon", "booster_box", "199.95", 36),
    ("Pokemon TCG Fusion Strike Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Brilliant Stars Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Astral Radiance Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Lost Origin Booster Box", "pokemon", "booster_box", "149.95", 36),
    ("Pokemon TCG Silver Tempest Booster Box", "pokemon", "booster_box", "149.95", 36),

    # --- One Piece TCG booster boxes (24 packs) ---
    ("One Piece TCG OP01 Romance Dawn Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP02 Paramount War Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP03 Pillars of Strength Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP04 Kingdoms of Intrigue Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP05 Awakening of the New Era Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP06 Wings of the Captain Booster Box", "one_piece", "booster_box", "144.00", 24),
    ("One Piece TCG OP07 500 Years in the Future Booster Box", "one_piece", "booster_box", "155.00", 24),
    ("One Piece TCG OP08 Two Legends Booster Box", "one_piece", "booster_box", "155.00", 24),
    ("One Piece TCG OP09 Emperors in the New World Booster Box", "one_piece", "booster_box", "155.00", 24),
    ("One Piece TCG OP10 Royal Bloodlines Booster Box", "one_piece", "booster_box", "165.00", 24),
    ("One Piece TCG OP11 A Fist of Divine Speed Booster Box", "one_piece", "booster_box", "165.00", 24),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        existing_names = {name for name, in session.execute(select(CatalogItem.name))}
        created = skipped = 0
        for name, game, product_type, rrp, units in PRODUCTS:
            if name in existing_names:
                skipped += 1
                continue
            print(f"  {'would add' if args.dry_run else 'added'}: {name} — ${rrp} RRP ({game})")
            created += 1
            if args.dry_run:
                continue
            item_id = str(uuid.uuid4())
            session.add(CatalogItem(id=item_id, item_type=CatalogItemType.SEALED_PRODUCT, name=name))
            session.add(SealedProduct(
                catalog_item_id=item_id, game=game, product_type=product_type,
                rrp=Decimal(rrp), units_per_display=units,
            ))

        if not args.dry_run:
            session.commit()

        print(f"\n{'would add' if args.dry_run else 'added'} {created} sealed product(s), {skipped} already present.")
        if args.dry_run:
            print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
