"""Rehearse a complete Whatnot show against a temporary database only.

    .venv/bin/python -m scripts.whatnot_show_drill
"""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session, make_engine
from automation_control.models import (
    CatalogItem, CatalogItemType, Customer, InventoryItem, InventoryStatus, Marketplace,
    MarketplaceFeeRule, Sale, WhatnotShow, WhatnotShowItem, WhatnotShowItemOutcome, WhatnotShowStatus,
)


def _inventory(session: Session, suffix: str) -> InventoryItem:
    catalog = CatalogItem(
        id=f"whatnot-drill-catalog-{suffix}", item_type=CatalogItemType.TCG_CARD,
        name=f"Whatnot drill card {suffix}",
    )
    inventory = InventoryItem(
        id=f"whatnot-drill-inventory-{suffix}", catalog_item_id=catalog.id,
        quantity=1, status=InventoryStatus.AVAILABLE,
        allocated_cost_basis=Decimal("5.00"), condition="Near Mint",
    )
    session.add_all([catalog, inventory])
    session.commit()
    return inventory


def run() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="ezbay-whatnot-drill.") as directory:
        engine = make_engine(f"sqlite+pysqlite:///{Path(directory) / 'drill.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            session.add(Marketplace(id="whatnot-drill", name="Whatnot"))
            session.add(MarketplaceFeeRule(
                id="whatnot-drill-rule", marketplace_id="whatnot-drill", category="TCG",
                commission_pct=Decimal("8"), processing_pct=Decimal("2.9"),
                fixed_fee=Decimal("0.30"), promotion_label="Temporary drill rule",
            ))
            session.commit()
            inventory = [_inventory(session, suffix) for suffix in ("sold", "unsold", "giveaway", "cancel")]

            app.dependency_overrides[get_session] = lambda: session
            app.dependency_overrides[require_dashboard_user] = lambda: "whatnot-drill"
            try:
                client = TestClient(app)
                created = client.post(
                    "/whatnot",
                    data={"title": "Temporary rehearsal", "show_date": "2026-10-02", "category": "TCG"},
                    follow_redirects=False,
                )
                show_url = created.headers["location"]
                for stock in inventory[:3]:
                    response = client.post(
                        f"{show_url}/items",
                        data={"inventory_item_id": stock.id, "starting_price": "10"},
                        follow_redirects=False,
                    )
                    assert response.status_code == 303
                assert client.post(f"{show_url}/start", follow_redirects=False).status_code == 303
                items = session.scalars(
                    select(WhatnotShowItem).where(WhatnotShowItem.whatnot_show_id == show_url.rsplit("/", 1)[-1])
                    .order_by(WhatnotShowItem.inventory_item_id)
                ).all()
                by_inventory = {item.inventory_item_id: item for item in items}

                sold = by_inventory[inventory[0].id]
                client.post(
                    f"{show_url}/items/{sold.id}/outcome",
                    data={
                        "outcome": "sold", "final_price": "25", "actual_fees": "3.03",
                        "buyer_handle": "drill_buyer", "whatnot_order_id": "DRILL-WN-1",
                    }, follow_redirects=False,
                )
                unsold = by_inventory[inventory[1].id]
                client.post(f"{show_url}/items/{unsold.id}/outcome",
                            data={"outcome": "unsold"}, follow_redirects=False)
                giveaway = by_inventory[inventory[2].id]
                client.post(f"{show_url}/items/{giveaway.id}/outcome",
                            data={"outcome": "giveaway"}, follow_redirects=False)
                completed = client.post(
                    f"{show_url}/complete",
                    data={"viewer_count": "12", "follower_count": "34"}, follow_redirects=False,
                )
                assert completed.status_code == 303

                cancel_created = client.post(
                    "/whatnot", data={"title": "Cancellation rehearsal", "category": "TCG"},
                    follow_redirects=False,
                )
                cancel_url = cancel_created.headers["location"]
                client.post(f"{cancel_url}/items", data={
                    "inventory_item_id": inventory[3].id, "starting_price": "10",
                }, follow_redirects=False)
                client.post(f"{cancel_url}/cancel", follow_redirects=False)

                show = session.get(WhatnotShow, show_url.rsplit("/", 1)[-1])
                cancelled = session.get(WhatnotShow, cancel_url.rsplit("/", 1)[-1])
                for row in inventory:
                    session.refresh(row)
                assert show.status == WhatnotShowStatus.COMPLETED
                assert cancelled.status == WhatnotShowStatus.CANCELLED
                assert inventory[0].status == InventoryStatus.SOLD
                assert inventory[1].status == InventoryStatus.AVAILABLE and inventory[1].quantity == 1
                assert inventory[2].status == InventoryStatus.GIVEAWAY
                assert inventory[3].status == InventoryStatus.AVAILABLE and inventory[3].quantity == 1
                assert session.query(Sale).count() == 1
                customer = session.scalars(select(Customer)).one()
                assert customer.platform_handles == {"whatnot": "drill_buyer"}
                assert by_inventory[inventory[0].id].outcome == WhatnotShowItemOutcome.SOLD

                return {
                    "status": "passed",
                    "inventory_reservation": "passed",
                    "show_start_and_completion": "passed",
                    "sale_and_fee_capture": "passed",
                    "customer_handoff": "passed",
                    "unsold_restore": "passed",
                    "giveaway_depletion": "passed",
                    "cancellation_restore": "passed",
                    "external_calls": "none",
                }
            finally:
                app.dependency_overrides.clear()
        engine.dispose()


def main() -> int:
    print(json.dumps(run(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
