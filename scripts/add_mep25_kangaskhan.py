"""One-off: catalog and commit the Mega Kangaskhan ex jumbo promo (#25) the
user found sitting in the review queue -- confirmed via PriceCharting
(pricecharting.com/game/pokemon-promo/mega-kangaskhan-ex-jumbo-25) as a real
Black Star Promo card, but one this app's catalog data source has no set
entry for at all (unlike svp, which covers the Charizard/Zapdos jumbos).

Creates a minimal CardSet + CatalogCard (the scan-ingest reference side) and
matching CatalogItem + TcgCard (the storefront-facing side, sharing the same
id -- see CatalogCard/CatalogItem's shared-id convention, confirmed against
svp-196), then commits the physical copy to inventory and records a manual
CardPrice, since there's no automatic pricing source for an uncatalogued
promo. Does NOT create a ShopListing -- that's a separate deliberate step
(condition wording, jumbo-specific shipping) not decided here.

    .venv/bin/python -m scripts.add_mep25_kangaskhan
"""

import getpass
from decimal import Decimal
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import (
    CardPrice, CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType, TcgCard,
)
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume
from sqlalchemy import select

SET_ID = "mep"
NUMBER = "25"
CARD_ID = f"{SET_ID}-{NUMBER}"
NAME = "Mega Kangaskhan ex"
CROP = Path("scan_ingest_data/media/needs_review/sheet-20260911-175126-838252-card1-not-card-shaped.jpg")
VARIANT = CardVariant.NORMAL
CONDITION = "Near Mint"
PRICE = Decimal("3.47")


def main() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        if session.get(CardSet, SET_ID) is None:
            session.add(CardSet(id=SET_ID, name="Mega Evolution Black Star Promos", series="Mega Evolution"))
        if session.get(CatalogCard, CARD_ID) is None:
            session.add(CatalogCard(id=CARD_ID, set_id=SET_ID, number=NUMBER, name=NAME, rarity="Promo"))
        if session.get(CatalogItem, CARD_ID) is None:
            session.add(CatalogItem(id=CARD_ID, item_type=CatalogItemType.TCG_CARD, name=NAME))
        if session.get(TcgCard, CARD_ID) is None:
            session.add(TcgCard(catalog_item_id=CARD_ID, game="pokemon", set_id=SET_ID, number=NUMBER, rarity="Promo"))
        if session.scalar(select(CardPrice).where(CardPrice.card_id == CARD_ID, CardPrice.source == "manual")) is None:
            session.add(CardPrice(card_id=CARD_ID, variant=VARIANT, source="manual", price=PRICE, currency="AUD"))
        session.commit()

        scan_session = start_or_resume(session, SET_ID, VARIANT, None, user=getpass.getuser(), unattended=True)
        identification = Identification(
            card_id=CARD_ID, number=NUMBER, name=NAME,
            source=Source.PHASH, confidence=1.0, note="manually catalogued -- see PriceCharting jumbo promo listing",
        )
        try:
            result = commit_card(
                session, identification, CROP, Path(settings.scan_media_dir),
                variant=VARIANT, condition=CONDITION,
                scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
            )
        except CommitRefused as exc:
            print(f"error: {exc}")
            return 1
        session.commit()

    CROP.unlink(missing_ok=True)
    print(f"committed: {result.summary}")
    print(f"recorded manual price: ${PRICE} AUD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
