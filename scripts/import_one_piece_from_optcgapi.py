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
from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CardSet, CatalogItem, CatalogItemType, TcgCard

API_BASE = "https://optcgapi.com/api"
GAME = "one_piece"
CARD_SET_ID_RE = re.compile(r"^(.+)-(\d+)$")
SET_ID_ALIASES = {
    "EB-03": "op-24545",
    "OP14-EB04": "op-24537",
}


def _slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def _set_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def resolve_card_set(session, external_set_id: str, set_name: str) -> CardSet:
    alias_id = SET_ID_ALIASES.get(external_set_id)
    if alias_id:
        aliased = session.get(CardSet, alias_id)
        if aliased:
            return aliased

    card_sets = session.scalars(select(CardSet).where(CardSet.game == GAME)).all()
    code_matches = [card_set for card_set in card_sets if _set_key(card_set.code) == _set_key(external_set_id)]
    if len(code_matches) == 1:
        return code_matches[0]
    name_matches = [card_set for card_set in card_sets if _set_key(card_set.name) == _set_key(set_name)]
    if len(name_matches) == 1:
        return name_matches[0]

    set_id = f"optcg-{_slug(external_set_id)}"[:32]
    card_set = session.get(CardSet, set_id)
    if card_set is None:
        card_set = CardSet(id=set_id, name=set_name, code=external_set_id, game=GAME)
        session.add(card_set)
        session.flush()
    return card_set


def _fetch_all_sets(client: httpx.Client) -> list[dict]:
    response = client.get(f"{API_BASE}/allSets/")
    response.raise_for_status()
    return response.json()


def _fetch_set_cards(client: httpx.Client, set_id: str) -> list[dict]:
    response = client.get(f"{API_BASE}/sets/{set_id}/")
    response.raise_for_status()
    return response.json()


# A base card_name often already ends in its own number or full code in
# parentheses -- "Roronoa Zoro (001)", "Roronoa Zoro (EB04-007)", "Yamato
# (OP16-098)" -- that's not a print-variant marker, so it needs stripping
# before checking for one. A real variant marker ("SP", "Alternate Art",
# "Parallel", "Full Art", "Jolly Roger Foil") is always letters and
# spaces with no digit; the card's own number/code always contains at
# least one digit, so "contains a digit" is what distinguishes them --
# checking for pure digits alone missed the alphanumeric-code case.
_TRAILING_CODE_PAREN_RE = re.compile(r"\([A-Za-z0-9-]*\d[A-Za-z0-9-]*\)$")


def _is_plain_print(name: str) -> bool:
    return "(" not in _TRAILING_CODE_PAREN_RE.sub("", name).strip()


def _dedupe_parallels(cards: list[dict]) -> dict[str, dict]:
    """One entry per physical card design -- prefer the plain printing
    when more than one card shares the same card_set_id.

    Originally looked for the literal marker "(parallel)" -- but real
    optcgapi data never uses that word; alternate/chase prints are named
    "(Alternate Art)", and this project also saw a stale "(SP)" variant
    for a handful of cards. That made this check dead code: it always
    just kept whichever entry the API listed first for a given code,
    which is arbitrary and occasionally landed on a SAMPLE-watermarked
    preview image (no real scan exists yet for some very new chase
    prints) instead of the plain card's real photo. See _is_plain_print
    for what "plain" actually means here.
    """
    by_code: dict[str, dict] = {}
    for card in cards:
        code = (card.get("card_set_id") or "").strip()
        if not code:
            continue
        name = card.get("card_name") or ""
        existing = by_code.get(code)
        if existing is None or (_is_plain_print(name) and not _is_plain_print(existing.get("card_name") or "")):
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
                card_set = resolve_card_set(session, set_id, set_name) if not args.dry_run else None
                # A fresh connection per set, not the one shared for the
                # whole sweep above: reusing one long-lived connection
                # across ~20 sequential requests was observed to come back
                # with a stale response for a handful of sets (an old
                # duplicate printing instead of the current one) even
                # though a lone request for that same set, moments later,
                # always returned the current data -- looks like a
                # connection-pinned cache on optcgapi's side, not
                # anything this app controls.
                with httpx.Client(timeout=30.0) as set_client:
                    cards = _fetch_set_cards(set_client, set_id)
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
                    tcg_card = session.get(TcgCard, item_id)
                    # _dedupe_parallels only sees one set's own card list --
                    # it can't catch a card_set_id that's ALSO reprinted as
                    # a bonus secret rare inside a different, later set (a
                    # real thing optcgapi's data does: e.g. PRB02-006
                    # stands alone in PRB-02 but reappears as "(SP)" inside
                    # OP14-EB04's own list). Never let that later set's own
                    # -- non-plain -- listing overwrite an already-plain
                    # identity already on file; a price observation is
                    # still worth recording either way, below.
                    already_plain = tcg_card is not None and _is_plain_print(tcg_card.character or "")
                    overwrite = _is_plain_print(card["card_name"]) or not already_plain

                    if catalog_item is None:
                        catalog_item = CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=card["card_name"])
                        session.add(catalog_item)
                        catalog_item.image_url = card.get("card_image")
                        created += 1
                    else:
                        if overwrite:
                            catalog_item.name = card["card_name"]
                            catalog_item.image_url = card.get("card_image") or catalog_item.image_url
                        updated += 1

                    if tcg_card is None:
                        tcg_card = TcgCard(catalog_item_id=item_id)
                        session.add(tcg_card)
                    tcg_card.game = GAME
                    if overwrite:
                        tcg_card.set_id = card_set.id
                        tcg_card.set_code = set_code
                        tcg_card.character = card["card_name"]
                        tcg_card.rarity = card.get("rarity")
                    tcg_card.number = number
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
