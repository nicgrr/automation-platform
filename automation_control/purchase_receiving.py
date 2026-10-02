"""Canonical inventory receipt and cost allocation for purchase lots."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import (
    CardVariant, CatalogItem, InventoryItem, InventoryStatus,
    PurchaseLot, PurchaseLotItem, PurchaseLotStatus,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _receive_line(
    session: Session, lot: PurchaseLot, line: PurchaseLotItem,
    allocated_total: Decimal,
) -> InventoryItem:
    catalog = session.get(CatalogItem, line.catalog_item_id)
    if catalog is None:
        raise ValueError(f"{line.description} is not linked to a valid catalogue item.")
    unit_cost = _money(allocated_total / line.quantity)
    inventory = session.scalar(select(InventoryItem).where(
        InventoryItem.catalog_item_id == catalog.id,
        InventoryItem.variant == CardVariant.NORMAL,
        InventoryItem.condition == line.condition,
    ).order_by(InventoryItem.added_at).limit(1))
    note = f"Received from purchase lot {lot.id} on {datetime.now(UTC).date().isoformat()}"
    if lot.supplier_id:
        note += f" | supplier {lot.supplier_id}"
    if inventory is None:
        inventory = InventoryItem(
            id=str(uuid.uuid4()), card_id=None, catalog_item_id=catalog.id,
            variant=CardVariant.NORMAL, condition=line.condition,
            quantity=line.quantity, allocated_cost_basis=unit_cost,
            status=InventoryStatus.AVAILABLE, notes=note,
        )
        session.add(inventory)
    else:
        old_quantity = inventory.quantity
        old_cost = (inventory.allocated_cost_basis or ZERO) * old_quantity
        inventory.quantity += line.quantity
        inventory.allocated_cost_basis = _money((old_cost + allocated_total) / inventory.quantity)
        inventory.notes = (inventory.notes + "\n" if inventory.notes else "") + note
        if inventory.status in {
            InventoryStatus.SOLD, InventoryStatus.GIVEAWAY, InventoryStatus.RETURNED,
        }:
            inventory.status = InventoryStatus.AVAILABLE
    session.flush()
    line.inventory_item_id = inventory.id
    line.allocated_total_cost = allocated_total
    line.allocated_unit_cost = unit_cost
    return inventory


def receive_purchase_lot(session: Session, lot: PurchaseLot) -> list[InventoryItem]:
    """Receive a purchased lot once and proportionally allocate its total cost.

    Supplier quoted cost is the allocation weight when available; otherwise the
    evaluation-time market value is used. The final line absorbs cent rounding so
    allocated totals always equal the recorded purchase price exactly.
    """
    if lot.inventory_received_at is not None:
        return []
    lines = session.scalars(
        select(PurchaseLotItem)
        .where(PurchaseLotItem.purchase_lot_id == lot.id)
        .order_by(PurchaseLotItem.id)
    ).all()
    if not lines:
        raise ValueError("Add at least one catalogue-linked item before marking the lot purchased.")
    if any(not line.catalog_item_id for line in lines):
        raise ValueError("Every purchase-lot item must be linked to the catalogue before inventory receipt.")
    if any(line.quantity < 1 for line in lines):
        raise ValueError("Every purchase-lot quantity must be at least one.")
    purchase_total = lot.purchase_price
    if purchase_total is None or purchase_total <= 0:
        raise ValueError("Enter the final purchase price before receiving inventory.")
    weights = [
        (line.quoted_unit_cost if line.quoted_unit_cost is not None else line.market_value) * line.quantity
        for line in lines
    ]
    weight_total = sum(weights, ZERO)
    if weight_total <= 0:
        raise ValueError("Purchase-lot item values must total more than zero for cost allocation.")

    received_at = datetime.now(UTC)
    claim = session.execute(
        update(PurchaseLot)
        .where(PurchaseLot.id == lot.id, PurchaseLot.inventory_received_at.is_(None))
        .values(
            inventory_received_at=received_at,
            status=PurchaseLotStatus.PURCHASED,
            purchased_at=lot.purchased_at or received_at,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.rollback()
        return []

    received: list[InventoryItem] = []
    allocated = ZERO
    for index, (line, weight) in enumerate(zip(lines, weights)):
        line_total = (
            _money(purchase_total - allocated)
            if index == len(lines) - 1
            else _money(purchase_total * weight / weight_total)
        )
        allocated += line_total
        received.append(_receive_line(session, lot, line, line_total))
    session.expire(lot)
    session.flush()
    return received
