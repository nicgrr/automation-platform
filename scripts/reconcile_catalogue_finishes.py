"""Correct inventory finishes that are facts of the card catalogue.

This deliberately excludes image-classifier guesses.  It only acts when
``catalogue_variant`` can establish one printing from an exact marketplace
subtype or a game rarity/parallel rule.  The default is a read-only report;
``--apply`` performs audited updates.

One Piece and Dragon Ball are the default scope.  Pokemon can be included
explicitly, but is kept out of the operational cleanup requested for the
Bandai queues.
"""

import argparse
import uuid
from pathlib import Path

from sqlalchemy import delete, select, update

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import (
    AuditEvent,
    CapturePromotion,
    CatalogCard,
    FoilLabel,
    InventoryItem,
    PhotoAuditDismissal,
    PurchaseLotItem,
    RetailerPurchase,
    SaleItem,
    ShopListing,
    WhatnotShowItem,
)
from automation_control.scan_ingest.commit import _image_quality, _store_scan_image
from automation_control.scan_ingest.foil import catalogue_variant


_BANDAI_PREFIXES = ("op-", "opjp-", "dbfw-", "dbs-")


def _audit(item_id: str, card: CatalogCard, old_variant, new_variant, reason: str) -> AuditEvent:
    return AuditEvent(
        actor_type="system",
        actor_id="catalogue-finish-reconciler",
        action="inventory.correct_variant",
        resource_type="inventory_item",
        resource_id=item_id,
        outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={
            "card": f"{card.set_id}#{card.number} {card.name}",
            "from_variant": old_variant.value,
            "to_variant": new_variant.value,
            "reason": reason,
        },
    )


def _repoint_merge_dependencies(session, source_id: str, target_id: str) -> None:
    """Preserve live workflow links when two holding rows become one.

    Historical sale/show links and foil labels intentionally follow their
    declared ON DELETE SET NULL behavior.  Live listings and ingest/purchase
    provenance must follow the surviving stock row.  A photo dismissal is
    specific to the disappearing photo/row and is discarded.
    """
    for model in (ShopListing, CapturePromotion, PurchaseLotItem, RetailerPurchase):
        session.execute(
            update(model)
            .where(model.inventory_item_id == source_id)
            .values(inventory_item_id=target_id)
        )
    session.execute(
        delete(PhotoAuditDismissal).where(PhotoAuditDismissal.inventory_item_id == source_id)
    )
    # Be explicit instead of relying on database-specific ON DELETE handling.
    for model in (FoilLabel, SaleItem, WhatnotShowItem):
        session.execute(
            update(model)
            .where(model.inventory_item_id == source_id)
            .values(inventory_item_id=None)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the reported corrections")
    parser.add_argument("--include-pokemon", action="store_true", help="also reconcile non-Bandai exact-printing facts")
    args = parser.parse_args(argv)
    media_dir = Path(get_settings().scan_media_dir)

    changed = merged = skipped = quantity = 0
    with SessionLocal() as session:
        rows = session.execute(
            select(InventoryItem, CatalogCard)
            .join(CatalogCard, InventoryItem.catalog_item_id == CatalogCard.id)
            .order_by(CatalogCard.set_id, CatalogCard.number, InventoryItem.id)
        ).all()
        for item, card in rows:
            if item.quantity <= 0:
                continue
            if not args.include_pokemon and not card.set_id.startswith(_BANDAI_PREFIXES):
                continue
            target_variant, reason = catalogue_variant(card)
            if target_variant is None or target_variant == item.variant:
                continue

            existing = session.scalar(
                select(InventoryItem).where(
                    InventoryItem.catalog_item_id == item.catalog_item_id,
                    InventoryItem.variant == target_variant,
                    InventoryItem.condition == item.condition,
                    InventoryItem.id != item.id,
                )
            )
            mode = "merge" if existing else "update"
            print(
                f"{mode:6} {card.set_id} {card.number} {card.name}: "
                f"{item.variant.value} -> {target_variant.value} x{item.quantity} ({reason})"
            )

            if existing and any((
                existing.status != item.status,
                existing.storage_location_id != item.storage_location_id,
                existing.allocated_cost_basis != item.allocated_cost_basis,
                existing.grading_company != item.grading_company,
                existing.certification_number != item.certification_number,
            )):
                print("       SKIP: target holding has different status/location/cost/grading metadata")
                skipped += 1
                continue

            if not args.apply:
                changed += 1
                merged += bool(existing)
                quantity += item.quantity
                continue

            original_variant = item.variant
            set_code = (card.card_set.code or card.set_id) if card.card_set else card.set_id
            if existing:
                existing.quantity += item.quantity
                if item.scan_image_path and (
                    not existing.scan_image_path
                    or _image_quality(Path(item.scan_image_path)) > _image_quality(Path(existing.scan_image_path))
                ):
                    stored = _store_scan_image(
                        Path(item.scan_image_path), media_dir, set_code,
                        card.number, target_variant, item.condition,
                    )
                    existing.scan_image_path = str(stored)
                _repoint_merge_dependencies(session, item.id, existing.id)
                result_id = existing.id
                session.delete(item)
                merged += 1
            else:
                if item.scan_image_path:
                    stored = _store_scan_image(
                        Path(item.scan_image_path), media_dir, set_code,
                        card.number, target_variant, item.condition,
                    )
                    item.scan_image_path = str(stored)
                item.variant = target_variant
                result_id = item.id

            session.add(_audit(result_id, card, original_variant, target_variant, reason or "catalogue printing fact"))
            session.flush()
            changed += 1
            quantity += item.quantity

        if args.apply:
            session.commit()

    verb = "changed" if args.apply else "would change"
    print(f"{verb}: {changed} rows / {quantity} cards; merges: {merged}; skipped: {skipped}")
    return 0 if skipped == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
