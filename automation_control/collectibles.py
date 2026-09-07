"""Sonny Angel, Smiski, and other blind-box collectibles -- Module 12.

Ownership tracking (`scripts/migrate_relax_inventory_card_id.py`): a real
InventoryItem row (card_id=None, catalog_item_id set) is created whenever a
collectible is added here with a nonzero quantity -- either through the
manual form or through confirming an AI photo capture. This only covers
quantities entered going forward; catalogue entries created before this
migration have no retroactive inventory row (their owned quantity shows as
0, not "unknown"), since there's no reliable source to backfill from. Each
add creates its own new row rather than incrementing an existing one for
the same figure -- restocking the same item as a second, separate purchase
is a real future need but out of scope here.
"""

import mimetypes
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import extract_collectible_details
from .config import get_settings
from .database import SessionLocal, get_session
from .image_processing import auto_orient
from .models import CaptureBatch, CaptureBatchStatus, CapturedCollectible, CardCaptureStatus, CardVariant, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem
from .ui import brand_header, page, pill

router = APIRouter(tags=["collectibles"])

CAPTURE_FIELDS = ["brand", "series", "character", "variant", "blind_box_series"]


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip()) if value.strip() else default
    except ValueError:
        return default


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


@router.get("/collectibles", response_class=HTMLResponse)
def collectibles_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.execute(
        select(CatalogItem, CollectibleProduct).join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
        .order_by(CatalogItem.name)
    ).all()
    owned_by_item = dict(session.execute(
        select(InventoryItem.catalog_item_id, func.sum(InventoryItem.quantity))
        .where(InventoryItem.catalog_item_id.isnot(None)).group_by(InventoryItem.catalog_item_id)
    ).all())
    table_rows = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td>"
        f"<td class='muted'>{escape(cp.brand or '—')}</td>"
        f"<td class='muted'>{escape(cp.series or '—')}</td>"
        f"<td class='muted'>{escape(cp.variant or '—')}</td>"
        f"<td>{pill('secret', 'warn') if cp.is_secret else '—'}</td>"
        f"<td>{owned_by_item.get(item.id, 0)}</td>"
        f"<td>{f'${cp.retail_price:,.2f}' if cp.retail_price else '—'}</td>"
        "</tr>"
        for item, cp in rows
    ) or "<tr><td colspan=7>No collectibles catalogued yet.</td></tr>"
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Name</th><th>Brand</th><th>Series</th><th>Variant</th><th></th><th>Owned</th><th>Retail</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div></div>"
    )

    form = (
        "<div class='panel'><h2>Add a collectible</h2>"
        "<form method='post' action='/collectibles' class='calc-form'>"
        "<label>Name<input name='name' required placeholder='Sonny Angel Fruits Series - Peach'></label>"
        "<label>Brand<input name='brand' placeholder='Sonny Angel, Smiski, ...'></label>"
        "<label>Series<input name='series'></label>"
        "<label>Character<input name='character'></label>"
        "<label>Variant<input name='variant'></label>"
        "<label>Blind box series<input name='blind_box_series'></label>"
        "<label><input name='is_secret' type='checkbox' value='true' style='width:auto'> Secret / chase figure</label>"
        "<label>Retail price<input name='retail_price' type='number' step='0.01'></label>"
        "<label>Quantity owned<input name='quantity' type='number' step='1' min='0' value='0'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    pending_count = len(session.scalars(select(CapturedCollectible).where(CapturedCollectible.status == CardCaptureStatus.PENDING_REVIEW)).all())
    body = (
        brand_header("Collectibles")
        + "<p class='subtitle'>Module 12 -- Sonny Angel, Smiski, blind boxes, with real ownership tracking "
          "(quantity owned, not just catalogued).</p>"
        + f"<div class='panel'><h2>Photo capture (AI-assisted)</h2><p><a class='btn' href='/collectibles/capture'>Capture from a photo</a> &nbsp; "
          f"<a href='/collectibles/review'>Review queue ({pending_count})</a></p></div>"
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Collectibles", body))


@router.post("/collectibles")
def create_collectible(
    name: str = Form(...), brand: str = Form(""), series: str = Form(""), character: str = Form(""),
    variant: str = Form(""), blind_box_series: str = Form(""), is_secret: str = Form(""), retail_price: str = Form(""),
    quantity: str = Form("0"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(
        catalog_item_id=item_id, brand=brand or None, series=series or None, character=character or None,
        variant=variant or None, blind_box_series=blind_box_series or None, is_secret=bool(is_secret),
        retail_price=_dec(retail_price) if retail_price.strip() else None,
    ))
    qty = _int(quantity)
    if qty > 0:
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL, condition="New", quantity=qty))
    session.commit()
    return RedirectResponse("/collectibles", status_code=303)


# --- Module 19: AI-assisted photo capture -----------------------------------
# Mirrors capture.py's pattern exactly: a photo never writes to the
# catalogue directly. extract_collectible_details only ever produces
# CapturedCollectible rows (PENDING_REVIEW); only confirming one on
# /collectibles/review actually creates a CatalogItem/CollectibleProduct.
# A failed or empty extraction still creates one blank row rather than
# silently dropping the photo, so nothing captured is ever lost.

def _collectible_capture_dir(settings) -> Path:
    return Path(settings.captured_cards_dir) / "collectibles"


@router.get("/collectibles/capture", response_class=HTMLResponse)
def collectible_capture_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Capture a collectible")
        + "<p class='subtitle'>Photograph a Sonny Angel, Smiski, or similar figure -- AI extracts brand/series/"
          "character/variant as a suggestion. Nothing is added to the catalogue until you confirm it on the "
          "review queue.</p>"
        + "<div class='panel'><form method='post' action='/collectibles/capture' enctype='multipart/form-data' class='calc-form'>"
        + "<label>Photo<input type='file' name='photo' accept='image/*' capture='environment' required></label>"
        + "<button type='submit'>Capture &amp; identify</button>"
        + "</form></div>"
        + "<p><a href='/collectibles/capture/bulk'>Bulk upload multiple figures instead</a></p>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Capture collectible", body))


def _save_collectible_photo(data: bytes, content_type: str | None, settings) -> Path:
    photo_id = str(uuid.uuid4())
    photo_dir = _collectible_capture_dir(settings) / photo_id
    photo_dir.mkdir(parents=True, exist_ok=True)
    extension = mimetypes.guess_extension(content_type or "") or ".jpg"
    photo_path = photo_dir / f"photo{extension}"
    photo_path.write_bytes(data)
    try:
        auto_orient(photo_path)
    except Exception:
        pass
    return photo_path


def _process_collectible_photo(photo_path: Path, settings, session: Session, user: str) -> list[CapturedCollectible]:
    """Extract collectible details from an already-saved photo and create one
    CapturedCollectible row per figure found -- or one blank row if the AI
    call fails or finds nothing, so a photo is never silently dropped. Shared
    by both the single-photo and bulk capture routes."""
    outcome = "success"
    blank_note = {"note": "no collectible detected in photo"}
    try:
        extracted_list = extract_collectible_details(photo_path, api_key=settings.anthropic_api_key)
        if not extracted_list:
            outcome = "no_collectible_detected"
    except Exception as exc:
        outcome = "failed"
        extracted_list = []
        blank_note = {"error": str(exc)}

    records: list[CapturedCollectible] = []
    if extracted_list:
        for extracted in extracted_list:
            values = {field: (None if field in extracted.unreadable_fields else getattr(extracted, field)) for field in CAPTURE_FIELDS}
            records.append(CapturedCollectible(
                id=str(uuid.uuid4()), image_path=str(photo_path), ai_raw_response=extracted.model_dump(),
                is_secret=extracted.is_secret if "is_secret" not in extracted.unreadable_fields else False,
                status=CardCaptureStatus.PENDING_REVIEW, **values,
            ))
    else:
        records.append(CapturedCollectible(
            id=str(uuid.uuid4()), image_path=str(photo_path), ai_raw_response=blank_note,
            status=CardCaptureStatus.PENDING_REVIEW, **dict.fromkeys(CAPTURE_FIELDS),
        ))

    session.add_all(records)
    session.commit()
    for record in records:
        record_event(
            session, actor_type="user", actor_id=user, action="collectible.capture",
            resource_type="captured_collectible", resource_id=record.id, outcome=outcome,
            correlation_id=str(uuid.uuid4()), details={},
        )
    return records


@router.post("/collectibles/capture")
async def collectible_capture_submit(
    photo: UploadFile = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="collectible recognition is not configured (ANTHROPIC_API_KEY missing)")

    photo_path = _save_collectible_photo(await photo.read(), photo.content_type, settings)
    records = _process_collectible_photo(photo_path, settings, session, user)
    return RedirectResponse(f"/collectibles/review/{records[0].id}", status_code=303)


def _run_collectible_capture_batch(batch_id: str, photo_paths: list[Path], settings, user: str) -> None:
    """Background task: extract + insert every photo in a batch using its own
    DB session, since the request-scoped session closes before this runs.
    One photo's failure is recorded on the batch and the rest still proceed."""
    with SessionLocal() as session:
        batch = session.get(CaptureBatch, batch_id)
        failure_details = dict(batch.failure_details or {})
        for photo_path in photo_paths:
            try:
                records = _process_collectible_photo(photo_path, settings, session, user)
                batch.cards_created += len(records)
            except Exception as exc:
                batch.failed_photos += 1
                failure_details[str(photo_path)] = str(exc)
                record_event(session, actor_type="user", actor_id=user, action="collectible.capture", resource_type="captured_collectible", resource_id=None, outcome="failed", correlation_id=str(uuid.uuid4()), details={"error": str(exc), "path": str(photo_path)})
            batch.processed_photos += 1
            batch.failure_details = failure_details
            session.commit()
        batch.status = CaptureBatchStatus.DONE
        batch.finished_at = datetime.now(UTC)
        session.commit()


@router.get("/collectibles/capture/bulk", response_class=HTMLResponse)
def collectible_capture_bulk_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Bulk capture collectibles")
        + "<p class='subtitle'>Select multiple photos at once -- each becomes its own review-queue entry. "
          "Processing happens in the background, so you can close this page after submitting and check back "
          "on the status page later.</p>"
        + "<div class='panel'><form method='post' action='/collectibles/capture/bulk' enctype='multipart/form-data' class='calc-form'>"
        + "<label>Photos<input type='file' name='photos' accept='image/*' multiple required></label>"
        + "<button type='submit'>Identify all</button>"
        + "</form></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Bulk capture collectibles", body))


@router.post("/collectibles/capture/bulk")
async def collectible_capture_bulk_submit(
    background_tasks: BackgroundTasks,
    photos: list[UploadFile] = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="collectible recognition is not configured (ANTHROPIC_API_KEY missing)")

    photo_paths: list[Path] = []
    save_failures: dict[str, str] = {}
    for photo in photos:
        if not (photo and photo.filename):
            continue
        try:
            photo_paths.append(_save_collectible_photo(await photo.read(), photo.content_type, settings))
        except Exception as exc:
            save_failures[photo.filename or "unnamed"] = str(exc)
            record_event(session, actor_type="user", actor_id=user, action="collectible.capture", resource_type="captured_collectible", resource_id=None, outcome="failed", correlation_id=str(uuid.uuid4()), details={"error": str(exc), "filename": photo.filename})

    batch = CaptureBatch(
        total_photos=len(photo_paths) + len(save_failures),
        processed_photos=len(save_failures),
        failed_photos=len(save_failures),
        failure_details=save_failures,
        started_by=user,
    )
    session.add(batch)
    session.commit()

    background_tasks.add_task(_run_collectible_capture_batch, batch.id, photo_paths, settings, user)
    return RedirectResponse(f"/collectibles/capture/bulk/status/{batch.id}", status_code=303)


@router.get("/collectibles/capture/bulk/status/{batch_id}", response_class=HTMLResponse)
def collectible_capture_bulk_status(batch_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    batch = session.get(CaptureBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")

    done = batch.status == CaptureBatchStatus.DONE
    head_extra = "" if done else "<meta http-equiv='refresh' content='3'>"

    failure_html = ""
    if batch.failure_details:
        items = "".join(f"<li>{escape(str(key))}: {escape(str(err))}</li>" for key, err in batch.failure_details.items())
        failure_html = f"<div class='panel'><h2>{batch.failed_photos} photo(s) failed</h2><ul class='events'>{items}</ul></div>"

    if done:
        status_line = (
            f"<p>{batch.cards_created} figure(s) captured from {batch.total_photos} photo(s), queued for review.</p>"
            + "<p><a class='btn' href='/collectibles/review'>Go to review queue</a></p>"
        )
    else:
        status_line = f"<p>Processing in the background &mdash; {batch.processed_photos} of {batch.total_photos} photo(s) done so far. This page refreshes automatically; feel free to close it and check back later.</p>"

    body = (
        brand_header("Bulk capture" + (" — done" if done else " — processing"))
        + f"<div class='panel'><h2>{'Done' if done else 'Processing...'}</h2>{status_line}</div>"
        + failure_html
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Bulk capture status", body, head_extra=head_extra))


@router.get("/collectibles/review", response_class=HTMLResponse)
def collectible_review_list(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = session.scalars(
        select(CapturedCollectible).where(CapturedCollectible.status == CardCaptureStatus.PENDING_REVIEW)
        .order_by(CapturedCollectible.captured_at)
    ).all()
    if not pending:
        body = (
            brand_header("Collectible review queue")
            + "<div class='panel'><p>Queue is empty -- everything captured so far has been confirmed or discarded.</p>"
              "<p><a class='btn' href='/collectibles/capture'>Capture another</a></p></div>"
        )
        return HTMLResponse(page("EzBay — Collectible review", body))

    rows = "".join(
        f"<tr><td><a href='/collectibles/review/{escape(c.id)}'>{escape(c.character or c.brand or '(unidentified)')}</a></td>"
        f"<td class='muted'>{escape(c.brand or '—')}</td><td class='muted'>{escape(c.series or '—')}</td>"
        f"<td>{c.captured_at:%Y-%m-%d %H:%M}</td></tr>"
        for c in pending
    )
    body = (
        brand_header("Collectible review queue")
        + f"<div class='panel'><div class='table-wrap'><table><thead><tr><th>Figure</th><th>Brand</th><th>Series</th><th>Captured</th></tr></thead>"
        + f"<tbody>{rows}</tbody></table></div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Collectible review", body))


@router.get("/collectibles/review/{capture_id}", response_class=HTMLResponse)
def collectible_review_form(capture_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    def field(name: str, label: str, value: str | None) -> str:
        safe_value = escape(value or "")
        return f"<label>{label}<input name='{name}' value='{safe_value}'></label>"

    body = (
        brand_header("Review capture")
        + f"<div class='panel'><p><img src='/collectibles/capture/image/{escape(capture_id)}' style='max-width:280px;border-radius:8px'></p>"
        + f"<form method='post' action='/collectibles/review/{escape(capture_id)}' class='calc-form'>"
        + field("brand", "Brand", capture.brand)
        + field("series", "Series", capture.series)
        + field("character", "Character", capture.character)
        + field("variant", "Variant", capture.variant)
        + field("blind_box_series", "Blind box series", capture.blind_box_series)
        + f"<label><input name='is_secret' type='checkbox' value='true' style='width:auto'{' checked' if capture.is_secret else ''}> Secret / chase figure</label>"
        + "<label>Quantity owned<input name='quantity' type='number' step='1' min='0' value='1'></label>"
        + "<div style='display:flex;gap:12px;margin-top:6px'>"
        + "<button name='action' value='confirm'>Confirm &amp; add to catalogue</button>"
        + "<button name='action' value='reject' class='ghost'>Discard</button>"
        + "</div></form></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Review capture", body))


@router.post("/collectibles/review/{capture_id}")
def collectible_review_submit(
    capture_id: str,
    action: str = Form("confirm"),
    brand: str = Form(""), series: str = Form(""), character: str = Form(""),
    variant: str = Form(""), blind_box_series: str = Form(""), is_secret: str = Form(""),
    quantity: str = Form("1"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    if action == "reject":
        capture.status = CardCaptureStatus.REJECTED
        capture.reviewed_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="collectible.reject", resource_type="captured_collectible", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={})
        return RedirectResponse("/collectibles/review", status_code=303)

    name = character.strip() or brand.strip() or "Unnamed collectible"
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(
        catalog_item_id=item_id, brand=brand or None, series=series or None, character=character or None,
        variant=variant or None, blind_box_series=blind_box_series or None, is_secret=bool(is_secret),
    ))
    qty = _int(quantity, default=1)
    if qty > 0:
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL, condition="New", quantity=qty))
    capture.status = CardCaptureStatus.REVIEWED
    capture.reviewed_at = datetime.now(UTC)
    capture.catalog_item_id = item_id
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="collectible.confirm", resource_type="captured_collectible", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={"catalog_item_id": item_id})
    return RedirectResponse("/collectibles", status_code=303)


@router.get("/collectibles/capture/image/{capture_id}")
def collectible_capture_image(capture_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    return FileResponse(capture.image_path)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#04101a;font-weight:700;cursor:pointer;margin-top:4px}
.muted{color:var(--text-dim);font-size:13px}
</style>"""
