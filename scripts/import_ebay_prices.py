"""Record eBay *active listing* prices into card_prices -- Module 23.

Important, and the reason the source names below say "active" rather than
"sold": eBay has no freely-available sold-comp API any more. The Finding
API's findCompletedItems was deprecated in Jan 2024 and decommissioned in
Feb 2025, and Marketplace Insights (the one official sold-data source, 90
days) is a Limited Release requiring business-partner approval that a
single-operator seller won't generally be granted. The Browse API is free
and works, but returns *currently listed* items only. Asking prices skew
optimistic -- these numbers are a market signal, not a sold comp, and are
deliberately named so nothing downstream can mistake them for one.

Two rows are written per card per run, both source-tagged, matching
card_prices' existing append-only convention:

    ebay_active_low      cheapest active listing found
    ebay_active_median   median of the listings found

Rate limits matter: eBay's default application-token quota is around
5,000 Browse calls/day, and this makes one call per card. Pricing the
whole catalogue (20k+ Pokémon rows) is therefore not a daily job -- the
default targets only cards you actually own (inventory_items), which is
the set worth tracking anyway. --all is available but warns first.

Needs production eBay credentials to return real data: while EBAY_ENV is
sandbox (its default, and the only value scripts/start-control-plane.sh
will start the service with), the Browse API returns eBay's fake test
catalogue, so this will write sandbox-derived numbers. See
ARCHITECTURE.md for what going to production actually requires.

Unlike the other import scripts, this one needs the EBAY_* credentials in
its environment, and Settings deliberately doesn't read .env.local itself
(only scripts/start-control-plane.sh sources it, for the service). So
source it first, or the script will correctly refuse to run:

    set -a; . ./.env.local; set +a
    .venv/bin/python -m scripts.import_ebay_prices --dry-run
    .venv/bin/python -m scripts.import_ebay_prices --limit 50
    .venv/bin/python -m scripts.import_ebay_prices --game one_piece
    .venv/bin/python -m scripts.import_ebay_prices --all --limit 500
    .venv/bin/python -m scripts.import_ebay_prices --brand "Sonny Angel"
    .venv/bin/python -m scripts.import_ebay_prices --status personal_collection
"""

import argparse
import asyncio
import statistics
import time
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from automation_control.adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_market
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import OAuthError, configured, ebay_services
from automation_control.models import (
    CardPrice, CardSet, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem, InventoryStatus, TcgCard,
)

DEFAULT_LIMIT = 200
CALL_DELAY_SECONDS = 0.4
COMMIT_EVERY = 25


def build_query(item: CatalogItem, tcg: TcgCard | None, set_name: str | None = None, collectible: CollectibleProduct | None = None) -> tuple[str, str]:
    """(search query, token that must appear in a result's title).

    A bare catalogue name is far too vague to price against: this app owns
    a Sonny Angel literally called "Hamburger", and searching eBay for
    that returns kitchen gadgets. So each item type gets the most
    discriminating identifier it actually has, and the second return value
    is used to throw away results that clearly aren't the same thing:

      One Piece   set code + number  ("Divine Departure OP13-076")
      Pokémon     set name + number  ("Tangela Steam Siege 1") -- these
                  rows have set_id/card_sets rather than a set_code
      Collectible brand + name       ("Sonny Angel Hamburger")
    """
    if collectible is not None and collectible.brand:
        # a secret/chase figure sells well above the regular version of the
        # same character, so pricing one against the other understates it.
        # "secret" biases eBay's own ranking toward the right variant; the
        # relevance filter stays on brand so a differently-worded listing
        # ("chase", or just the figure name) isn't thrown away entirely.
        # ...but only add the word if the name doesn't already carry it.
        # A seeded row is called "Animal Series 4 secret", and querying
        # "Sonny Angel Animal Series 4 secret secret" is both sloppy and
        # worse for eBay's relevance ranking.
        secret = " secret" if collectible.is_secret and "secret" not in item.name.lower() else ""
        return f"{collectible.brand} {item.name}{secret}".strip(), collectible.brand
    if tcg is not None:
        if tcg.set_code and tcg.number:
            code = f"{tcg.set_code}-{tcg.number}"
            # A collector-export row's own name sometimes already carries
            # the code ("Piccolo - FB03-070 (Alternate Art)") -- appending
            # it again produced a garbled, zero-result query like
            # "... FB03-070 FB03-070".
            if code.lower() in item.name.lower():
                return item.name, item.name
            return f"{item.name} {code}".strip(), item.name
        if set_name and tcg.number:
            return f"{item.name} {set_name} {tcg.number}".strip(), item.name
        if tcg.number:
            return f"{item.name} {tcg.number}".strip(), item.name
    return item.name, item.name


def _price_values(listings: list[dict], must_contain: str | None = None) -> list[Decimal]:
    """Prices of the listings that actually look like the thing we asked
    for. eBay's search is fuzzy and happily returns adjacent products, so
    a title that doesn't even mention the item is dropped rather than
    averaged into its market price."""
    values = []
    needle = (must_contain or "").lower().strip()
    for listing in listings:
        if needle and needle not in (listing.get("title") or "").lower():
            continue
        raw = listing.get("price")
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        if value > 0:
            values.append(value)
    return values


def _targets(session, *, price_all: bool, game: str | None, limit: int,
             collectibles: bool = False, brand: str | None = None, status: str | None = None):
    query = (
        select(CatalogItem, TcgCard, CardSet.name, CollectibleProduct)
        .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id, isouter=True)
        .join(CardSet, CardSet.id == TcgCard.set_id, isouter=True)
        .join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id, isouter=True)
    )
    # Collectibles are targeted by *catalogue* rather than by ownership.
    # The default (owned only) is right for 22k TCG rows, but a seeded
    # Sonny Angel series isn't owned yet and is exactly what you want a
    # market price for -- that's how you decide whether to buy it.
    if collectibles or brand:
        query = query.where(CatalogItem.item_type == CatalogItemType.COLLECTIBLE)
        if brand:
            query = query.where(CollectibleProduct.brand.ilike(brand))
    elif status:
        owned_ids = (
            select(InventoryItem.catalog_item_id)
            .where(InventoryItem.catalog_item_id.isnot(None), InventoryItem.status == InventoryStatus(status))
            .distinct()
        )
        query = query.where(CatalogItem.id.in_(owned_ids))
    elif not price_all:
        owned_ids = select(InventoryItem.catalog_item_id).where(InventoryItem.catalog_item_id.isnot(None)).distinct()
        query = query.where(CatalogItem.id.in_(owned_ids))
    if game:
        query = query.where(TcgCard.game == game)
    return session.execute(query.limit(limit)).all()


async def _price_one(adapter, item: CatalogItem, tcg: TcgCard | None, set_name: str | None, collectible: CollectibleProduct | None, marketplace: str) -> tuple[list[Decimal], str | None, int]:
    query, must_contain = build_query(item, tcg, set_name, collectible)
    payload = await adapter.search_market(query, limit=20, marketplace=marketplace)
    listings = normalize_market(payload)
    values = _price_values(listings, must_contain)
    currency = next((listing.get("currency") for listing in listings if listing.get("currency")), None)
    return values, currency, len(listings)


async def run(args) -> int:
    settings = get_settings()
    if not configured(settings):
        raise SystemExit(
            f"eBay {settings.ebay_env} OAuth is not configured -- nothing to query. "
            "See ARCHITECTURE.md for which EBAY_* variables need to be set."
        )
    if settings.ebay_env != "production":
        print(f"NOTE: EBAY_ENV={settings.ebay_env} -- the Browse API will return eBay's fake sandbox catalogue, not real listings.\n")

    _, oauth = ebay_services(settings)
    try:
        token_payload = await oauth.application_token()
    except OAuthError as exc:
        raise SystemExit(f"could not get an eBay application token: {exc}") from exc

    adapter = EbaySandboxReadAdapter(token_payload["access_token"], environment=settings.ebay_env)

    priced = skipped = failed = 0
    with SessionLocal() as session:
        targets = _targets(session, price_all=args.all, game=args.game, limit=args.limit,
                           collectibles=args.collectibles, brand=args.brand, status=args.status)
        if not targets:
            print("no matching cards to price.")
            return 0
        print(f"pricing {len(targets)} card(s) against eBay {settings.ebay_env} ({args.marketplace})...\n")

        for index, (item, tcg, set_name, collectible) in enumerate(targets):
            query, _ = build_query(item, tcg, set_name, collectible)
            try:
                values, currency, seen = await _price_one(adapter, item, tcg, set_name, collectible, args.marketplace)
            except (EbayApiError, OAuthError) as exc:
                print(f"  fail  {query}: {exc}")
                failed += 1
                continue

            if not values:
                print(f"  none  {query} ({seen} result(s), none matched the item)")
                skipped += 1
                continue

            low, median = min(values), Decimal(str(statistics.median(values)))
            print(f"  ok    {query}: {len(values)}/{seen} relevant, low {low}, median {median}")
            priced += 1

            if not args.dry_run:
                for source, value in (("ebay_active_low", low), ("ebay_active_median", median)):
                    session.add(CardPrice(
                        id=str(uuid.uuid4()), card_id=item.id, variant=None,
                        source=source, price=value, currency=currency or "AUD",
                    ))
                # Commit as we go rather than once at the end: a full
                # inventory sweep is ~1,000 calls over ~15 minutes, and
                # losing all of it to an interruption at card 900 would be
                # a bad trade for one fewer transaction.
                if priced % COMMIT_EVERY == 0:
                    session.commit()

            if index < len(targets) - 1:
                time.sleep(CALL_DELAY_SECONDS)

        if not args.dry_run:
            session.commit()

    verb = "would be priced" if args.dry_run else "priced"
    print(f"\nDone: {priced} {verb}, {skipped} with no listings found, {failed} failed.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="query eBay and report, write nothing")
    parser.add_argument("--all", action="store_true", help="price the whole catalogue, not just what you own")
    parser.add_argument("--game", help="only price this game, e.g. one_piece or pokemon")
    parser.add_argument("--collectibles", action="store_true",
                        help="price every collectible in the catalogue, owned or not")
    parser.add_argument("--brand", help="only price this collectible brand, e.g. 'Sonny Angel'")
    parser.add_argument("--status", help="only price owned items with this InventoryStatus, e.g. personal_collection")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max cards to price this run (default {DEFAULT_LIMIT})")
    parser.add_argument("--marketplace", default="EBAY_AU", help="eBay marketplace id (default EBAY_AU)")
    args = parser.parse_args(argv)

    if args.all and args.limit > 1000:
        print(f"WARNING: --all with --limit {args.limit} makes {args.limit} Browse calls; eBay's default daily quota is ~5,000.\n")

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
