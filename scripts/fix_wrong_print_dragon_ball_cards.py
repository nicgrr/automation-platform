"""One-off: 11 Dragon Ball Fusion World cards were catalogued and pictured
as the English TCGplayer print (Fusion World's whole reference catalogue
is TCGplayer, a US marketplace -- see export_ebay_bulk_listing.py's
single_language docstring) when the physical card actually scanned in and
held is the Japanese release. Confirmed 2026-09-27 by opening each scan
directly: card text, name, and "BANDAI MADE IN JAPAN" are all Japanese.
One of these had already sold and shipped as the pictured English card
before this was caught -- the order has since been cancelled.

Three independent things get corrected here -- confirmed by running the
first version of this script and then re-checking with
single_image_url() directly, which is what caught the third:

  * TcgCard.language -> "Japanese" (2 of the 11 already had this right;
    the other 9 were left at the column's own "English" default).
  * CatalogItem.image_url and ShopListing.image_url cleared.
  * CatalogCard.image_url cleared too -- a second, separate English/
    TCGplayer-sourced photo, from the phash reference cache
    (single_image_url's own _reference_image_url fallback), matched by
    (set_id, number) rather than by catalog_item_id. Clearing only the
    first two still left every one of these 11 pointing at this second
    source, unnoticed until each was individually re-verified against
    single_image_url()'s real output rather than assumed fixed.

Clearing all three photo sources lets single_image_url's fallback chain
reach the seller's own scan via R2 -- the one honest photo available for
a card with no correctly-language-tagged stock source at all. Language
alone was confirmed not enough on its own either: 2 of the 11 already
said "Japanese" and still carried the English photo, which is worse than
either mistake alone -- a listing that claims Japanese but pictures
English.

    .venv/bin/python -m scripts.fix_wrong_print_dragon_ball_cards --dry-run
    .venv/bin/python -m scripts.fix_wrong_print_dragon_ball_cards
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, CatalogItem, ShopListing, TcgCard

# ShopListing.id -> a short label, purely for the printed report.
SKUS = {
    "6ffa7e42-4a8b-48ec-91a4-1ed1f242be6f": "Majin Buu : Evil - FB04-091",
    "49defc12-9b22-480e-9bea-4e23ce788106": "Energy Marker (E-47)",
    "906ad903-9106-41f2-8560-c287bedfad5e": "Son Goku - FB01-139",
    "f1cd4d50-9a13-45bd-af01-61819cd5b6a1": "Son Gohan : Future - FB10-059",
    "9903ca82-8242-452a-aa0d-809f976e1faf": "Son Goku - FB10-011",
    "7abc5616-46ac-4f81-947d-7150d1b63b52": "Son Gohan : Youth - FB08-056 (H)",
    "b491ebb3-d7d4-42eb-9b92-48c4f967544e": "Son Gohan : SH - FB07-121",
    "f001fed9-2bfd-4f3c-8ce0-13d7637a7c24": "Jiren - FB03-009",
    "9744d1de-4c55-4fa1-8fa2-3666e2b866aa": "Son Goku - FB09-036",
    "d9015604-a9bd-47c4-a9bb-7633cb613f48": "Zamasu: Fused - FB02-044",
    "c199c967-c65e-45df-bfaf-89d1425199ee": "Son Gohan: Youth - FB08-056 (N)",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        relabelled = repictured = 0
        for sku, label in SKUS.items():
            listing = session.get(ShopListing, sku)
            if listing is None:
                print(f"  SKIP {label} ({sku}) -- ShopListing not found")
                continue
            item = session.get(CatalogItem, listing.catalog_item_id)
            tcg = session.get(TcgCard, listing.catalog_item_id)

            changes = []
            if tcg is not None and tcg.language != "Japanese":
                changes.append(f"language {tcg.language!r} -> 'Japanese'")
                if not args.dry_run:
                    tcg.language = "Japanese"
                    relabelled += 1
            if item is not None and item.image_url:
                changes.append(f"item.image_url cleared (was {item.image_url})")
                if not args.dry_run:
                    item.image_url = None
                    repictured += 1
            if listing.image_url:
                changes.append(f"listing.image_url cleared (was {listing.image_url})")
                if not args.dry_run:
                    listing.image_url = None

            if tcg is not None and tcg.set_id:
                ref_card = session.scalar(
                    select(CatalogCard).where(CatalogCard.set_id == tcg.set_id, CatalogCard.number == tcg.number)
                )
                if ref_card is not None and ref_card.image_url:
                    changes.append(f"reference CatalogCard {ref_card.id!r}.image_url cleared (was {ref_card.image_url})")
                    if not args.dry_run:
                        ref_card.image_url = None
                        repictured += 1

            if changes:
                print(f"  {label} ({sku}):")
                for c in changes:
                    print(f"    {c}")
            else:
                print(f"  {label} ({sku}): already correct, nothing to change")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        session.commit()
        print(f"\ndone -- {relabelled} row(s) relabelled Japanese, {repictured} row(s) had their wrong photo cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
