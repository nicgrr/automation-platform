"""Replace legacy OPTCG set codes with named canonical CardSet IDs."""

import argparse
import re

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardSet, TcgCard
from scripts.import_one_piece_from_optcgapi import GAME, SET_ID_ALIASES


def _key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing them")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        card_sets = session.scalars(select(CardSet).where(CardSet.game == GAME)).all()
        by_code: dict[str, list[CardSet]] = {}
        for card_set in card_sets:
            by_code.setdefault(_key(card_set.code), []).append(card_set)

        repaired = unresolved = 0
        cards = session.scalars(select(TcgCard).where(TcgCard.game == GAME)).all()
        for card in cards:
            if session.get(CardSet, card.set_id):
                continue
            target = session.get(CardSet, SET_ID_ALIASES.get(card.set_id, ""))
            if target is None:
                matches = by_code.get(_key(card.set_id), [])
                target = matches[0] if len(matches) == 1 else None
            if target is None:
                unresolved += 1
                continue
            print(f"{card.catalog_item_id}: {card.set_id} -> {target.id} ({target.name})")
            card.set_id = target.id
            repaired += 1

        if not args.dry_run:
            session.commit()
        print(f"{'Would repair' if args.dry_run else 'Repaired'} {repaired} card(s); {unresolved} unresolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
