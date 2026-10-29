import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import extract_card_details
from .capture_workflow import (
    capture_batch_status_response,
    create_capture_batch,
    next_pending_capture,
    pending_captures,
    run_capture_batch,
    save_capture_bytes,
)
from .database import SessionLocal, get_session
from .image_processing import auto_orient, crop_card
from .images import serve_image
from .models import (
    CaptureBatch, CapturedCard, CardCaptureStatus, CardVariant,
    CapturePromotion, CapturePromotionStatus,
)
from .navigation import finish_flow, remember_flow_origin
from .ui import brand_header, page, photo_field

router = APIRouter(prefix="/cards", tags=["cards"])

FIELDS = ["character", "set_name", "card_number", "rarity", "language", "graded"]
CONDITIONS = ("Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged")


def _review_variant_picker(selected: CardVariant | None) -> str:
    labels = {
        CardVariant.NORMAL: "Normal",
        CardVariant.HOLO: "Holo",
        CardVariant.REVERSE_HOLO: "Reverse Holo",
        CardVariant.POKEBALL_PATTERN: "Poké Ball pattern",
        CardVariant.MASTERBALL_PATTERN: "Master Ball pattern",
    }
    options = "<option value=''>Choose printing…</option>" + "".join(
        f"<option value='{value.value}'{' selected' if value == selected else ''}>{label}</option>"
        for value, label in labels.items()
    )
    return f"<label>Printing <select name='variant' required>{options}</select></label>"


def _review_condition_picker(selected: str | None) -> str:
    current = selected or "Near Mint"
    options = "".join(
        f"<option value='{escape(value)}'{' selected' if value == current else ''}>{escape(value)}</option>"
        for value in CONDITIONS
    )
    return f"<label>Condition <select name='condition' required>{options}</select></label>"


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _has_content(upload: UploadFile | None) -> bool:
    return bool(upload and upload.filename)


def _save_upload(upload: UploadFile, card_dir: Path, name: str, data: bytes) -> Path:
    return save_capture_bytes(data, upload.content_type, card_dir, stem=name)


async def _save_photo_pair(front: UploadFile, back: UploadFile | None, settings) -> tuple[Path, Path | None]:
    """Save uploaded file(s) to disk and return their paths. Split out from
    extraction/DB work so bulk uploads can save (fast, just disk I/O) inside
    the request, then extract (slow, calls the Anthropic API) as a background
    task -- the browser doesn't have to hold the request open for that part.
    Each file is auto-oriented right after saving (cheap, no AI needed) so a
    phone photo taken sideways/upside-down is corrected before anything else
    -- AI-detected rotation (per card, see _process_photo) handles the
    separate case of a card laid sideways within an otherwise upright photo.
    """
    photo_id = str(uuid.uuid4())
    photo_dir = Path(settings.captured_cards_dir) / photo_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    front_path = _save_upload(front, photo_dir, "front", await front.read())
    _try_auto_orient(front_path)
    back_path = _save_upload(back, photo_dir, "back", await back.read()) if _has_content(back) else None
    if back_path:
        _try_auto_orient(back_path)
    return front_path, back_path


def _try_auto_orient(path: Path) -> None:
    try:
        auto_orient(path)
    except Exception:
        pass


def _card_front_image(front_path: Path, index: int, extracted) -> Path:
    """Crop this one card out of the (possibly multi-card) front photo using
    its AI-detected bounding box, so its review page shows just this card
    instead of the shared group photo -- otherwise a 3-card photo shows the
    same picture on all 3 review pages with no way to tell which detected
    name/number belongs to which physical card. Falls back to the original,
    uncropped photo if the box is missing or malformed rather than failing
    the whole capture over a cosmetic step.
    """
    if not extracted.bounding_box:
        return front_path
    cropped_path = front_path.parent / f"card{index}_{front_path.name}"
    try:
        crop_card(front_path, cropped_path, extracted.bounding_box, extracted.rotation_degrees)
    except Exception:
        return front_path
    return cropped_path


def _process_photo(front_path: Path, back_path: Path | None, settings, session: Session, user: str) -> list[CapturedCard]:
    """Extract card details from already-saved photo file(s) and create one
    CapturedCard row per card the AI finds. A photo can show more than one
    physical card (e.g. a promo lot laid out together) -- every detected card
    gets its own review-queue row, its front image cropped from the shared
    photo down to just that card (see _card_front_image). If the AI call
    fails or finds nothing, we still create a single blank row so the photo
    isn't silently dropped -- it can be filled in by hand on review.
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
        for index, extracted in enumerate(extracted_list):
            values = {field: (None if field in extracted.unreadable_fields else getattr(extracted, field)) for field in FIELDS}
            card_front_path = _card_front_image(front_path, index, extracted)
            records.append(CapturedCard(
                id=str(uuid.uuid4()), front_image_path=str(card_front_path), back_image_path=back_path_str,
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
        # outcome != "success" means this record IS blank_note -- an AI
        # failure or a miss, not a real find -- so the reason belongs in
        # the audit trail rather than an empty {}.
        record_event(session, actor_type="user", actor_id=user, action="card.capture", resource_type="captured_card", resource_id=record.id, outcome=outcome, correlation_id=_correlation_id(), details=blank_note if outcome != "success" else {})
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
    def process(saved, current_settings, session, actor):
        front_path, back_path = saved
        return _process_photo(front_path, back_path, current_settings, session, actor)

    run_capture_batch(
        batch_id, saved_photos, settings, user,
        session_factory=SessionLocal,
        process_one=process,
        item_label=lambda saved: str(saved[0]),
        audit_action="card.capture",
        resource_type="captured_card",
    )


@router.get("/capture", response_class=HTMLResponse)
def capture_page(
    request: Request,
    user: str = Depends(require_dashboard_user),
):
    if request.app.state.settings.card_bulk_upload_only:
        return RedirectResponse("/cards/capture/bulk", status_code=303)
    body = (
        brand_header("Capture a card")
        + "<div class='panel'><form method='post' action='/cards/capture' enctype='multipart/form-data'>"
        + photo_field("front", "Front photo", required=True, hint="fills the frame, face up")
        + photo_field("back", "Back photo", hint="optional \u2014 needed for a graded slab's cert label")
        + "<button>Identify card</button>"
        + "</form></div>"
        + "<p><a href='/cards/capture/bulk'>Bulk upload multiple cards instead</a></p>"
    )
    return page("EzBay — Capture", body)


@router.post("/capture")
async def capture_submit(
    request: Request,
    front: UploadFile | None = File(None),
    back: UploadFile | None = File(None),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    if settings.card_bulk_upload_only:
        return RedirectResponse("/cards/capture/bulk", status_code=303)
    if not _has_content(front):
        raise HTTPException(status_code=400, detail="front photo is required")
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="card recognition is not configured (ANTHROPIC_API_KEY missing)")

    try:
        records = await _capture_photo(front, back, settings, session, user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save this capture: {exc}") from exc

    if len(records) == 1:
        return remember_flow_origin(request, RedirectResponse(f"/cards/review/{records[0].id}", status_code=303))
    return RedirectResponse("/cards/review", status_code=303)


@router.get("/capture/bulk", response_class=HTMLResponse)
def capture_bulk_page(user: str = Depends(require_dashboard_user)) -> str:
    body = (
        brand_header("Bulk capture")
        + "<div class='panel'><form method='post' action='/cards/capture/bulk' enctype='multipart/form-data'>"
        + photo_field("photos", "Front photos", required=True, multiple=True, hint="one card per photo")
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

    batch = create_capture_batch(
        session, saved_count=len(saved_photos), save_failures=save_failures, user=user,
    )

    background_tasks.add_task(_run_capture_batch, batch.id, saved_photos, settings, user)

    return remember_flow_origin(request, RedirectResponse(f"/cards/capture/bulk/status/{batch.id}", status_code=303))


@router.get("/capture/bulk/status/{batch_id}", response_class=HTMLResponse)
def capture_bulk_status(batch_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    batch = session.get(CaptureBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")

    return capture_batch_status_response(batch, captured_noun="card", review_url="/cards/review")


def _next_pending_card_id(session: Session, exclude_id: str) -> str | None:
    nxt = next_pending_capture(session, CapturedCard, None)
    return nxt.id if nxt and nxt.id != exclude_id else None


@router.get("/review", response_class=HTMLResponse)
def review_list(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    cards = pending_captures(session, CapturedCard)
    rows = "".join(
        f"<li><a href='/cards/review/{c.id}'>{escape(c.character or '(unread) ')} — {escape(c.set_name or '?')} #{escape(c.card_number or '?')}</a></li>"
        for c in cards
    ) or "<li>No cards pending review.</li>"
    body = (
        brand_header("Pending review")
        + f"<div class='panel'><h2>Pending review ({len(cards)})</h2><ul class='events'>{rows}</ul></div>"
        + ("<p><a class='btn' href='" + f"/cards/review/{cards[0].id}" + "'>Start reviewing</a></p>" if cards else "")
        + "<p><a href='/cards/capture/bulk'>Upload another card batch</a></p>"
    )
    return page("EzBay — Review queue", body)


@router.get("/review/{card_id}", response_class=HTMLResponse)
def review_form(card_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    remaining = session.scalar(select(func.count()).select_from(CapturedCard).where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW))

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
        + f"<h2>Review card ({remaining} pending)</h2>"
        + f"<p><img src='/cards/image/{card_id}/front' style='max-width:280px;border-radius:8px;margin-right:12px'>"
        + back_img + "</p>"
        + f"<form method='post' action='/cards/review/{card_id}'>{field_inputs}"
        + _review_variant_picker(card.variant)
        + _review_condition_picker(card.condition)
        + "<div style='display:flex;gap:12px'>"
        + "<button name='action' value='approve' style='background:linear-gradient(120deg,var(--success),var(--accent))'>Approve</button>"
        + "<button name='action' value='reject' style='background:var(--danger);color:#fff'>Disapprove</button>"
        + "</div></form>"
        + "</div>"
    )
    return page("EzBay — Review card", body)


@router.post("/review/{card_id}")
def review_submit(
    card_id: str,
    request: Request,
    action: str = Form("approve"),
    character: str = Form(""),
    set_name: str = Form(""),
    card_number: str = Form(""),
    rarity: str = Form(""),
    language: str = Form(""),
    graded: str = Form(""),
    variant: str = Form(""),
    condition: str = Form("Near Mint"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    if action == "reject":
        card.status = CardCaptureStatus.REJECTED
        audit_action = "card.reject"
    else:
        try:
            selected_variant = CardVariant(variant)
        except ValueError:
            raise HTTPException(status_code=400, detail="choose the physical printing") from None
        if condition not in CONDITIONS:
            raise HTTPException(status_code=400, detail="unknown card condition")
        card.character = character
        card.set_name = set_name
        card.card_number = card_number
        card.rarity = rarity
        card.language = language
        card.graded = graded
        card.variant = selected_variant
        card.condition = condition
        card.status = CardCaptureStatus.REVIEWED
        audit_action = "card.review"

    card.reviewed_at = datetime.now(UTC)
    card.reviewed_by = user
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action=audit_action, resource_type="captured_card", resource_id=card_id, outcome="success", correlation_id=_correlation_id(), details={})

    # Identity corrections for a blocked listing handoff are made on this
    # form.  Return straight to pricing so the corrected card can be retried
    # instead of dropping the reviewer on an empty pending-capture list.
    promotion = session.scalar(
        select(CapturePromotion).where(
            CapturePromotion.captured_card_id == card_id,
            CapturePromotion.status == CapturePromotionStatus.NEEDS_REVIEW,
        )
    )
    if action != "reject" and promotion is not None:
        return RedirectResponse(f"/cards/pricing/{card_id}", status_code=303)

    next_id = _next_pending_card_id(session, card_id)
    if next_id:
        return RedirectResponse(f"/cards/review/{next_id}", status_code=303)
    return finish_flow(request, "/cards/review")


@router.get("/image/{card_id}/{side}")
def card_image(card_id: str, side: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    if side not in ("front", "back"):
        raise HTTPException(status_code=404, detail="unknown side")
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    raw_path = card.front_image_path if side == "front" else card.back_image_path
    return serve_image(raw_path, unset="no photo for this side")
