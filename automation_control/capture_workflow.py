"""Shared mechanics for the three AI-assisted capture/review pipelines.

Cards, collectibles and accessories intentionally keep separate extraction,
form fields and catalogue-creation rules.  This module owns only the mechanics
that must behave identically: saving upload bytes, recording batch progress,
rendering batch status, and walking a pending-review queue.
"""

import mimetypes
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, TypeVar

from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .models import CaptureBatch, CaptureBatchStatus, CardCaptureStatus
from .ui import brand_header, page

CapturedT = TypeVar("CapturedT")
SavedT = TypeVar("SavedT")


def save_capture_bytes(
    data: bytes, content_type: str | None, directory: Path, *, stem: str,
) -> Path:
    """Persist one upload with a MIME-derived extension.

    Orientation/cropping remain pipeline-specific because cards can have a
    front/back pair while collectibles and accessories remove backgrounds.
    """
    directory.mkdir(parents=True, exist_ok=True)
    extension = mimetypes.guess_extension(content_type or "") or ".jpg"
    path = directory / f"{stem}{extension}"
    path.write_bytes(data)
    return path


def create_capture_batch(
    session: Session, *, saved_count: int, save_failures: dict[str, str], user: str,
) -> CaptureBatch:
    batch = CaptureBatch(
        total_photos=saved_count + len(save_failures),
        processed_photos=len(save_failures),
        failed_photos=len(save_failures),
        failure_details=save_failures,
        started_by=user,
    )
    session.add(batch)
    session.commit()
    return batch


def run_capture_batch(
    batch_id: str,
    saved_items: Iterable[SavedT],
    settings,
    user: str,
    *,
    session_factory,
    process_one: Callable[[SavedT, Any, Session, str], list],
    item_label: Callable[[SavedT], str],
    audit_action: str,
    resource_type: str,
) -> None:
    """Process every saved upload independently and always settle the batch."""
    with session_factory() as session:
        batch = session.get(CaptureBatch, batch_id)
        if batch is None:
            return
        failure_details = dict(batch.failure_details or {})
        for saved in saved_items:
            label = item_label(saved)
            try:
                records = process_one(saved, settings, session, user)
                batch.cards_created += len(records)
            except Exception as exc:
                batch.failed_photos += 1
                failure_details[label] = str(exc)
                record_event(
                    session,
                    actor_type="user",
                    actor_id=user,
                    action=audit_action,
                    resource_type=resource_type,
                    resource_id=None,
                    outcome="failed",
                    correlation_id=str(uuid.uuid4()),
                    details={"error": str(exc), "path": label},
                )
            batch.processed_photos += 1
            batch.failure_details = failure_details
            session.commit()
        batch.status = CaptureBatchStatus.DONE
        batch.finished_at = datetime.now(UTC)
        session.commit()


def capture_batch_status_response(
    batch: CaptureBatch,
    *,
    captured_noun: str,
    review_url: str,
    style: str = "",
) -> HTMLResponse:
    done = batch.status == CaptureBatchStatus.DONE
    head_extra = "" if done else "<meta http-equiv='refresh' content='3'>"
    failure_html = ""
    if batch.failure_details:
        items = "".join(
            f"<li>{escape(str(key))}: {escape(str(err))}</li>"
            for key, err in batch.failure_details.items()
        )
        failure_html = (
            f"<div class='panel'><h2>{batch.failed_photos} photo(s) failed</h2>"
            f"<ul class='events'>{items}</ul></div>"
        )
    if done:
        status_line = (
            f"<p>{batch.cards_created} {escape(captured_noun)}(s) captured from "
            f"{batch.total_photos} photo(s), queued for review.</p>"
            f"<p><a class='btn' href='{escape(review_url)}'>Go to review queue</a></p>"
        )
    else:
        status_line = (
            f"<p>Processing in the background &mdash; {batch.processed_photos} of "
            f"{batch.total_photos} photo(s) done so far. This page refreshes "
            "automatically; feel free to close it and check back later.</p>"
        )
    body = (
        brand_header("Bulk capture" + (" — done" if done else " — processing"))
        + f"<div class='panel'><h2>{'Done' if done else 'Processing...'}</h2>{status_line}</div>"
        + failure_html
        + style
    )
    return HTMLResponse(page("EzBay — Bulk capture status", body, head_extra=head_extra))


def pending_captures(session: Session, model: type[CapturedT]) -> list[CapturedT]:
    return session.scalars(
        select(model)
        .where(model.status == CardCaptureStatus.PENDING_REVIEW)
        .order_by(model.captured_at, model.id)
    ).all()


def next_pending_capture(
    session: Session, model: type[CapturedT], current: CapturedT | None,
) -> CapturedT | None:
    """Next pending row, wrapping so a skipped current row comes back later."""
    queue = pending_captures(session, model)
    if not queue:
        return None
    if current is None:
        return queue[0]
    ids = [capture.id for capture in queue]
    if current.id in ids:
        index = ids.index(current.id)
        return queue[(index + 1) % len(queue)] if len(queue) > 1 else None
    return queue[0]
