"""One-off: 2 Pokemon cards were falling out of the eBay export's variation
grouping under a literal "(no set)" title -- both had a real, correct
`TcgCard.set_code` but a NULL `set_id`, the field every set-name lookup in
this codebase (export builder, storefront) actually reads.

Zarude (set_code M5) is a simple backfill: 118 of 119 other cards sharing
that set_code already correctly link to jp-m5 ("アビスアイ") -- this was
just the one row that slipped through.

The other 3 (Ivory Pig ex, Team Rocket's Crobat ex, Excadrill ex, Yanma;
set_code CSV10C) have no catalogued CardSet at all -- an entirely
uncoded Chinese-market set. Identified via web search (2026-09-26):
"Chasing Glory Together", the Chinese release paired with the English
Scarlet & Violet-era "Destined Rivals" (Yanma is 183/182 there, 223/222
here -- same card, this market's own numbering/total). printed_total=222
is confirmed by the numbering seen in this catalogue (three cards under
222, one secret rare at 223); `total` (how far secret rares actually run)
isn't confirmed by anything on hand, so it's left unset rather than guessed.

    .venv/bin/python -m scripts.fix_no_set_pokemon_cards --dry-run
    .venv/bin/python -m scripts.fix_no_set_pokemon_cards
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CardSet, TcgCard

NEW_SET = dict(
    id="cn-csv10c", name="Chasing Glory Together", code="CSV10C",
    series="Scarlet & Violet", printed_total=222, game="pokemon",
)
RELINK_TO_NEW_SET = {"csv10C"}  # TcgCard.set_code values to point at NEW_SET
RELINK_TO_EXISTING = {"M5": "jp-m5"}  # set_code -> existing CardSet.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if session.get(CardSet, NEW_SET["id"]) is None:
            print(f"would create CardSet {NEW_SET['id']!r} ({NEW_SET['name']})" if args.dry_run
                  else f"creating CardSet {NEW_SET['id']!r} ({NEW_SET['name']})")
            if not args.dry_run:
                session.add(CardSet(**NEW_SET))
        else:
            print(f"CardSet {NEW_SET['id']!r} already exists -- not recreating")

        relinked = 0
        for set_code in RELINK_TO_NEW_SET:
            rows = session.scalars(select(TcgCard).where(TcgCard.set_code == set_code, TcgCard.set_id.is_(None))).all()
            for tcg in rows:
                print(f"  {'would link' if args.dry_run else 'linking'} {tcg.catalog_item_id} "
                      f"(#{tcg.number}, set_code={set_code}) -> {NEW_SET['id']}")
                if not args.dry_run:
                    tcg.set_id = NEW_SET["id"]
                    relinked += 1

        for set_code, target_id in RELINK_TO_EXISTING.items():
            rows = session.scalars(select(TcgCard).where(TcgCard.set_code == set_code, TcgCard.set_id.is_(None))).all()
            for tcg in rows:
                print(f"  {'would link' if args.dry_run else 'linking'} {tcg.catalog_item_id} "
                      f"(#{tcg.number}, set_code={set_code}) -> {target_id}")
                if not args.dry_run:
                    tcg.set_id = target_id
                    relinked += 1

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        session.commit()
        print(f"\ndone -- {relinked} TcgCard row(s) relinked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
