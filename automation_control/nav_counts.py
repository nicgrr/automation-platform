"""How much work is waiting in each review queue, in one place.

The dashboard and the nav drawer both want these numbers and used to
disagree about them -- the dashboard counted four queues inline in its own
handler, the drawer counted none at all, and foil/photo-audit had no
count anywhere despite being real queues with real backlogs. One helper
now answers for all six.

Deliberately cheap: every queue here is either a plain SQL count, a
directory listing, or a *read* of a cache file another module already
maintains. `foil_review` and `inventory_audit_review` both compute their
candidates by hashing every inventory photo (~30s) and cache the result to
disk; this module reads that cache and reports `None` when it's missing
rather than triggering the recompute. A count is a convenience -- it must
never be the thing that makes a page load slowly, and a nav badge is not
worth a 30-second stall on whatever page happens to be rendered first
after a rescan.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CapturedAccessory,
    CapturedCard,
    CapturedCollectible,
    CardCaptureStatus,
    InventoryItem,
    ListingBuildStatus,
    PendingListing,
    PhotoAuditDismissal,
    PricingStatus,
    FoilLabel,
)

router = APIRouter(tags=["nav"])


def _scan_crops(settings) -> int:
    """The bulk-scan queue is files on disk, not a table -- crops the
    scanner set aside rather than committing."""
    directory = Path(settings.scan_media_dir) / "needs_review"
    return len(list(directory.glob("*.jpg"))) if directory.exists() else 0


def _read_cache(settings, name: str) -> list[dict] | None:
    path = Path(settings.scan_media_dir) / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _foil_pending(session: Session, settings) -> int | None:
    """Mirrors foil_review._pending, but cache-read-only."""
    candidates = _read_cache(settings, "foil_candidates_cache.json")
    if candidates is None:
        return None
    labelled = set(session.scalars(select(FoilLabel.inventory_item_id)))
    return sum(1 for c in candidates if c["item_id"] not in labelled)


def _photo_audit_pending(session: Session, settings) -> int | None:
    """Mirrors inventory_audit_review._pending, but cache-read-only. The
    same live filtering applies: a dismissal, a delete or a stock-photo
    swap drops an item out of the queue without a rescan, so counting the
    raw cache would overstate the backlog."""
    mismatches = _read_cache(settings, "inventory_audit_cache.json")
    if mismatches is None:
        return None
    dismissed = set(session.scalars(select(PhotoAuditDismissal.inventory_item_id)))
    pending = 0
    for m in mismatches:
        if m["item_id"] in dismissed:
            continue
        item = session.get(InventoryItem, m["item_id"])
        if item is None or item.scan_image_path != m.get("_scan_image_path"):
            continue
        pending += 1
    return pending


def queue_counts(session: Session, settings) -> dict[str, int | None]:
    """Keyed by the route each count belongs to, so callers can look a
    count up by href without keeping a second mapping in sync."""
    counts = {
        "/review": _scan_crops(settings),
        "/foil-review": _foil_pending(session, settings),
        "/inventory-audit": _photo_audit_pending(session, settings),
        "/collectibles/review": session.scalar(
            select(func.count())
            .select_from(CapturedCollectible)
            .where(CapturedCollectible.status == CardCaptureStatus.PENDING_REVIEW)
        ),
        "/accessories/review": session.scalar(
            select(func.count())
            .select_from(CapturedAccessory)
            .where(CapturedAccessory.status == CardCaptureStatus.PENDING_REVIEW)
        ),
        "/cards/review": session.scalar(
            select(func.count())
            .select_from(CapturedCard)
            .where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW)
        ),
        "/cards/pricing": session.scalar(
            select(func.count())
            .select_from(CapturedCard)
            .where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW)
        ),
        "/listings/review": session.scalar(
            select(func.count())
            .select_from(PendingListing)
            .where(PendingListing.status == ListingBuildStatus.DRAFT)
        ),
    }
    review_routes = (
        "/review", "/foil-review", "/inventory-audit", "/collectibles/review",
        "/accessories/review", "/cards/review", "/cards/pricing",
    )
    known_total = sum(counts[href] or 0 for href in review_routes)
    # Unknown cache-backed queues must not erase known work. When the known
    # queues are clear but one cache has never been built, None remains the
    # honest aggregate (the drawer simply shows no badge, rather than a false
    # zero). The Review Centre itself explains which scan is still required.
    counts["/review-centre"] = (
        known_total if known_total
        else None if any(counts[href] is None for href in review_routes)
        else 0
    )
    return counts


@router.get("/nav/counts")
def nav_counts(
    request: Request,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    """Fetched by the nav drawer the first time it's opened, not on every
    page render -- the drawer is closed by default, so most page loads
    never need these numbers at all."""
    return {"counts": queue_counts(session, request.app.state.settings)}
