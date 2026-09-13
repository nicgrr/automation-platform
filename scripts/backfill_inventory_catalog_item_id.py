"""Fill in `catalog_item_id` on inventory rows that only carry `card_id`.

InventoryItem has both columns. `card_id` is the older one, pointing at a
CatalogCard; `catalog_item_id` points at the CatalogItem that replaced it
as the thing everything else keys on. Rows committed before that change
have only the old column, and nothing has ever filled in the new one.

That makes them invisible to the two places that matter most, both of
which filter on `catalog_item_id IS NOT NULL`:

  * `scripts/import_ebay_prices.py` -- so they never get a market price.
    163 held cards had no `ebay_active_low` row for exactly this reason,
    and no number of runs would have fixed it.
  * `scripts/list_for_sale.py` -- so they can never be listed at all.

Safe because the two columns are not merely similar but identical wherever
both are set: zero rows disagree, and every orphaned `card_id` resolves to
a real CatalogItem. The script re-checks both facts at run time and
refuses rather than guessing if either stops being true.

    .venv/bin/python -m scripts.backfill_inventory_catalog_item_id --dry-run
    .venv/bin/python -m scripts.backfill_inventory_catalog_item_id
"""

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.models import CatalogItem, InventoryItem

JOURNAL_DIR = Path("backups")


def orphaned(session) -> list[InventoryItem]:
    return list(session.scalars(
        select(InventoryItem).where(
            InventoryItem.card_id.isnot(None),
            InventoryItem.catalog_item_id.is_(None),
        )
    ))


def _guard(session, rows: list[InventoryItem]) -> None:
    """Refuse if the assumption this rests on has stopped holding."""
    disagreeing = session.scalars(
        select(InventoryItem).where(
            InventoryItem.card_id.isnot(None),
            InventoryItem.catalog_item_id.isnot(None),
            InventoryItem.card_id != InventoryItem.catalog_item_id,
        )
    ).first()
    if disagreeing is not None:
        raise SystemExit(
            "refusing: card_id and catalog_item_id disagree on at least one row "
            f"({disagreeing.id}), so copying one onto the other is not safe"
        )
    known = set(session.scalars(select(CatalogItem.id).where(
        CatalogItem.id.in_([row.card_id for row in rows]))))
    missing = sorted({row.card_id for row in rows} - known)
    if missing:
        raise SystemExit(
            f"refusing: {len(missing)} card_id(s) resolve to no CatalogItem, "
            f"e.g. {missing[:3]}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--user", default="cli")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        rows = orphaned(session)
        if not rows:
            print("Every inventory row already has a catalog_item_id. Nothing to do.")
            return 0
        _guard(session, rows)

        copies = sum(row.quantity or 0 for row in rows)
        print(f"{len(rows)} inventory row(s), {copies} copies, would gain a catalog_item_id.")
        for row in rows[:10]:
            print(f"   {row.id[:8]}  card_id={row.card_id}  qty={row.quantity}")
        if len(rows) > 10:
            print(f"   ... and {len(rows) - 10} more")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        path = JOURNAL_DIR / f"inventory-catalog-item-id-{datetime.now(UTC).strftime('%Y-%m-%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(
            {"filled_at": datetime.now(UTC).isoformat(),
             "items": [{"id": r.id, "card_id": r.card_id, "quantity": r.quantity} for r in rows]},
            indent=2))

        for row in rows:
            row.catalog_item_id = row.card_id
        record_event(session, actor_type="user", actor_id=args.user,
                     action="inventory.backfill_catalog_item_id",
                     resource_type="inventory_item", resource_id=None, outcome="success",
                     correlation_id=str(uuid.uuid4()),
                     details={"rows": len(rows), "copies": copies, "journal": str(path)})
        session.commit()
        print(f"\nFilled {len(rows)} row(s). Journal: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
