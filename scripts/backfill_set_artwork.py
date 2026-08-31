"""Populate logo_url/symbol_url for sets cached before those columns existed.

`cache_set` records them going forward (see catalog._upsert_set), but sets
cached earlier have them empty. This fetches the set list once and fills in
the gaps -- one API call total, no per-card work, no image downloads.

    .venv/bin/python -m scripts.backfill_set_artwork
"""

import sys

from sqlalchemy import select

from automation_control.adapters import pokemontcg_catalog
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardSet


def main() -> int:
    settings = get_settings()
    try:
        upstream = {s["id"]: s for s in pokemontcg_catalog.list_sets(api_key=settings.pokemontcg_api_key)}
    except Exception as exc:
        print(f"error: could not fetch the set list: {exc}", file=sys.stderr)
        return 1

    filled = missing = 0
    with SessionLocal() as session:
        for card_set in session.scalars(select(CardSet)):
            if card_set.logo_url:
                continue
            images = (upstream.get(card_set.id) or {}).get("images") or {}
            if not images.get("logo"):
                print(f"  no artwork upstream for {card_set.id} ({card_set.name})")
                missing += 1
                continue
            card_set.logo_url = images.get("logo")
            card_set.symbol_url = images.get("symbol")
            filled += 1
            print(f"  {card_set.id:<10} {card_set.name}")
        session.commit()

    print(f"\nFilled artwork for {filled} set(s); {missing} had none upstream.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
