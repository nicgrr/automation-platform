"""Check that every committed card's photo is actually that card.

Identification can be confidently wrong. With ~20k reference cards an
unrelated card sometimes hashes closer than the right one, and same-numbered
cards in different sets are an easy confusion -- a Sword & Shield Joltik
(070/202) was committed as an Unbroken Bonds Gengar (70/214), carrying that
card's $33 valuation with it.

This compares each inventory item's stored photo against the card it is
filed under, and for anything that doesn't match, says what the photo looks
like instead. Read-only: it reports, it never edits inventory, because
choosing the right card is exactly the judgement that got this wrong.

Treat the output as triage, not a verdict. On its first real run six items
were flagged and three were correct all along -- a crop that catches the
edge of the next card, or a foil, hashes badly against its own reference
without being the wrong card. Equally, it misses cards filed under the
wrong *set* when the artwork is a reprint shared by both, which is how ten
Paldean Fates cards sat under Scarlet & Violet with only five flagged. Read
the number printed on the scan before changing anything.

The detection itself lives in automation_control/inventory_audit.py, shared
with the /inventory-audit review page (2026-09-10) -- that page is where a
human actually works through what this script finds; this run is what feeds
it and what inventory-audit.timer logs hourly.

    .venv/bin/python -m scripts.audit_inventory_photos [--threshold 12]
"""

import argparse

from automation_control.database import SessionLocal
from automation_control.inventory_audit import DEFAULT_THRESHOLD, find_mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify each committed card's photo matches the card")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="Hamming distance above which a photo is considered a mismatch")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        mismatches = find_mismatches(session, threshold=args.threshold)

        if not mismatches:
            print("Every committed card's photo matches the card it's filed under.")
            return 0

        print(f"{len(mismatches)} item(s) whose photo does not match the card they're filed under:\n")
        print(f"  {'filed as':<46}{'photo looks like':<46}")
        print("  " + "-" * 92)
        for m in mismatches:
            print(f"  {m['filed_label'][:44]:<46}{m['looks_like_label'][:44]:<46}")
            print(f"  {'  d' + str(m['own_distance']) + ' from what it says it is':<46}"
                  f"{'  d' + str(m['looks_like_distance']) + ' from this':<46}")
        print("\nNothing was changed. Correct one in the browser, or re-scan the card.")
        print("Full review: /inventory-audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
