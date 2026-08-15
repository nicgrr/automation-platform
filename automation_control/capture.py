import mimetypes
import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import extract_card_details
from .database import SessionLocal, get_session
from .models import CaptureBatch, CaptureBatchStatus, CapturedCard, CardCaptureStatus
from .ui import brand_header, page

router = APIRouter(prefix="/cards", tags=["cards"])

FIELDS = ["character", "set_name", "card_number", "rarity", "language", "graded"]


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _has_content(upload: UploadFile | None) -> bool:
    return bool(upload and upload.filename)


def _save_upload(upload: UploadFile, card_dir: Path, name: str, data: bytes) -> Path:
    extension = mimetypes.guess_extension(upload.content_type or "") or ".jpg"
    path = card_dir / f"{name}{extension}"
    path.write_bytes(data)
    return path


async def _save_photo_pair(front: UploadFile, back: UploadFile | None, settings) -> tuple[Path, Path | None]:
    """Save uploaded file(s) to disk and return their paths. Split out from
    extraction/DB work so bulk uploads can save (fast, just disk I/O) inside
    the request, then extract (slow, calls the Anthropic API) as a background
    task -- the browser doesn't have to hold the request open for that part.
    """
    photo_id = str(uuid.uuid4())
    photo_dir = Path(settings.captured_cards_dir) / photo_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    front_path = _save_upload(front, photo_dir, "front", await front.read())
    back_path = _save_upload(back, photo_dir, "back", await back.read()) if _has_content(back) else None
    return front_path, back_path


def _process_photo(front_path: Path, back_path: Path | None, settings, session: Session, user: str) -> list[CapturedCard]:
    """Extract card details from already-saved photo file(s) and create one
    CapturedCard row per card the AI finds. A photo can show more than one
    physical card (e.g. a promo lot laid out together) -- every detected card
    gets its own review-queue row, all pointing at the same shared image
    file(s). If the AI call fails or finds nothing, we still create a single
    blank row so the photo isn't silently dropped -- it can be filled in by
    hand on review.
    """
    back_path_str = str(back_path) if back_path else None

    outcome = "success"
    try:
        extracted_list = extract_card_details(front_path, back_path, api_key=settings.anthropic_api_key)
        blank_note = {"note": "no card detected in photo"}
        if not extracted_list:
            outcome = "no_card_detected"
    except Exception as exc:
        outcome = "failed"
        extracted_list = []
        blank_note = {"error": str(exc)}

    records: list[CapturedCard] = []
    if extracted_list:
        for extracted in extracted_list:
            values = {field: (None if field in extracted.unreadable_fields else getattr(extracted, field)) for field in FIELDS}
            records.append(CapturedCard(
                id=str(uuid.uuid4()), front_image_path=str(front_path), back_image_path=back_path_str,
                ai_raw_response=extracted.model_dump(), status=CardCaptureStatus.PENDING_REVIEW, **values,
            ))
    else:
        records.append(CapturedCard(
            id=str(uuid.uuid4()), front_image_path=str(front_path), back_image_path=back_path_str,
            ai_raw_response=blank_note, status=CardCaptureStatus.PENDING_REVIEW, **dict.fromkeys(FIELDS),
        ))

    try:
        session.add_all(records)
        session.commit()
    except Exception:
        session.rollback()
        raise

    for record in records:
        record_event(session, actor_type="user", actor_id=user, action="card.capture", resource_type="captured_card", resource_id=record.id, outcome=outcome, correlation_id=_correlation_id(), details={})
    return records


async def _capture_photo(front: UploadFile, back: UploadFile | None, settings, session: Session, user: str) -> list[CapturedCard]:
    front_path, back_path = await _save_photo_pair(front, back, settings)
    return _process_photo(front_path, back_path, settings, session, user)


def _run_capture_batch(batch_id: str, saved_photos: list[tuple[Path, Path | None]], settings, user: str) -> None:
    """Background task: extract + insert every photo in a batch using its own
    DB session, since the request-scoped session closes as soon as the
    upload response is sent -- long before this runs. One photo's failure is
    recorded on the batch and the rest still proceed.
    """
    with SessionLocal() as session:
        batch = session.get(CaptureBatch, batch_id)
        failure_details = dict(batch.failure_details or {})
        for front_path, back_path in saved_photos:
            try:
                records = _process_photo(front_path, back_path, settings, session, user)
                batch.cards_created += len(records)
            except Exception as exc:
                batch.failed_photos += 1
                failure_details[str(front_path)] = str(exc)
                record_event(session, actor_type="user", actor_id=user, action="card.capture", resource_type="captured_card", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": str(exc), "path": str(front_path)})
            batch.processed_photos += 1
            batch.failure_details = failure_details
            session.commit()
        batch.status = CaptureBatchStatus.DONE
        batch.finished_at = datetime.now(UTC)
        session.commit()


@router.get("/capture", response_class=HTMLResponse)
def capture_page(user: str = Depends(require_dashboard_user)) -> str:
    body = (
        brand_header("Capture a card")
        + "<p class='subtitle'>Photograph the front (back optional), then submit for AI identification.</p>"
        + "<div class='panel'><form method='post' action='/cards/capture' enctype='multipart/form-data'>"
        + "<label>Front photo <input type='file' name='front' accept='image/*' capture='environment' required></label>"
        + "<label>Back photo (optional) <input type='file' name='back' accept='image/*' capture='environment'></label>"
        + "<button>Identify card</button>"
        + "</form></div>"
        + "<p><a href='/cards/capture/bulk'>Bulk upload multiple cards instead</a></p>"
    )
    return page("EzBay — Capture", body)


@router.post("/capture")
async def capture_submit(
    request: Request,
    front: UploadFile = File(...),
    back: UploadFile | None = File(None),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="card recognition is not configured (ANTHROPIC_API_KEY missing)")

    try:
        records = await _capture_photo(front, back, settings, session, user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save this capture: {exc}") from exc

    if len(records) == 1:
        return RedirectResponse(f"/cards/review/{records[0].id}", status_code=303)
    return RedirectResponse("/cards/review", status_code=303)


@router.get("/capture/bulk", response_class=HTMLResponse)
def capture_bulk_page(user: str = Depends(require_dashboard_user)) -> str:
    body = (
        brand_header("Bulk capture")
        + "<p class='subtitle'>Select multiple front photos at once -- each becomes its own card, front-only (no back). Processing happens in the background, so you can close this page after submitting and check back on the status page later. Use single capture instead if you need a back photo, e.g. for a graded slab's certification label.</p>"
        + "<div class='panel'><form method='post' action='/cards/capture/bulk' enctype='multipart/form-data'>"
        + "<label>Front photos <input type='file' name='photos' accept='image/*' multiple required></label>"
        + "<button>Identify all cards</button>"
        + "</form></div>"
    )
    return page("EzBay — Bulk capture", body)


@router.post("/capture/bulk")
async def capture_bulk_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    photos: list[UploadFile] = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="card recognition is not configured (ANTHROPIC_API_KEY missing)")

    # Saving is fast (disk I/O only) so it stays synchronous; a photo that
    # fails to save is recorded as a batch failure immediately rather than
    # aborting the rest of the upload, same principle as the background
    # extraction step below.
    saved_photos: list[tuple[Path, Path | None]] = []
    save_failures: dict[str, str] = {}
    for photo in photos:
        if not _has_content(photo):
            continue
        try:
            saved_photos.append(await _save_photo_pair(photo, None, settings))
        except Exception as exc:
            save_failures[photo.filename or "unnamed"] = str(exc)
            record_event(session, actor_type="user", actor_id=user, action="card.capture", resource_type="captured_card", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": str(exc), "filename": photo.filename})

    batch = CaptureBatch(
        total_photos=len(saved_photos) + len(save_failures),
        processed_photos=len(save_failures),
        failed_photos=len(save_failures),
        failure_details=save_failures,
        started_by=user,
    )
    session.add(batch)
    session.commit()

    background_tasks.add_task(_run_capture_batch, batch.id, saved_photos, settings, user)

    return RedirectResponse(f"/cards/capture/bulk/status/{batch.id}", status_code=303)


@router.get("/capture/bulk/status/{batch_id}", response_class=HTMLResponse)
def capture_bulk_status(batch_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    batch = session.get(CaptureBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")

    done = batch.status == CaptureBatchStatus.DONE
    head_extra = "" if done else "<meta http-equiv='refresh' content='3'>"

    failure_html = ""
    if batch.failure_details:
        items = "".join(f"<li>{escape(str(key))}: {escape(str(err))}</li>" for key, err in batch.failure_details.items())
        failure_html = f"<div class='panel'><h2>{batch.failed_photos} photo(s) failed</h2><ul class='events'>{items}</ul></div>"

    if done:
        status_line = (
            f"<p>{batch.cards_created} card(s) captured from {batch.total_photos} photo(s), queued for review.</p>"
            + "<p><a class='btn' href='/cards/review'>Go to review queue</a></p>"
        )
    else:
        status_line = f"<p>Processing in the background &mdash; {batch.processed_photos} of {batch.total_photos} photo(s) done so far. This page refreshes automatically; feel free to close it and check back later.</p>"

    body = (
        brand_header("Bulk capture" + (" — done" if done else " — processing"))
        + f"<div class='panel'><h2>{'Done' if done else 'Processing...'}</h2>{status_line}</div>"
        + failure_html
    )
    return HTMLResponse(page("EzBay — Bulk capture status", body, head_extra=head_extra))


@router.get("/review", response_class=HTMLResponse)
def review_list(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    cards = session.scalars(select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW).order_by(CapturedCard.captured_at)).all()
    rows = "".join(
        f"<li><a href='/cards/review/{c.id}'>{escape(c.character or '(unread) ')} — {escape(c.set_name or '?')} #{escape(c.card_number or '?')}</a></li>"
        for c in cards
    ) or "<li>No cards pending review.</li>"
    body = (
        brand_header("Pending review")
        + f"<div class='panel'><h2>Pending review ({len(cards)})</h2><ul class='events'>{rows}</ul></div>"
        + "<p><a class='btn' href='/cards/capture'>Capture another card</a> &nbsp; <a href='/cards/capture/bulk'>Bulk upload</a></p>"
    )
    return page("EzBay — Review queue", body)


@router.get("/review/{card_id}", response_class=HTMLResponse)
def review_form(card_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    unreadable = set(card.ai_raw_response.get("unreadable_fields", [])) if isinstance(card.ai_raw_response, dict) else set()
    field_inputs = "".join(
        f"<label>{escape(field.replace('_', ' ').title())}"
        + (" <span class='pill bad'>unreadable</span>" if field in unreadable else "")
        + f" <input name='{field}' value='{escape(getattr(card, field) or '')}'></label>"
        for field in FIELDS
    )
    back_img = f"<img src='/cards/image/{card_id}/back' style='max-width:280px;border-radius:8px'>" if card.back_image_path else "<span class='pill neutral'>no back photo</span>"
    body = (
        brand_header("Review capture")
        + "<div class='panel'>"
        + f"<h2>Review card</h2>"
        + f"<p><img src='/cards/image/{card_id}/front' style='max-width:280px;border-radius:8px;margin-right:12px'>"
        + back_img + "</p>"
        + f"<form method='post' action='/cards/review/{card_id}'>{field_inputs}<button>Save as reviewed</button></form>"
        + "</div>"
    )
    return page("EzBay — Review card", body)


@router.post("/review/{card_id}")
def review_submit(
    card_id: str,
    character: str = Form(...),
    set_name: str = Form(...),
    card_number: str = Form(...),
    rarity: str = Form(...),
    language: str = Form(...),
    graded: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    card.character = character
    card.set_name = set_name
    card.card_number = card_number
    card.rarity = rarity
    card.language = language
    card.graded = graded
    card.status = CardCaptureStatus.REVIEWED
    card.reviewed_at = datetime.now(UTC)
    card.reviewed_by = user
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="card.review", resource_type="captured_card", resource_id=card_id, outcome="success", correlation_id=_correlation_id(), details={})

    return RedirectResponse("/cards/review", status_code=303)


@router.get("/image/{card_id}/{side}")
def card_image(card_id: str, side: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    if side not in ("front", "back"):
        raise HTTPException(status_code=404, detail="unknown side")
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    raw_path = card.front_image_path if side == "front" else card.back_image_path
    if not raw_path:
        raise HTTPException(status_code=404, detail="no photo for this side")
    path = Path(raw_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(path)
