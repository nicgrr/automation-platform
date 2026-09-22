"""One-off: record real eBay active-listing prices for the 8 catalogued
cards from sheet-20260921-192456-112823 (7 Dragon Ball Super Divers + 1
Super Dragon Ball Heroes -- see reconcile_dbsd_sheet_112823.py and
reconcile_sdbh_sheet_112823.py for how they got their real catalogue ids).

Neither game has an automated eBay pricing path: scripts/import_ebay_prices.py
needs production eBay OAuth credentials, which aren't configured (EBAY_ENV
is sandbox). Nic asked to look these up manually instead, so this records
what a live ebay.com.au search on 2026-09-22 actually showed -- one
search per card, quoted on its exact printed code so eBay's own "results
matching fewer words" padding doesn't leak in unrelated cards.

Each search's raw active-listing prices were filtered by hand before
computing low/median/high, in the same direction _ebay_prices callers
already discount these figures (asking price, not sold comp -- see
import_ebay_prices.py's own module docstring): multi-card bundles/sets
excluded (they price several cards at once, not this one), and -- for
SDV8-SEC2 and UGM5-002 specifically -- graded (PSA/CGC/ARK/BGS) and
"Parallel"/"DA" alternate-print listings excluded too, since those are a
different, much rarer physical printing of the same card number and
blending them in would badly skew the estimate for the plain print this
catalogue entry actually is.

    .venv/bin/python -m scripts.price_dbsd_sheet_112823 --dry-run
    .venv/bin/python -m scripts.price_dbsd_sheet_112823
"""

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogCard

# card_id -> (low, median, high), AUD, from ebay.com.au active listings
# surveyed 2026-09-22.
PRICES: dict[str, tuple[str, str, str]] = {
    "dbsd-sdv11-pur1": ("4.02", "16.97", "46.33"),   # Son Goku: BR
    "dbsd-sdv11-pur2": ("3.08", "14.99", "30.88"),   # Son Gohan: SH
    "dbsd-sdv11-pur3": ("4.02", "10.80", "30.88"),   # Trunks: Future
    "dbsd-sdv11-pur4": ("4.02", "9.42", "46.33"),    # Piccolo
    "dbsd-sdv10-pur1": ("7.40", "19.93", "46.34"),   # Son Goku
    "dbsd-sdv8-sec2": ("25.47", "54.05", "106.59"),  # Broly: BR (Secret rare)
    "dbsd-sdv1-039": ("1.53", "2.33", "16.99"),      # Whis
    "sdbh-ugm5-002": ("2.01", "13.49", "19.31"),     # Son Gohan: Boyhood (SSJ2), C rarity
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        written = 0
        now = datetime.now(UTC)
        for card_id, (low, median, high) in PRICES.items():
            card = session.get(CatalogCard, card_id)
            if card is None:
                print(f"skip {card_id}: no such catalogue card")
                continue
            print(f"{card.name}: low ${low}, median ${median}, high ${high} AUD")
            if args.dry_run:
                written += 3
                continue
            for source, value in (("ebay_active_low", low), ("ebay_active_median", median), ("ebay_active_high", high)):
                session.add(CardPrice(card_id=card_id, variant=None, source=source,
                                      price=Decimal(value), currency="AUD", fetched_at=now))
                written += 1

        if args.dry_run:
            print(f"\n--dry-run: {written} row(s) would be written, nothing saved.")
            return 0

        session.commit()
        print(f"\nWrote {written} CardPrice row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
