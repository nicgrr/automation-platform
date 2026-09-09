"""Replace One Piece card images with real, unwatermarked TCGplayer photos.

Every image optcgapi.com serves (scripts/import_one_piece_from_optcgapi.py)
turned out to carry Bandai's official "SAMPLE" preview watermark -- checked
across dozens of cards, common and secret rare alike, and confirmed against
the official collector app the same watermark shows on. That's true of the
whole dataset, not a bug in how this app picks a printing, so no amount of
catalogue-matching logic fixes it. TCGplayer's own product photography does
not carry that watermark (verified by eye against a fetched sample).

Source: tcgcsv.com, a free, keyless, static mirror of TCGplayer's own
catalog (the TCGplayer API itself has been closed to new applicants).
categoryId 68 is One Piece Card Game. Each product's `extendedData` has a
"Number" field in the same "SETCODE-###" shape optcgapi uses, so this maps
onto the exact same catalog_item ids scripts/import_one_piece_from_optcgapi.py
already created -- this only ever UPDATES an existing CatalogItem.image_url,
it never creates new catalogue rows (that stays optcgapi's job, since it
also carries rarity/text/price data this doesn't try to duplicate).

Same cross-listing wrinkle as the optcgapi importer: a card can appear more
than once within one group (Alternate Art, Manga art, etc. reprints of the
same number) -- the plain printing is preferred, same rule as there.

    .venv/bin/python -m scripts.backfill_one_piece_images_from_tcgplayer --dry-run
    .venv/bin/python -m scripts.backfill_one_piece_images_from_tcgplayer
    .venv/bin/python -m scripts.backfill_one_piece_images_from_tcgplayer --group OP17
"""

import argparse
import re
import time

import httpx

from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, TcgCard
from scripts.import_one_piece_from_optcgapi import CARD_SET_ID_RE, GAME, _is_plain_print, _slug

API_BASE = "https://tcgcsv.com/tcgplayer"
CATEGORY_ID = 68  # One Piece Card Game
HEADERS = {"User-Agent": "nlvault-catalog-backfill/1.0"}


def _image_url(product_id: int) -> str:
    return f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"


def _fetch_groups(client: httpx.Client) -> list[dict]:
    response = client.get(f"{API_BASE}/{CATEGORY_ID}/groups")
    response.raise_for_status()
    payload = response.json()
    return payload.get("results", payload)


def _fetch_products(client: httpx.Client, group_id: int) -> list[dict]:
    response = client.get(f"{API_BASE}/{CATEGORY_ID}/{group_id}/products")
    response.raise_for_status()
    payload = response.json()
    return payload.get("results", payload)


def _dedupe_by_number(products: list[dict]) -> dict[str, dict]:
    """One product per card number -- prefer whichever has no print-variant
    marker in its name, same rule as import_one_piece_from_optcgapi.py's
    _is_plain_print (a base name may already end in its own number/code in
    parens; only a later, separate, non-numeric parenthetical like
    "(Alternate Art)" or "(Manga)" marks an actual different printing)."""
    by_number: dict[str, dict] = {}
    for product in products:
        number = next((e["value"] for e in product.get("extendedData", []) if e["name"] == "Number"), None)
        if not number:
            continue  # sealed product or a DON!! card with no real number
        existing = by_number.get(number)
        if existing is None or (_is_plain_print(product["name"]) and not _is_plain_print(existing["name"])):
            by_number[number] = product
    return by_number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--group", help="only this group's abbreviation, e.g. OP17")
    args = parser.parse_args(argv)

    with httpx.Client(timeout=30.0, headers=HEADERS) as client:
        groups = _fetch_groups(client)
        if args.group:
            groups = [g for g in groups if g["abbreviation"].upper() == args.group.upper()]
            if not groups:
                raise SystemExit(f"no such group abbreviation: {args.group!r}")

        updated = skipped_no_match = skipped_unparseable = 0
        with SessionLocal() as session:
            for i, group in enumerate(groups):
                with httpx.Client(timeout=30.0, headers=HEADERS) as group_client:
                    products = _fetch_products(group_client, group["groupId"])
                by_number = _dedupe_by_number(products)

                group_updated = 0
                for number, product in by_number.items():
                    match = CARD_SET_ID_RE.match(number)
                    if not match:
                        skipped_unparseable += 1
                        continue
                    if not _is_plain_print(product["name"]):
                        # This group only lists a reprint/alternate-art
                        # edition of this number (the same cross-listing
                        # quirk import_one_piece_from_optcgapi.py had to
                        # guard against) -- never take that group's own
                        # image for the catalogue's single reference
                        # photo, even when it's the only candidate this
                        # group has. Leave whatever's on file; a group
                        # that lists the plain print may still come along
                        # later in this same sweep, or already has.
                        skipped_no_match += 1
                        continue
                    set_code, card_number = match.group(1).upper(), match.group(2)
                    item_id = _slug(GAME, set_code, card_number)

                    tcg_card = session.get(TcgCard, item_id)
                    if tcg_card is None:
                        skipped_no_match += 1
                        continue

                    new_url = _image_url(product["productId"])
                    if args.dry_run:
                        group_updated += 1
                        continue

                    catalog_item = session.get(CatalogItem, item_id)
                    catalog_item.image_url = new_url
                    group_updated += 1

                print(f"[{i + 1}/{len(groups)}] {group['abbreviation']:<10} {group['name'][:40]:<40} "
                      f"{'would update' if args.dry_run else 'updated'} {group_updated}")
                updated += group_updated

                if not args.dry_run:
                    session.commit()
                if i < len(groups) - 1:
                    time.sleep(0.3)

    verb = "would be updated" if args.dry_run else "updated"
    print(f"\n{updated} image(s) {verb}, {skipped_no_match} with no matching local card, "
          f"{skipped_unparseable} unparseable number(s).")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
