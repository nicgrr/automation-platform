"""One-off: record real market prices for 22 scanned, in-inventory cards
that had zero CardPrice rows -- discovered 2026-09-26 when Nic asked why
a fair few scanned cards weren't showing up to list on eBay. Not all of
these have an automated pricing source (SNAPSHOT_PRICE_SOURCES has no
entry for plain "pokemon" promos or Chinese-market SDBH codes), so this
records what a real, live search actually found for each card on
2026-09-26, same methodology as price_dbsd_sheet_112823.py and the
jp-m1s-042/jp-sv10-055 pair from earlier this session:

  - pricecharting.com's "Ungraded" figure (a real market aggregate from
    historic sales) where PriceCharting has the card -- one CardPrice row,
    source="pricecharting_market", USD.
  - cardotaku.com's own listed shop price for Super Dragon Ball Heroes
    cards PriceCharting doesn't track at all -- source="cardotaku_shop",
    USD, matching CardotakuSnapshotPriceSource's own source name.
  - tcgplayer.com's own market price where that's what a search actually
    surfaced -- source="tcgplayer_market", USD.

Two cards (sdbh-abs-12 Cell, sdbh-cp-zh-307 Jiren/Goku China promo) had
no findable price anywhere after a real search and are left unpriced
here rather than guessed -- cp-307 is the same Chinese-market regional
code already confirmed unpriceable earlier this session (no eBay
presence for that exact code); abs-12 only turned up multi-card sealed
blister prices ($49 for a 2-card set), which doesn't cleanly divide into
a single-card figure without guessing.

    .venv/bin/python -m scripts.price_scanned_backlog_20260926 --dry-run
    .venv/bin/python -m scripts.price_scanned_backlog_20260926
"""

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from automation_control.database import SessionLocal
from automation_control.models import CardPrice, CatalogCard

# card_id -> (price, source), USD unless noted. Comment on each cites what
# was actually searched/found on 2026-09-26.
PRICES: dict[str, tuple[str, str]] = {
    "mep-8": ("1.11", "pricecharting_market"),        # Golduck, MEP Black Star Promos
    "mep-68": ("1.40", "pricecharting_market"),        # Makuhita, MEP Black Star Promos
    "mep-20": ("0.36", "pricecharting_market"),        # Sneasel, MEP Black Star Promos
    "svp-218": ("1.50", "pricecharting_market"),       # Team Rocket's Persian ex, SVP
    "svp-210": ("1.99", "pricecharting_market"),       # Tornadus, SVP
    "svp-48": ("3.00", "pricecharting_market"),        # Squirtle, SVP
    "sm4-103": ("10.00", "pricecharting_market"),      # Nihilego-GX, Crimson Invasion
    "tcgp-615542": ("3.45", "pricecharting_market"),   # Zamasu: Fused FB02-044 (Dokkan Battle Alt Art)
    "tcgp-682030": ("18.00", "pricecharting_market"),  # Son Goku FB09-036 (Alternate Art)
    # Japanese-language printing of the same FB09-096 card PriceCharting
    # prices at $7.18 -- no separate JP-specific figure found, same
    # physical card content.
    "dbfw-jp-fb09-096-p1": ("7.18", "pricecharting_market"),
    "tcgp-666730": ("0.13", "tcgplayer_market"),       # Son Gohan: Youth FB08-056 (Alternate Art)
    "dragon-ball-fusion-world-st01-053": ("1.95", "pricecharting_market"),  # Vegeta ST01-053
    "dragon-ball-super-ex25-36": ("0.72", "pricecharting_market"),  # Son Goten, Will Overtaken
    "dragon-ball-super-ex25-09": ("2.02", "pricecharting_market"),  # Krillin, Masterful Teamwork
    "dragon-ball-super-ex25-02": ("0.63", "pricecharting_market"),  # Champa, Tournament Proposal
    "dragon-ball-super-ex06-14": ("0.27", "pricecharting_market"),  # Plan for Destruction
    "dbsd-sdv1-070": ("12.19", "cardotaku_shop"),      # Vegeta: DA, SDV1-070 GDR
    "sdbh-pums14-25": ("1.82", "cardotaku_shop"),      # Trunks: Younenki, PUMS14-25
    "sdbh-sh8-hcp2": ("2.08", "cardotaku_shop"),       # SH8-HCP2
    "sdbh-mm3-cp2": ("3.45", "cardotaku_shop"),        # Turles, MM3-CP2 (listed price before selling out)
    # sdbh-abs-12 (Cell) and sdbh-cp-zh-307 (Jiren & Son Goku SSB, China
    # promo) intentionally absent -- genuine misses, see module docstring.
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        written = 0
        now = datetime.now(UTC)
        for card_id, (price, source) in PRICES.items():
            card = session.get(CatalogCard, card_id)
            if card is None:
                print(f"skip {card_id}: no such catalogue card")
                continue
            print(f"{card.name}: ${price} USD ({source})")
            if args.dry_run:
                written += 1
                continue
            session.add(CardPrice(catalog_item_id=card_id, variant=None, source=source,
                                  price=Decimal(price), currency="USD", fetched_at=now))
            written += 1

        if args.dry_run:
            print(f"\n--dry-run: {written} row(s) would be written, nothing saved.")
            return 0

        session.commit()
        print(f"\nWrote {written} CardPrice row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
