"""Re-file an inventory item under the card its photo actually shows.

For use after `scripts.audit_inventory_photos` (or a manual look) confirms a
specific item is filed wrong -- this never guesses which card is correct
itself, only moves an item once a human (or an AI reading the photo) has
said where it belongs. Safe to run against a live database: uses the same
merge-or-create logic `commit_card` already uses for a normal scan, so a
correction that lands on a card already in inventory increments it (keeping
the better of the two photos) instead of creating a duplicate row.

    .venv/bin/python -m scripts.correct_inventory_item \
        --item-id <inventory_item_id> --correct-set-id sv3pt5 --correct-number 79 \
        --reason "photo shows Slowpoke, not Graveler -- Expedition Base Set and 151 both have a #79"
"""

import argparse
import uuid
from pathlib import Path

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CatalogCard, InventoryItem
from automation_control.scan_ingest.commit import _image_quality, _store_scan_image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move a misfiled inventory item onto the card its photo shows")
    parser.add_argument("--item-id", required=True, help="the InventoryItem.id currently filed wrong")
    parser.add_argument("--correct-set-id", required=True, help="the CardSet.id the photo actually belongs to")
    parser.add_argument("--correct-number", required=True, help="the card number within that set")
    parser.add_argument("--reason", required=True, help="why -- goes into the audit log, not optional")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    media_dir = Path(get_settings().scan_media_dir)

    with SessionLocal() as session:
        item = session.get(InventoryItem, args.item_id)
        if item is None:
            print(f"no inventory item with id {args.item_id!r}")
            return 1
        wrong_card = session.get(CatalogCard, item.card_id)

        correct_card = session.scalar(
            select(CatalogCard).where(CatalogCard.set_id == args.correct_set_id, CatalogCard.number == args.correct_number)
        )
        if correct_card is None:
            print(f"no catalog card {args.correct_set_id}#{args.correct_number} -- is that set cached?")
            return 1
        if correct_card.id == item.card_id:
            print("item is already filed under that card -- nothing to do")
            return 0

        print(f"{wrong_card.card_set.name if wrong_card.card_set else wrong_card.set_id} #{wrong_card.number} {wrong_card.name}"
              f"  ->  {correct_card.card_set.name if correct_card.card_set else correct_card.set_id} #{correct_card.number} {correct_card.name}")

        existing = session.scalar(
            select(InventoryItem).where(
                InventoryItem.card_id == correct_card.id,
                InventoryItem.variant == item.variant,
                InventoryItem.condition == item.condition,
            )
        )
        set_code = (correct_card.card_set.code or correct_card.set_id) if correct_card.card_set else correct_card.set_id

        if args.dry_run:
            if existing:
                print(f"would merge into existing holding (qty {existing.quantity} -> {existing.quantity + item.quantity}), then delete the misfiled row")
            else:
                print("would re-file this row under the correct card and rename its photo")
            return 0

        if existing:
            existing.quantity += item.quantity
            if item.scan_image_path and (
                not existing.scan_image_path
                or _image_quality(Path(item.scan_image_path)) > _image_quality(Path(existing.scan_image_path))
            ):
                stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, correct_card.number, item.variant, item.condition)
                existing.scan_image_path = str(stored)
            session.delete(item)
            result_id = existing.id
        else:
            if item.scan_image_path:
                stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, correct_card.number, item.variant, item.condition)
                item.scan_image_path = str(stored)
            item.card_id = correct_card.id
            result_id = item.id

        session.commit()
        record_event(
            session, actor_type="user", actor_id="quality-audit", action="inventory.correct",
            resource_type="inventory_item", resource_id=result_id, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={
                "from": f"{wrong_card.set_id}#{wrong_card.number} {wrong_card.name}",
                "to": f"{correct_card.set_id}#{correct_card.number} {correct_card.name}",
                "reason": args.reason,
            },
        )
        print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
