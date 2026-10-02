"""Incrementally price scanned inventory against pokemonpricetracker.com.

The free tier is 100 credits/day and a search costs a fixed 3 credits (its
search `limit`) per distinct card regardless of how many of that card's
variants come back priced -- see scan_ingest/pricing.py's
`fetch_all_pokemonpricetracker_variants`. `--max-cards` caps how many new
cards get searched in one run so it stays comfortably inside that budget;
run this once a day (see scripts/run-pokemonpricetracker-backfill.sh and its
systemd timer) and it works through the backlog a bit at a time, settling
into a fast no-op once every held card+variant has a price.

Only searches cards that don't already have a pokemonpricetracker.com price
for a variant actually held, so re-running (including a partial run cut off
mid-way) is always safe -- nothing gets priced twice.

    .venv/bin/python -m scripts.backfill_pokemonpricetracker_prices [--max-cards N] [--dry-run]
"""

import argparse
import time
from collections import defaultdict

import httpx
from sqlalchemy import func, select

from automation_control.adapters.pokemonpricetracker import PokemonPriceTrackerRateLimited
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogCard, InventoryItem, TcgCard
from automation_control.scan_ingest.pricing import POKEMONPRICETRACKER_SOURCE_NAME, fetch_all_pokemonpricetracker_variants

DEFAULT_MAX_CARDS = 25
PACING_DELAY_SECONDS = 0.5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Incrementally price scanned inventory via pokemonpricetracker.com")
    parser.add_argument("--max-cards", type=int, default=DEFAULT_MAX_CARDS, help="stop after searching this many distinct cards this run")
    parser.add_argument("--dry-run", action="store_true", help="report what would be searched, write nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.pokemonpricetracker_api_key:
        print("POKEMONPRICETRACKER_API_KEY is not configured -- nothing to do.")
        return 1

    priced_count = 0
    no_quote: list[str] = []
    searched = 0

    with SessionLocal() as session:
        # CatalogCard began as Pokemon-only, but now also contains scanned
        # Dragon Ball references. Join the canonical TCG detail and filter the
        # game explicitly: otherwise a Pokemon-only API search burns the daily
        # allowance on guaranteed misses before reaching newly held Pokemon.
        # Quantity zero is no longer held stock and must not consume a credit.
        held = session.execute(
            select(InventoryItem.catalog_item_id, InventoryItem.variant)
            .join(CatalogCard, CatalogCard.id == InventoryItem.catalog_item_id)
            .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
            .where(TcgCard.game == "pokemon", InventoryItem.quantity > 0)
            .distinct()
        ).all()
        already_priced = set(
            session.execute(
                select(CardPrice.catalog_item_id, CardPrice.variant).where(CardPrice.source == POKEMONPRICETRACKER_SOURCE_NAME).distinct()
            ).all()
        )

        # Group by card so one search covers every held variant of it, per
        # fetch_all_pokemonpricetracker_variants -- pricing a card with two
        # held variants must not cost two searches.
        variants_needed: dict[str, set] = defaultdict(set)
        for card_id, variant in held:
            if (card_id, variant) not in already_priced:
                variants_needed[card_id].add(variant)

        if not variants_needed:
            print("Everything held already has a pokemonpricetracker.com price. Nothing to do.")
            return 0

        # Spend scarce daily searches where they can close a real pricing gap
        # first. A card with no observation from any source precedes one that
        # already has eBay/catalogue coverage; among covered cards the oldest
        # observation goes first. Lexicographic IDs used to leave newly held,
        # completely unpriced cards behind months of already-covered backlog.
        last_prices = dict(session.execute(
            select(CardPrice.catalog_item_id, func.max(CardPrice.fetched_at))
            .where(CardPrice.catalog_item_id.in_(variants_needed))
            .group_by(CardPrice.catalog_item_id)
        ).all())
        card_ids = sorted(
            variants_needed,
            key=lambda card_id: (
                last_prices.get(card_id) is not None,
                last_prices[card_id].timestamp() if last_prices.get(card_id) is not None else float("-inf"),
                card_id,
            ),
        )
        client = httpx.Client()
        try:
            for card_id in card_ids:
                if searched >= args.max_cards:
                    break
                card = session.get(CatalogCard, card_id)
                if card is None:
                    continue

                needed = variants_needed[card_id]
                set_label = card.card_set.name if card.card_set else card.set_id
                if args.dry_run:
                    print(f"would search: {card.name} #{card.number} ({set_label}) for {sorted(v.value for v in needed)}")
                    searched += 1
                    continue

                # A hard rate-limit/credit-exhaustion here means every
                # remaining card would fail the exact same way -- stop the
                # whole run immediately rather than let the loop plough
                # through the rest, which is what actually got a real API
                # key temporarily blocked (see PokemonPriceTrackerRateLimited).
                try:
                    quotes = fetch_all_pokemonpricetracker_variants(settings.pokemonpricetracker_api_key, card, client=client)
                except PokemonPriceTrackerRateLimited as exc:
                    print(f"Stopping early -- pokemonpricetracker.com asked to wait {exc.retry_after_seconds}s (today's credits are likely exhausted).")
                    break
                searched += 1
                for variant in needed:
                    quote = quotes.get(variant)
                    if quote is None:
                        no_quote.append(f"{card.name} #{card.number} ({set_label}, {variant.value})")
                        continue
                    session.add(CardPrice(catalog_item_id=card.id, variant=variant, source=quote.source, price=quote.price, currency=quote.currency))
                    priced_count += 1
                session.commit()

                if searched < len(card_ids) and searched < args.max_cards:
                    time.sleep(PACING_DELAY_SECONDS)
        finally:
            client.close()

    remaining = len(variants_needed) - searched
    verb = "would search" if args.dry_run else "searched"
    print(f"{verb.capitalize()} {searched} card(s), priced {priced_count} card+variant pair(s), {len(no_quote)} had no match.")
    if remaining > 0:
        print(f"{remaining} card(s) still need pricing -- run again (tomorrow, once today's credits reset) to continue.")
    if no_quote and not args.dry_run:
        print("No pokemonpricetracker.com match for:")
        for line in no_quote:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
