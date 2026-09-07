"""Import the full One Piece TCG catalogue -- and, as of 2026-09-07, real
market prices -- from optcgapi.com's free, public, no-API-key-required
REST API. This is the real API import_tcg_catalog_csv.py's own docstring
said to adopt as soon as one was identified, replacing the CSV-only
stopgap for this game:

    GET https://optcgapi.com/api/allSets/           -- every set
    GET https://optcgapi.com/api/sets/{set_id}/     -- every card in one set

Same id-derivation convention as import_tcg_catalog_csv.py --
_slug("one_piece", set_code, number) -- so this updates the 2 example rows
seeded by that script instead of duplicating them, and running this again
later (say, after a new set releases) is always safe to re-run.

Each optcgapi `card_set_id` (e.g. "OP01-001") isn't unique on its own --
a card's parallel printing shares the same code with a different
card_name ("... (Parallel)") and price. Only the non-parallel printing is
imported per code; tracking parallels as distinct catalogue rows is a
real future need, out of scope for this pass.

Pricing: considered PriceCharting.com's API first ($49/month minimum for
API access, and its own docs say it doesn't support historic prices at
all despite the name -- current values only). optcgapi.com already
returns `market_price` per card in the exact same response used for the
catalogue, at no cost, so that's the source used here instead -- one
`CardPrice` row per card per run, source-tagged `"optcgapi_market"`,
`card_id` set to the card's `catalog_item_id` (the FK is declared against
catalog_cards, which only Pokémon rows have, but it isn't enforced by
SQLite and every reader already keys off this same id-space -- the same
approach `card_id` migration already relies on for Pokémon). Re-running
this appends a fresh observation each time rather than overwriting, same
append-only convention as the rest of `card_prices` -- run it periodically
and this becomes a real, if coarse, price history for free.

    .venv/bin/python -m scripts.import_one_piece_from_optcgapi --dry-run
    .venv/bin/python -m scripts.import_one_piece_from_optcgapi
    .venv/bin/python -m scripts.import_one_piece_from_optcgapi --set OP-01
    .venv/bin/python -m scripts.import_one_piece_from_optcgapi --no-prices
"""

import argparse
import re
import time
import uuid
from decimal import Decimal, InvalidOperation

import httpx

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogItem, CatalogItemType, TcgCard

API_BASE = "https://optcgapi.com/api"
GAME = "one_piece"
CARD_SET_ID_RE = re.compile(r"^(.+)-(\d+)$")


def _slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def _fetch_all_sets(client: httpx.Client) -> list[dict]:
    response = client.get(f"{API_BASE}/allSets/")
    response.raise_for_status()
    return response.json()


def _fetch_set_cards(client: httpx.Client, set_id: str) -> list[dict]:
    response = client.get(f"{API_BASE}/sets/{set_id}/")
    response.raise_for_status()
    return response.json()


def _dedupe_parallels(cards: list[dict]) -> dict[str, dict]:
    """One entry per physical card design -- prefer the non-parallel
    printing when both exist for the same card_set_id."""
    by_code: dict[str, dict] = {}
    for card in cards:
        code = (card.get("card_set_id") or "").strip()
        if not code:
            continue
        is_parallel = "(parallel)" in (card.get("card_name") or "").lower()
        existing = by_code.get(code)
        if existing is None or (is_parallel is False and "(parallel)" in (existing.get("card_name") or "").lower()):
            by_code[code] = card
    return by_code


def _price_decimal(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--set", dest="only_set", help="only import this one set_id, e.g. OP-01")
    parser.add_argument("--no-prices", action="store_true", help="catalogue only -- skip writing card_prices")
    args = parser.parse_args(argv)

    with httpx.Client(timeout=30.0) as client:
        sets = _fetch_all_sets(client)
        if args.only_set:
            sets = [s for s in sets if s["set_id"] == args.only_set]
            if not sets:
                raise SystemExit(f"no such set: {args.only_set} (see --help or /api/allSets/ for valid ids)")

        total_created = total_updated = total_skipped = total_priced = 0
        with SessionLocal() as session:
            for i, set_row in enumerate(sets):
                set_id, set_name = set_row["set_id"], set_row["set_name"]
                cards = _fetch_set_cards(client, set_id)
                by_code = _dedupe_parallels(cards)

                created = updated = skipped = priced = 0
                for code, card in by_code.items():
                    match = CARD_SET_ID_RE.match(code)
                    if not match:
                        print(f"  skip (unparseable card_set_id {code!r} in {set_id}): {card.get('card_name')}")
                        skipped += 1
                        continue
                    set_code, number = match.group(1).upper(), match.group(2)
                    item_id = _slug(GAME, set_code, number)
                    market_price = _price_decimal(card.get("market_price"))

                    if args.dry_run:
                        action = "update" if session.get(CatalogItem, item_id) else "create"
                        created += action == "create"
                        updated += action == "update"
                        priced += bool(market_price and not args.no_prices)
                        continue

                    catalog_item = session.get(CatalogItem, item_id)
                    if catalog_item is None:
                        catalog_item = CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=card["card_name"])
                        session.add(catalog_item)
                        created += 1
                    else:
                        catalog_item.name = card["card_name"]
                        updated += 1
                    catalog_item.image_url = card.get("card_image") or catalog_item.image_url

                    tcg_card = session.get(TcgCard, item_id)
                    if tcg_card is None:
                        tcg_card = TcgCard(catalog_item_id=item_id)
                        session.add(tcg_card)
                    tcg_card.game = GAME
                    tcg_card.set_id = set_id
                    tcg_card.set_code = set_code
                    tcg_card.number = number
                    tcg_card.character = card["card_name"]
                    tcg_card.rarity = card.get("rarity")
                    tcg_card.language = "English"

                    if market_price and not args.no_prices:
                        session.add(CardPrice(id=str(uuid.uuid4()), card_id=item_id, variant=None, source="optcgapi_market", price=market_price, currency="USD"))
                        priced += 1

                print(f"{set_id} ({set_name}): {len(by_code)} card(s), {priced} priced, {skipped} skipped")
                total_created += created
                total_updated += updated
                total_skipped += skipped
                total_priced += priced

                if not args.dry_run:
                    session.commit()
                if i < len(sets) - 1:
                    time.sleep(0.3)  # be a polite citizen of a free public API

    if args.dry_run:
        print(f"\n--dry-run: {total_created} would be created, {total_updated} would be updated, {total_priced} would be priced, {total_skipped} skipped, nothing written.")
    else:
        print(f"\nDone: {total_created} created, {total_updated} updated, {total_priced} price observation(s) recorded, {total_skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
