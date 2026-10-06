"""Deduplicated priority queue for pricing refresh work."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PricingQueueStatus, PricingRefreshQueue
from .identity import PricingIdentity


def utcnow() -> datetime:
    return datetime.now(UTC)


def enqueue_refresh(
    session: Session, *, identity: PricingIdentity, reason: str,
    priority: int = 100, manually_requested: bool = False,
    available_at: datetime | None = None,
) -> PricingRefreshQueue:
    if not reason.strip():
        raise ValueError("refresh reason is required")
    if priority < 0:
        raise ValueError("priority cannot be negative")
    ready_at = available_at or utcnow()
    row = session.scalar(select(PricingRefreshQueue).where(
        PricingRefreshQueue.catalog_item_id == identity.catalog_item_id,
        PricingRefreshQueue.identity_key == identity.key,
    ))
    if row is None:
        row = PricingRefreshQueue(
            catalog_item_id=identity.catalog_item_id,
            identity_key=identity.key,
            identity=identity.as_dict(),
            reason=reason.strip(),
            priority=priority,
            requested_manually=manually_requested,
            available_at=ready_at,
        )
        session.add(row)
    else:
        row.identity = identity.as_dict()
        row.reason = reason.strip()
        row.priority = min(row.priority, priority)
        row.requested_manually = row.requested_manually or manually_requested
        row.available_at = min(_aware(row.available_at), _aware(ready_at))
        row.status = PricingQueueStatus.PENDING
        row.last_error = None
        row.updated_at = utcnow()
    session.flush()
    return row


def claim_next_refresh(
    session: Session, *, now: datetime | None = None,
) -> PricingRefreshQueue | None:
    checked_at = now or utcnow()
    row = session.scalar(
        select(PricingRefreshQueue)
        .where(
            PricingRefreshQueue.status == PricingQueueStatus.PENDING,
            PricingRefreshQueue.available_at <= checked_at,
        )
        .order_by(
            PricingRefreshQueue.priority.asc(),
            PricingRefreshQueue.requested_manually.desc(),
            PricingRefreshQueue.created_at.asc(),
        )
        .limit(1)
    )
    if row is None:
        return None
    row.status = PricingQueueStatus.RUNNING
    row.attempts += 1
    row.updated_at = checked_at
    session.flush()
    return row


def finish_refresh(
    row: PricingRefreshQueue, *, succeeded: bool, error: str | None = None,
) -> None:
    row.status = PricingQueueStatus.SUCCEEDED if succeeded else PricingQueueStatus.FAILED
    row.last_error = None if succeeded else (error or "pricing refresh failed")[:512]
    row.updated_at = utcnow()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
