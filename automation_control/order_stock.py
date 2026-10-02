"""Crash-safe stock transitions for NL Vault orders.

Checkout reserves stock before a shopper leaves for Stripe. Payment merely
finalises that reservation; it never subtracts a second time. Cancellation and
an explicitly restocked refund use the same guarded restoration path, so a
double click or retried request cannot add stock twice.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import (
    BulkLot, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus,
    ShopOrder, ShopOrderItem, ShopOrderStatus,
)


class StockUnavailable(Exception):
    """Raised when the database can no longer satisfy an order exactly."""


def _items(session: Session, order: ShopOrder) -> list[ShopOrderItem]:
    return session.scalars(
        select(ShopOrderItem).where(ShopOrderItem.order_id == order.id)
    ).all()


def reserve_order_stock(session: Session, order: ShopOrder) -> list[tuple[str, int]]:
    """Atomically remove an order's units from sale.

    Each conditional UPDATE includes ``quantity >= requested``. Concurrent
    checkouts can both read the last item, but only one can win the write; if
    any line fails, the caller rolls the whole transaction back.
    """
    if order.stock_reserved_at is not None:
        return []

    changed_ids: list[str] = []
    for item in _items(session, order):
        if item.unit_price < 0:
            continue
        quantity = item.quantity
        if item.listing_id:
            listing = session.get(ShopListing, item.listing_id)
            if listing is None:
                raise StockUnavailable(f"{item.title} is no longer available")
            result = session.execute(
                update(ShopListing)
                .where(
                    ShopListing.id == item.listing_id,
                    ShopListing.status == ShopListingStatus.ACTIVE,
                    ShopListing.quantity >= quantity,
                )
                .values(quantity=ShopListing.quantity - quantity)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise StockUnavailable(f"There is not enough stock left for {item.title}")
            session.expire(listing)
            if listing.quantity == 0:
                listing.status = ShopListingStatus.SOLD_OUT
            changed_ids.append(item.listing_id)

            if listing.inventory_item_id:
                inventory_result = session.execute(
                    update(InventoryItem)
                    .where(
                        InventoryItem.id == listing.inventory_item_id,
                        InventoryItem.quantity >= quantity,
                    )
                    .values(quantity=InventoryItem.quantity - quantity)
                    .execution_options(synchronize_session=False)
                )
                if inventory_result.rowcount != 1:
                    raise StockUnavailable(f"Physical stock is unavailable for {item.title}")
                inventory = session.get(InventoryItem, listing.inventory_item_id)
                session.expire(inventory)
                if inventory.quantity == 0:
                    inventory.status = InventoryStatus.RESERVED
        elif item.bulk_lot_id:
            result = session.execute(
                update(BulkLot)
                .where(
                    BulkLot.id == item.bulk_lot_id,
                    BulkLot.status == ShopListingStatus.ACTIVE,
                    BulkLot.quantity >= quantity,
                )
                .values(quantity=BulkLot.quantity - quantity)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise StockUnavailable(f"There is not enough stock left for {item.title}")
            lot = session.get(BulkLot, item.bulk_lot_id)
            session.expire(lot)
            if lot.quantity == 0:
                lot.status = ShopListingStatus.SOLD_OUT

    order.stock_reserved_at = datetime.now(UTC)
    session.flush()
    return [
        (listing_id, session.get(ShopListing, listing_id).quantity)
        for listing_id in changed_ids
    ]


def finalise_order_stock(session: Session, order: ShopOrder) -> None:
    """Turn zero-quantity physical reservations into completed sales."""
    for item in _items(session, order):
        if not item.listing_id:
            continue
        listing = session.get(ShopListing, item.listing_id)
        if listing is None or not listing.inventory_item_id:
            continue
        inventory = session.get(InventoryItem, listing.inventory_item_id)
        if inventory is not None and inventory.quantity == 0 and inventory.status == InventoryStatus.RESERVED:
            inventory.status = InventoryStatus.SOLD


def mark_order_paid(
    session: Session, order: ShopOrder, stripe_payload: dict | None = None,
) -> tuple[bool, list[tuple[str, int]]]:
    """Atomically claim the pending-to-paid transition and finalise stock."""
    if order.stock_reserved_at is None or order.stock_restored_at is not None:
        raise StockUnavailable("this order no longer has a valid stock reservation")

    values = {
        "status": ShopOrderStatus.PAID,
        "paid_at": datetime.now(UTC),
    }
    if stripe_payload:
        if stripe_payload.get("id"):
            values["stripe_session_id"] = stripe_payload["id"]
        if stripe_payload.get("payment_intent"):
            values["stripe_payment_intent"] = stripe_payload["payment_intent"]
    claim = session.execute(
        update(ShopOrder)
        .where(
            ShopOrder.id == order.id,
            ShopOrder.status == ShopOrderStatus.PENDING_PAYMENT,
            ShopOrder.stock_reserved_at.is_not(None),
            ShopOrder.stock_restored_at.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        return False, []
    session.expire(order)
    finalise_order_stock(session, order)
    session.flush()
    changed = []
    for item in _items(session, order):
        if item.listing_id:
            listing = session.get(ShopListing, item.listing_id)
            if listing is not None:
                changed.append((listing.id, listing.quantity))
    return True, changed


def _restore_items(session: Session, order: ShopOrder) -> list[tuple[str, int]]:
    changed: list[tuple[str, int]] = []
    for item in _items(session, order):
        if item.unit_price < 0:
            continue
        if item.listing_id:
            listing = session.get(ShopListing, item.listing_id)
            if listing is None:
                continue
            listing.quantity += item.quantity
            if listing.status == ShopListingStatus.SOLD_OUT:
                listing.status = ShopListingStatus.ACTIVE
            if listing.inventory_item_id:
                inventory = session.get(InventoryItem, listing.inventory_item_id)
                if inventory is not None:
                    inventory.quantity += item.quantity
                    if inventory.status in (InventoryStatus.RESERVED, InventoryStatus.SOLD):
                        inventory.status = InventoryStatus.LISTED
            changed.append((listing.id, listing.quantity))
        elif item.bulk_lot_id:
            lot = session.get(BulkLot, item.bulk_lot_id)
            if lot is not None:
                lot.quantity += item.quantity
                if lot.status == ShopListingStatus.SOLD_OUT:
                    lot.status = ShopListingStatus.ACTIVE
    return changed


def cancel_pending_order(session: Session, order: ShopOrder) -> tuple[bool, list[tuple[str, int]]]:
    """Atomically let cancellation win only while the order is unpaid."""
    if order.stock_reserved_at is None or order.stock_restored_at is not None:
        return False, []
    now = datetime.now(UTC)
    claim = session.execute(
        update(ShopOrder)
        .where(
            ShopOrder.id == order.id,
            ShopOrder.status == ShopOrderStatus.PENDING_PAYMENT,
            ShopOrder.stock_reserved_at.is_not(None),
            ShopOrder.stock_restored_at.is_(None),
        )
        .values(
            status=ShopOrderStatus.CANCELLED,
            cancelled_at=now,
            stock_restored_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        return False, []
    changed = _restore_items(session, order)
    session.expire(order)
    session.flush()
    return True, changed


def restore_order_stock(session: Session, order: ShopOrder) -> list[tuple[str, int]]:
    """Restore a reservation exactly once and return eBay sync quantities."""
    if order.stock_reserved_at is None or order.stock_restored_at is not None:
        return []

    restored_at = datetime.now(UTC)
    claim = session.execute(
        update(ShopOrder)
        .where(ShopOrder.id == order.id, ShopOrder.stock_restored_at.is_(None))
        .values(stock_restored_at=restored_at)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        return []

    changed = _restore_items(session, order)
    order.stock_restored_at = restored_at
    session.flush()
    return changed
