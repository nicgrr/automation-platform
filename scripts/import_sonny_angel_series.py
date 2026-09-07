"""Seed the Sonny Angel regular series as priceable catalogue items.

There is no Sonny Angel API, and no public dataset of every figure. What
*is* well-established is the regular series line-up, and that turns out to
be the right level to price at anyway: nobody sells "one random Animal
Series 4 figure" at a market price -- they sell a sealed blind box of a
named series, and they sell the secret from that series separately, for
multiples of it. Those are the two real products, so those are the two
rows seeded per series.

Individual characters are deliberately NOT seeded. Inventing ~140 figure
names from memory would put fabricated product data in the catalogue, and
the app already has the honest path: photograph one and Module 19's
recognition creates the CatalogItem + CollectibleProduct after a human
confirms it.

`is_secret` drives the query the eBay pricer builds, which is what makes
the split worth having: the Hamburger secret already in this catalogue
prices around $198 median against roughly $50 for a regular figure.

    .venv/bin/python -m scripts.import_sonny_angel_series --dry-run
    .venv/bin/python -m scripts.import_sonny_angel_series
"""

import argparse
import uuid

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, CatalogItemType, CollectibleProduct

BRAND = "Sonny Angel"

# The 11 regular series. Confirmed against the official store's own
# "regular series" collection rather than recalled -- see the module
# docstring for why nothing finer-grained than this is seeded.
SERIES = [
    "Animal Series 1",
    "Animal Series 2",
    "Animal Series 3",
    "Animal Series 4",
    "Marine Series",
    "Fruit Series",
    "Vegetable Series",
    "Sweets Series",
    "Flower Series",
    "Hippers Animal Series",
    "Hippers Harvest Series",
]


def _seed_rows() -> list[tuple[str, str, bool]]:
    """(catalogue name, series, is_secret) for every row this seeds."""
    rows = []
    for series in SERIES:
        rows.append((f"{series} blind box", series, False))
        rows.append((f"{series} secret", series, True))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    created = existing = 0
    with SessionLocal() as session:
        # Match on the CollectibleProduct pair rather than the catalogue
        # name, so re-running is idempotent even if a name is edited later.
        already = {
            (row.series, row.is_secret)
            for row in session.scalars(
                select(CollectibleProduct).where(CollectibleProduct.brand == BRAND)
            )
        }

        for name, series, is_secret in _seed_rows():
            if (series, is_secret) in already:
                existing += 1
                continue

            item_id = str(uuid.uuid4())
            print(f"  {'would add' if args.dry_run else 'added'}: {BRAND} {name}")
            created += 1
            if args.dry_run:
                continue

            session.add(CatalogItem(
                id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name,
            ))
            session.add(CollectibleProduct(
                catalog_item_id=item_id, brand=BRAND, series=series,
                character=None, variant="Secret" if is_secret else None,
                is_secret=is_secret, blind_box_series=series,
            ))

        if not args.dry_run:
            session.commit()

    print(f"\n{'would add' if args.dry_run else 'added'} {created} row(s); {existing} already present.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    else:
        print("Next: price them with\n"
              "  set -a; . ./.env.local; set +a\n"
              "  .venv/bin/python -m scripts.import_ebay_prices --collectibles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
