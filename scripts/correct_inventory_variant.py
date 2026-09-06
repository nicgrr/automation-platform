"""Re-file an inventory item under the printing its photo actually shows.

For use after a human confirms a card was scanned as one printing (usually
"normal", the default batch setting) but is actually holo or reverse holo --
`assess_foil` logs a weak hint at scan time but deliberately never acts on
it (see that module's docstring), so this is the human-confirmed follow-up,
the same role `correct_inventory_item.py` plays for a misidentified card.
Uses the same merge-or-create logic as a normal scan, so a correction that
lands on a printing already in inventory increments it instead of creating
a duplicate row.

    .venv/bin/python -m scripts.correct_inventory_variant \
        --item-id <inventory_item_id> --correct-variant reverse_holo \
        --reason "visually confirmed reverse-holo sheen, filed as normal at scan time"
"""

import argparse
import uuid
from pathlib import Path

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from automation_control.scan_ingest.commit import _image_quality, _store_scan_image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move a misfiled inventory item onto the printing its photo shows")
    parser.add_argument("--item-id", required=True, help="the InventoryItem.id currently filed under the wrong printing")
    parser.add_argument("--correct-variant", required=True, choices=[v.value for v in CardVariant])
    parser.add_argument("--reason", required=True, help="why -- goes into the audit log, not optional")
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)

    media_dir = Path(get_settings().scan_media_dir)
    correct_variant = CardVariant(args.correct_variant)

    with SessionLocal() as session:
        item = session.get(InventoryItem, args.item_id)
        if item is None:
            print(f"no inventory item with id {args.item_id!r}")
            return 1
        card = session.get(CatalogCard, item.card_id)
        if item.variant == correct_variant:
            print("item is already filed under that printing -- nothing to do")
            return 0

        original_variant = item.variant
        print(f"{card.name} #{card.number} ({card.set_id})  {original_variant.value}  ->  {correct_variant.value}")

        existing = session.scalar(
            select(InventoryItem).where(
                InventoryItem.card_id == item.card_id,
                InventoryItem.variant == correct_variant,
                InventoryItem.condition == item.condition,
            )
        )
        set_code = (card.card_set.code or card.set_id) if card.card_set else card.set_id

        if args.dry_run:
            if existing:
                print(f"would merge into existing holding (qty {existing.quantity} -> {existing.quantity + item.quantity}), then delete the misfiled row")
            else:
                print("would re-file this row under the correct printing and rename its photo")
            return 0

        if existing:
            existing.quantity += item.quantity
            if item.scan_image_path and (
                not existing.scan_image_path
                or _image_quality(Path(item.scan_image_path)) > _image_quality(Path(existing.scan_image_path))
            ):
                stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, card.number, correct_variant, item.condition)
                existing.scan_image_path = str(stored)
            session.delete(item)
            result_id = existing.id
        else:
            if item.scan_image_path:
                stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, card.number, correct_variant, item.condition)
                item.scan_image_path = str(stored)
            item.variant = correct_variant
            result_id = item.id

        session.commit()
        record_event(
            session, actor_type="user", actor_id="quality-audit", action="inventory.correct_variant",
            resource_type="inventory_item", resource_id=result_id, outcome="success",
            correlation_id=str(uuid.uuid4()),
            details={
                "card": f"{card.set_id}#{card.number} {card.name}",
                "from_variant": original_variant.value,
                "to_variant": correct_variant.value,
                "reason": args.reason,
            },
        )
        print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
