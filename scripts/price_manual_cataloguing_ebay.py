"""One-off: record eBay-derived prices for the 45 hand-catalogued Dragon
Ball items scripts/price_check_dragonball_manual.py reported as having no
quote (38 with a snapshot source but no cached data for this exact
printing, plus 7 vintage Carddass items with no pricing source at all --
see scripts/catalog_sheet_*.py for how they got their catalogue ids).

Same situation as scripts/price_dbsd_sheet_112823.py: neither game has an
automated eBay pricing path (no production OAuth configured), so this
was priced by hand via real web search on 2026-09-25, converted from USD
to AUD at ~1.5x. Two pricing strategies, per the same active-listing
methodology as price_dbsd_sheet_112823.py (multi-card bundles and
graded/alternate-print listings excluded by hand):

1. Direct comp -- searched the card's own printed code and found real
   comparable eBay/retailer listings for that exact card or a
   near-identical numbered sibling in the same sub-set.
2. Card-type comp -- for cards with no printed code at all (the "misc"
   buckets, Hero Avatar Cards, the SKILLS promo, the Panini lenticular
   insert, and the 1996 Artbox scene cards), searched the shared card
   TYPE once and applied one price band across every card of that type,
   since they are functionally indistinguishable for pricing purposes.
   The Artbox band is derived from confirmed 10-card pack prices
   ($10.95-$22.39 for a sealed pack) rather than a direct single-card
   comp, since no single-card listing price could be confirmed.

sdbh-cp-zh-307 (a Chinese-market SDBH-style promo) turned up no usable
eBay data at all -- an obscure regional code -- and is deliberately left
unpriced rather than guessed.

NOTE: while researching this, sdbh-um4-034 turned out to be listed
everywhere as "Syn Shenron: Xeno", not "Ichiboshi Ryu: Bino" as
scripts/catalog_sheet_607520.py named it -- the catalogue identification
looks wrong and should be corrected separately; this script prices the
card_id as cataloged, unaffected either way.

    .venv/bin/python -m scripts.price_manual_cataloguing_ebay --dry-run
    .venv/bin/python -m scripts.price_manual_cataloguing_ebay
"""

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogCard

# card_id -> (low, median, high), AUD, from eBay/retailer active listings
# surveyed 2026-09-25 (USD converted at ~1.5x where the source was USD).
PRICES: dict[str, tuple[str, str, str]] = {
    # --- Direct comps: own printed code found on eBay/retailer listings ---
    "sdbh-um4-034": ("2.50", "3.20", "5.00"),        # $1.99 USD confirmed (PicClick) -- see name-mismatch note above
    "sdbh-um8-cp6": ("12.00", "15.00", "22.50"),     # Broly: BR CP, $8-$14.98 USD across sellers
    "sdbh-ugm8-cp5": ("3.00", "6.00", "9.00"),       # Paragus CP, $2.05-$5.82 USD across sellers
    "sdbh-hgd6-cp3": ("0.60", "1.50", "3.00"),       # comparable HGD-series cards $0.39-$2.05 USD
    "sdbh-sh1-cp8": ("8.00", "15.00", "25.00"),      # Zamasu CP, $9.98 USD retail median
    "sdbh-bm7-cp6": ("3.50", "5.50", "8.50"),        # Kefla CP, $2.23-$5.57 USD across sellers
    "sdbh-pums12-17": ("7.50", "10.50", "16.50"),    # Goku promo, $5-$11 USD typical (excluding one $40 outlier)
    "dbsd-sdvpj-036": ("2.25", "3.75", "6.00"),      # Gohan: Youth promo, $2.52 USD retail
    "dbsd-lz03-038": ("4.00", "5.50", "7.50"),       # unofficial "Dragon Heroes" LZ03 line, R/SSR comps $2.69-$4.99 USD

    # vintage Carddass Hondan regular (non-prism) cards, $1.17-$2.64 USD across comparable regular-card listings
    "db-carddass-hondan-549": ("1.75", "2.75", "4.00"),
    "db-carddass-hondan-550": ("1.75", "2.75", "4.00"),
    "db-carddass-hondan-552": ("1.75", "2.75", "4.00"),
    "db-carddass-hondan-554": ("1.75", "2.75", "4.00"),
    "db-carddass-hondan-556": ("1.75", "2.75", "4.00"),
    "db-carddass-hondan-558": ("1.75", "2.75", "4.00"),
    "db-carddass-dp-029": ("1.75", "2.75", "4.00"),  # same vintage-regular comp band; no DP-specific listing found

    # --- Card-type comps: no printed code, priced the shared type once ---
    # Generic uncoded/no-result SDBH campaign & promo cards -- comp band
    # from the several *priced* SDBH CP/PUMS cards above ($2-$15 USD range)
    "sdbh-misc-vegeta-da": ("2.25", "6.00", "15.00"),
    "sdbh-misc-super-baby-2": ("2.25", "6.00", "15.00"),
    "sdbh-misc-android17-mir": ("2.25", "6.00", "15.00"),
    "sdbh-misc-beerus": ("2.25", "6.00", "15.00"),
    "sdbh-misc-majin-buu-evil": ("2.25", "6.00", "15.00"),
    "sdbh-pumst01-040": ("2.25", "6.00", "15.00"),
    "sdbh-hj6-59": ("2.25", "6.00", "15.00"),
    "sdbh-gdds-01": ("2.25", "6.00", "15.00"),
    # Generic uncoded Divers commons, comp: APT-017 R $3.75 USD
    "dbsd-ap3-018": ("2.25", "5.25", "9.00"),
    "dbsd-ap3-019": ("2.25", "5.25", "9.00"),
    # Hero Avatar Card (SDBH promo), comp: standard promos $4.99-$6.99 USD (excludes a $461 limited-foil outlier)
    "sdbh-hero-avatar-card": ("4.50", "9.00", "15.00"),
    "sdbh-hero-avatar-gogeta": ("4.50", "9.00", "15.00"),
    "sdbh-hero-avatar-dr-gero": ("4.50", "9.00", "15.00"),
    # SKILLS app promo, comp: EUR 2.50 (~$2.96 USD)
    "sdbh-skills-goku-ui": ("3.00", "5.25", "9.00"),
    # Uncoded Divers Piccolo, comp: R-rarity Divers Piccolo $3.75 USD
    "dbsd-misc-piccolo-buppa": ("3.00", "6.00", "12.00"),
    "dbsd-misc-piccolo-pr": ("3.00", "6.00", "12.00"),
    # English Panini-era lenticular insert, comp: lenticular gold Trunks $9 USD
    "dbz-misc-trunks-lenticular": ("6.00", "13.50", "22.50"),
    # 1996 Artbox scene cards -- derived from confirmed sealed 10-card pack
    # prices ($10.95-$22.39), not a direct single-card comp (none found)
    "dbz-misc-piccolo-artbox": ("0.75", "2.25", "6.00"),
    "dbz-misc-gohan-kid-sword-a": ("0.75", "2.25", "6.00"),
    "dbz-misc-gohan-kid-sword-b": ("0.75", "2.25", "6.00"),
    "dbz-misc-goku-krillin-gang": ("0.75", "2.25", "6.00"),
    "dbz-misc-goku-vs-nappa": ("0.75", "2.25", "6.00"),
    "dbz-misc-krillin-power": ("0.75", "2.25", "6.00"),
    "dbz-misc-nappa-vs-gohan": ("0.75", "2.25", "6.00"),
    "dbz-misc-vegeta-saiyan-saga": ("0.75", "2.25", "6.00"),
    "dbz-misc-saiyan-warrior": ("0.75", "2.25", "6.00"),
    "dbz-misc-goku-standing": ("0.75", "2.25", "6.00"),
    "dbz-misc-otherworld-fighters": ("0.75", "2.25", "6.00"),
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
                session.add(CardPrice(catalog_item_id=card_id, variant=None, source=source,
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
