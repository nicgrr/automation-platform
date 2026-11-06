"""One-off corrections to photo confirmations made on 2026-11-06.

Japanese Pokemon Card 151 (sv2a) cards share their numbering with the
English 151 set (sv3pt5), and three were confirmed onto the English
catalogue card, so their stock, title and representative back were English:

- Charmander 168/165 and Poliwhirl 176/165: new stock rows, moved to the
  Japanese catalogue card.
- Meowth 052/165: the confirmation reused the English Meowth stock row from
  2026-09-19 and switched its finish to Master Ball pattern. The Japanese
  card gets its own stock row; the English row's reverse-holo finish is put
  back and the row is left alone (the owner isn't sure it still exists).

Also: the owner has two Zeraora sv7 109/102, confirmed as one, and the still
pending Dragonair 148/165 photo proposes the English card.

Dry-run is the default; exact IDs keep it from touching anything else::

    (set -a; . ./.env.local; set +a; .venv/bin/python -m scripts.fix_photo_confirm_conflicts_20261106)
    (set -a; . ./.env.local; set +a; .venv/bin/python -m scripts.fix_photo_confirm_conflicts_20261106 --apply)
"""

from __future__ import annotations

import argparse
import uuid

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.listing_quality import generated_ebay_description, generated_ebay_title
from automation_control.models import (
    CardSet, CardVariant, CatalogItem, InventoryItem, InventoryStatus, ListingPhotoPair, ShopListing, TcgCard,
)
from automation_control.representative_backs import attach_to_pair, with_notice

MOVES = (  # (pair id, listing id, inventory id, English catalogue id, Japanese catalogue id)
    ("3fd7a5b0-4f32-4e58-ad47-1bf5082c49f9", 21, "566ddcbb-a177-48c9-8d9b-90f65aef1e88",
     "a3b67007-9eea-4ca1-b0c6-7c8d563f2ed1", "sv3pt5-168", "jp-sv2a-168"),
    ("3fd7a5b0-4f32-4e58-ad47-1bf5082c49f9", 29, "44d3144d-be03-43a3-9197-7944de9badbd",
     "b3bfd9dc-5b8f-4f91-a70e-84829733b238", "sv3pt5-176", "jp-sv2a-176"),
)
MEOWTH = ("d51f20d2-2e82-44fc-8681-c87033d1eca8", 3, "16508e98-13bd-4f52-9bd9-08f10247484a",
          "a1f5e9e0-6b9d-4f18-af9b-be86f9c884cc", "sv3pt5-52", "jp-sv2a-052")
ZERAORA = ("223cabf4-4a09-4f00-bf1a-1f8c5286f0e2", "c3fb97d4-05bf-4dac-aaa5-d0474285e9a2", 2)
DRAGONAIR = ("3fd7a5b0-4f32-4e58-ad47-1bf5082c49f9", 8, "sv3pt5-148", "jp-sv2a-148")
ACTOR = "claude-code (owner-approved 2026-11-06)"


def _pair(session, batch_id: str, position: int) -> ListingPhotoPair:
    pair = session.query(ListingPhotoPair).filter_by(batch_id=batch_id, position=position).one()
    return pair


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"refusing: {message}")


def _rebuild_listing(session, pair: ListingPhotoPair, listing: ShopListing, inventory: InventoryItem, catalog_id: str) -> None:
    """Same steps confirm_card() takes once it knows the card."""
    item, card = session.get(CatalogItem, catalog_id), session.get(TcgCard, catalog_id)
    _expect(item is not None and card is not None, f"catalogue card {catalog_id} missing")
    listing.catalog_item_id = catalog_id
    listing.title = f"{item.name} #{card.number or ''}"
    listing.ebay_title = listing.ebay_description = None
    attach_to_pair(pair, card.game, card.language, card.rarity)
    listing.ebay_photo_paths = [pair.front_image_path] + ([pair.back_image_path] if pair.back_image_path else [])
    card_set = session.get(CardSet, card.set_id) if card.set_id else None
    listing.ebay_title = generated_ebay_title(listing, item, card, card_set, inventory)
    listing.ebay_description = with_notice(
        generated_ebay_description(listing, item, card, card_set, inventory, has_own_photo=True), listing.ebay_photo_paths)
    pair.proposed_catalog_item_id = catalog_id
    pair.proposed_title, pair.proposed_description = listing.ebay_title, listing.ebay_description


_EVENTS: list[tuple[str, str, str, dict]] = []


def _event(session, resource_type: str, resource_id: str, action: str, details: dict) -> None:
    # Queued, not recorded: record_event() commits the session, which would
    # turn a dry run into a real one. (It did, on the first run of this script.)
    _EVENTS.append((resource_type, resource_id, action, details))


def run(apply: bool) -> int:
    with SessionLocal() as session:
        for batch_id, position, listing_id, inventory_id, english, japanese in MOVES:
            pair, listing, inventory = (_pair(session, batch_id, position), session.get(ShopListing, listing_id),
                                        session.get(InventoryItem, inventory_id))
            _expect(listing.catalog_item_id == english and inventory.catalog_item_id == english
                    and listing.inventory_item_id == inventory.id, f"{listing_id} no longer on {english}")
            _expect(session.query(InventoryItem).filter_by(catalog_item_id=japanese).count() == 0,
                    f"stock for {japanese} already exists")
            inventory.catalog_item_id, inventory.card_id = japanese, None
            _rebuild_listing(session, pair, listing, inventory, japanese)
            print(f"move {listing.title!r}: {english} -> {japanese}, back {pair.back_image_path.rsplit('/', 1)[-1]}")
            _event(session, "shop_listing", listing.id, "photo_confirm.correct_language",
                   {"from": english, "to": japanese, "inventory_item_id": inventory.id, "pair_id": pair.id})

        batch_id, position, listing_id, english_inventory_id, english, japanese = MEOWTH
        pair, listing, english_row = (_pair(session, batch_id, position), session.get(ShopListing, listing_id),
                                      session.get(InventoryItem, english_inventory_id))
        _expect(listing.inventory_item_id == english_row.id and english_row.catalog_item_id == english
                and english_row.variant == CardVariant.MASTERBALL_PATTERN, "Meowth records changed")
        _expect(session.query(InventoryItem).filter_by(catalog_item_id=japanese).count() == 0,
                f"stock for {japanese} already exists")
        japanese_row = InventoryItem(
            catalog_item_id=japanese, variant=CardVariant.MASTERBALL_PATTERN, condition="Near Mint", quantity=1,
            status=InventoryStatus.AVAILABLE, scan_image_path=pair.front_image_path,
            notes=f"Japanese copy from photo batch {batch_id}; split from English Meowth stock 2026-11-06.",
        )
        session.add(japanese_row)
        session.flush()
        english_row.variant = CardVariant.REVERSE_HOLO
        listing.inventory_item_id = japanese_row.id
        listing.quantity = 1
        _rebuild_listing(session, pair, listing, japanese_row, japanese)
        print(f"split Meowth: listing -> new {japanese} stock {japanese_row.id[:8]}; English {english_row.id[:8]} back to reverse holo")
        _event(session, "inventory_item", japanese_row.id, "photo_confirm.correct_language",
               {"from": english, "to": japanese, "listing_id": listing.id, "english_inventory_restored": english_row.id,
                "english_variant_restored": "reverse_holo"})

        inventory_id, listing_id, quantity = ZERAORA
        inventory, listing = session.get(InventoryItem, inventory_id), session.get(ShopListing, listing_id)
        _expect(inventory.quantity == 1 and listing.inventory_item_id == inventory.id, "Zeraora records changed")
        inventory.quantity = listing.quantity = quantity
        print(f"Zeraora: quantity 1 -> {quantity}")
        _event(session, "inventory_item", inventory.id, "inventory.quantity_corrected",
               {"from": 1, "to": quantity, "reason": "owner confirmed two physical copies", "listing_id": listing.id})

        batch_id, position, english, japanese = DRAGONAIR
        pair = _pair(session, batch_id, position)
        _expect(pair.proposed_catalog_item_id == english, "Dragonair proposal changed")
        pair.proposed_catalog_item_id = japanese
        print(f"Dragonair (pending): proposal {english} -> {japanese}")

        if apply:
            for resource_type, resource_id, action, details in _EVENTS:
                record_event(session, actor_type="user", actor_id=ACTOR, action=action, resource_type=resource_type,
                             resource_id=resource_id, outcome="success", correlation_id=str(uuid.uuid4()), details=details)
            session.commit()
            print("applied")
        else:
            session.rollback()
            print("dry run: nothing written (pass --apply)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    return run(parser.parse_args(argv).apply)


if __name__ == "__main__":
    raise SystemExit(main())
