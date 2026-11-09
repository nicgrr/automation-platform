"""One-off: give three photo-added slabs the card identity Listing Studio needs.

Klawf PSA 9, Sanji Beckett 9.5 and Keldeo PSA 10 were saved through the
product-photo graded path, which creates the catalogue item, stock and draft
but no TcgCard row. Listing Studio therefore can't generate copy and the
outgoing eBay title has no card to build from. Slabs prepared through Graded
cards get this row; these three get the same, read from their slab labels.

Additive only: no stock, price, photo or listing status changes. The
existing jp-cp5 set is named 冷酷の反逆者, not Dream Shine Collection, so
Keldeo gets its own correctly named set rather than being linked to it.

Dry run is the default and writes nothing::

    (set -a; . ./.env.local; set +a; .venv/bin/python -m scripts.add_slab_card_identity_20261109)
    (set -a; . ./.env.local; set +a; .venv/bin/python -m scripts.add_slab_card_identity_20261109 --apply)
"""

from __future__ import annotations

import argparse
import uuid

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.listing_quality import generated_ebay_title
from automation_control.models import CardSet, CatalogItem, CatalogItemType, InventoryItem, ShopListing, TcgCard

ACTOR = "claude-code (owner-requested 2026-11-09)"
NEW_SET = dict(id="pokemon-jp-cp5-dream-shine", name="Mythical & Legendary Dream Shine Collection",
               code="CP5", game="pokemon_japan", release_date="2016")
SLABS = (  # catalogue item, expected grader/cert, item name, card fields (from the slab label)
    ("photo-product-9604ab83a6d14a943a716064a5350427", ("PSA", "117480459"), "Klawf",
     dict(game="pokemon", set_id="sv1", set_code="SVI", character="Klawf", number="217/198",
          language="English", rarity="Illustration Rare", parallel=None, release_date="2023")),
    ("photo-product-400d0f58da903e80049d36206d50a185", ("Beckett", "0020112666"), "Sanji",
     dict(game="one_piece", set_id="op-23496", set_code="PRB-01", character="Sanji", number="PRB01-001",
          language="English", rarity="Leader", parallel="Alternate Art", release_date="2024")),
    ("photo-product-59e7099d6ad81795f63d49267ac5221d", ("PSA", "119183410"), "Keldeo",
     dict(game="pokemon_japan", set_id=NEW_SET["id"], set_code="CP5", character="Keldeo", number="013/036",
          language="Japanese", rarity="Holo", parallel="1st Edition", release_date="2016")),
)


def _refuse(message: str) -> None:
    raise SystemExit(f"refusing: {message}")


def run(apply: bool) -> int:
    events = []
    with SessionLocal() as session:
        if session.get(CardSet, NEW_SET["id"]) is None:
            session.add(CardSet(**NEW_SET))
            events.append(("card_set", NEW_SET["id"], {"created": NEW_SET}))
        for cid, (company, cert), name, fields in SLABS:
            item = session.get(CatalogItem, cid)
            if item is None or item.item_type != CatalogItemType.GRADED_CARD:
                _refuse(f"{cid} is missing or not a graded card")
            if session.get(TcgCard, cid) is not None:
                _refuse(f"{cid} already has card identity")
            if fields["set_id"] != NEW_SET["id"] and session.get(CardSet, fields["set_id"]) is None:
                _refuse(f"set {fields['set_id']} not found")
            stock = session.query(InventoryItem).filter_by(catalog_item_id=cid).one()
            if (stock.grading_company, stock.certification_number) != (company, cert):
                _refuse(f"{cid} stock is no longer {company} {cert}")
            listing = session.query(ShopListing).filter_by(catalog_item_id=cid).one()
            before_name = item.name
            item.name = name
            session.add(TcgCard(catalog_item_id=cid, **fields))
            session.flush()
            card = session.get(TcgCard, cid)
            title = generated_ebay_title(listing, item, card, session.get(CardSet, card.set_id), stock)
            print(f"{before_name!r} -> {name!r}: {fields['set_code']} {fields['number']}  title: {title}")
            events.append(("catalog_item", cid, {"tcg_card_added": fields, "name_from": before_name, "name_to": name,
                                                 "certification_number": cert}))
        if not apply:
            session.rollback()
            print("dry run: nothing written (pass --apply)")
            return 0
        for resource_type, resource_id, details in events:
            # record_event commits, so it runs only once everything above is staged.
            record_event(session, actor_type="user", actor_id=ACTOR, action="graded.card_identity_added",
                         resource_type=resource_type, resource_id=resource_id, outcome="success",
                         correlation_id=str(uuid.uuid4()), details=details)
        session.commit()
        print("applied")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    return run(parser.parse_args(argv).apply)


if __name__ == "__main__":
    raise SystemExit(main())
