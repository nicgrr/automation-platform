"""Card sleeves, toploaders, deck boxes, binders and other trading-card
supplies -- a leaner sibling of collectibles.py's AI photo-capture flow.

Mirrors collectibles.py's pattern exactly (a photo never writes to the
catalogue directly; extract_accessory_details only ever produces
CapturedAccessory rows, PENDING_REVIEW; confirming one on
/accessories/review is what actually creates a CatalogItem +
AccessoryProduct + InventoryItem) but leaves out the blind-box-specific
extras that don't apply here: no vocabulary series/character suggestions,
no Google Lens consensus matching, no "fill from a listing link" (a supply
item's brand/type/pack-size is normally legible right off the packaging in
the photo itself, unlike a figure's series name).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import extract_accessory_details
from .capture_workflow import (
    capture_batch_status_response,
    create_capture_batch,
    next_pending_capture,
    pending_captures,
    run_capture_batch,
    save_capture_bytes,
)
from .config import get_settings
from .database import SessionLocal, get_session
from .image_processing import auto_orient, crop_card, remove_background
from .images import serve_image
from .models import AccessoryProduct, CaptureBatch, CapturedAccessory, CardCaptureStatus, CardVariant, CatalogItem, CatalogItemType, InventoryItem
from .navigation import finish_flow, remember_flow_origin
from .ui import brand_header, page, photo_field

router = APIRouter(tags=["accessories"])

CAPTURE_FIELDS = ["brand", "product_type", "pack_size", "color"]


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


@router.get("/accessories", response_class=HTMLResponse)
def accessories_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.execute(
        select(CatalogItem, AccessoryProduct).join(AccessoryProduct, AccessoryProduct.catalog_item_id == CatalogItem.id)
        .order_by(CatalogItem.name)
    ).all()

    owned_by_item = dict(session.execute(
        select(InventoryItem.catalog_item_id, InventoryItem.quantity)
        .where(InventoryItem.catalog_item_id.in_([item.id for item, _ in rows]) if rows else False)
    ).all())

    table_rows = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td>"
        f"<td class='muted'>{escape(ap.brand or '—')}</td>"
        f"<td class='muted'>{escape(ap.product_type or '—')}</td>"
        f"<td class='muted opt'>{escape(ap.pack_size or '—')}</td>"
        f"<td class='muted opt'>{escape(ap.color or '—')}</td>"
        f"<td>{owned_by_item.get(item.id, 0)}</td>"
        f"<td class='num muted opt'>{f'${ap.retail_price:,.2f}' if ap.retail_price else '&mdash;'}</td>"
        "</tr>"
        for item, ap in rows
    ) or "<tr><td colspan=7>No card supplies catalogued yet.</td></tr>"

    table = (
        f"<p class='subtitle'>{len(rows)} catalogued.</p>"
        + "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Name</th><th>Brand</th><th>Type</th><th class='opt'>Pack size</th>"
        "<th class='opt'>Colour</th><th>Owned</th><th class='num opt'>Retail</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div></div>"
    )

    form = (
        "<div class='panel'><h2>Add a supply item</h2>"
        "<form method='post' action='/accessories' class='calc-form'>"
        "<label>Name<input name='name' required placeholder='Ultra Pro Eclipse Sleeves - Standard 100ct'></label>"
        "<label>Brand<input name='brand' placeholder='Ultra Pro, Dragon Shield, ...'></label>"
        "<label>Product type<input name='product_type' placeholder='Sleeves, Toploaders, Deck Box, Binder...'></label>"
        "<label>Pack size<input name='pack_size' placeholder='100ct, 9-Pocket, Standard Size...'></label>"
        "<label>Colour<input name='color'></label>"
        "<label>Retail price<input name='retail_price' type='number' step='0.01'></label>"
        "<label>Quantity owned<input name='quantity' type='number' step='1' min='0' value='1'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    pending_count = len(session.scalars(select(CapturedAccessory).where(CapturedAccessory.status == CardCaptureStatus.PENDING_REVIEW)).all())
    body = (
        brand_header("Card supplies")
        + f"<div class='panel'><h2>Photo capture (AI-assisted)</h2><p><a class='btn' href='/accessories/capture'>Capture from a photo</a> &nbsp; "
          f"<a href='/accessories/review'>Review queue ({pending_count})</a></p></div>"
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Card supplies", body))


@router.post("/accessories")
def create_accessory(
    name: str = Form(...), brand: str = Form(""), product_type: str = Form(""), pack_size: str = Form(""),
    color: str = Form(""), retail_price: str = Form(""), quantity: str = Form("0"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.ACCESSORY, name=name))
    session.add(AccessoryProduct(
        catalog_item_id=item_id, brand=brand or None, product_type=product_type or None,
        pack_size=pack_size or None, color=color or None,
        retail_price=_dec(retail_price) if retail_price.strip() else None,
    ))
    qty = _int(quantity, default=1)
    if qty > 0:
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL, condition="New", quantity=qty))
    session.commit()
    return RedirectResponse("/accessories", status_code=303)


# --- AI-assisted photo capture, mirroring collectibles.py's Module 19 -------

def _accessory_capture_dir(settings) -> Path:
    return Path(settings.captured_cards_dir) / "accessories"


@router.get("/accessories/capture", response_class=HTMLResponse)
def accessory_capture_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Capture a supply item")
        + "<div class='panel'><form method='post' action='/accessories/capture' enctype='multipart/form-data' class='calc-form'>"
        + photo_field("photo", "Photo", required=True, hint="the item or its packaging, well lit")
        + "<button type='submit'>Capture &amp; identify</button>"
        + "</form></div>"
        + "<p><a href='/accessories/capture/bulk'>Bulk upload multiple items instead</a></p>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Capture supply item", body))


def _save_accessory_photo(data: bytes, content_type: str | None, settings) -> Path:
    photo_id = str(uuid.uuid4())
    photo_dir = _accessory_capture_dir(settings) / photo_id
    photo_path = save_capture_bytes(data, content_type, photo_dir, stem="photo")
    try:
        auto_orient(photo_path)
    except Exception:
        pass
    return photo_path


def _item_image(photo_path: Path, extracted, index: int) -> Path:
    """Crop one item out of a shared photo for its review page -- same
    reasoning as collectibles.py's _figure_image: a shelf of supplies in
    one photo needs each detected item visibly matched to its own crop."""
    if not getattr(extracted, "bounding_box", None):
        return photo_path
    cropped = photo_path.parent / f"item{index}_{photo_path.name}"
    try:
        crop_card(photo_path, cropped, extracted.bounding_box)
    except Exception:
        return photo_path
    remove_background(cropped)
    return cropped


def _process_accessory_photo(photo_path: Path, settings, session: Session, user: str) -> list[CapturedAccessory]:
    outcome = "success"
    blank_note = {"note": "no supply item detected in photo"}
    try:
        extracted_list = extract_accessory_details(photo_path, api_key=settings.anthropic_api_key)
        if not extracted_list:
            outcome = "no_accessory_detected"
    except Exception as exc:
        outcome = "failed"
        extracted_list = []
        blank_note = {"error": str(exc)}

    records: list[CapturedAccessory] = []
    if extracted_list:
        for index, extracted in enumerate(extracted_list):
            values = {field: (None if field in extracted.unreadable_fields else getattr(extracted, field)) for field in CAPTURE_FIELDS}
            records.append(CapturedAccessory(
                id=str(uuid.uuid4()), image_path=str(_item_image(photo_path, extracted, index)),
                ai_raw_response=extracted.model_dump(),
                status=CardCaptureStatus.PENDING_REVIEW, **values,
            ))
    else:
        records.append(CapturedAccessory(
            id=str(uuid.uuid4()), image_path=str(photo_path), ai_raw_response=blank_note,
            status=CardCaptureStatus.PENDING_REVIEW, **dict.fromkeys(CAPTURE_FIELDS),
        ))

    try:
        session.add_all(records)
        session.commit()
    except Exception:
        # A DB write failing here (contention with another writer, disk
        # trouble) left the session's transaction dead -- roll it back
        # before the caller does anything else with it, record_event's own
        # commit included, or that fails too with a much more confusing
        # error. This is what actually happened: the AI call itself failed
        # cleanly (caught above, `blank_note` holds why), but writing even
        # that fallback row hit "database is locked", was never caught, and
        # surfaced as a raw 500 with no explanation and nothing in the
        # audit log -- exactly the silent failure this exists to prevent.
        session.rollback()
        raise

    for record in records:
        # outcome != "success" means this record IS blank_note -- an AI
        # failure or a miss, not a real find -- so the reason belongs in
        # the audit trail rather than an empty {}, which is what made this
        # one take a service-log read to diagnose instead of a look at
        # /settings or the events themselves.
        record_event(
            session, actor_type="user", actor_id=user, action="accessory.capture",
            resource_type="captured_accessory", resource_id=record.id, outcome=outcome,
            correlation_id=str(uuid.uuid4()), details=blank_note if outcome != "success" else {},
        )
    return records


@router.post("/accessories/capture")
async def accessory_capture_submit(
    request: Request,
    photo: UploadFile = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="accessory recognition is not configured (ANTHROPIC_API_KEY missing)")

    photo_path = _save_accessory_photo(await photo.read(), photo.content_type, settings)
    try:
        records = _process_accessory_photo(photo_path, settings, session, user)
    except Exception as exc:
        # A raw 500 here is what the screenshot that prompted this showed:
        # no explanation, and the photo already saved to disk with nothing
        # pointing back at it. A controlled error at least says why.
        raise HTTPException(status_code=500, detail=f"failed to save this capture: {exc}") from exc
    return remember_flow_origin(request, RedirectResponse(f"/accessories/review/{records[0].id}", status_code=303))


def _run_accessory_capture_batch(batch_id: str, photo_paths: list[Path], settings, user: str) -> None:
    run_capture_batch(
        batch_id, photo_paths, settings, user,
        session_factory=SessionLocal,
        process_one=_process_accessory_photo,
        item_label=str,
        audit_action="accessory.capture",
        resource_type="captured_accessory",
    )


@router.get("/accessories/capture/bulk", response_class=HTMLResponse)
def accessory_capture_bulk_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Bulk capture supplies")
        + "<div class='panel'><form method='post' action='/accessories/capture/bulk' enctype='multipart/form-data' class='calc-form'>"
        + photo_field("photos", "Photos", required=True, multiple=True, hint="one item (or box of it) per photo")
        + "<button type='submit'>Identify all</button>"
        + "</form></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Bulk capture supplies", body))


@router.post("/accessories/capture/bulk")
async def accessory_capture_bulk_submit(
    background_tasks: BackgroundTasks,
    photos: list[UploadFile] = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="accessory recognition is not configured (ANTHROPIC_API_KEY missing)")

    photo_paths: list[Path] = []
    save_failures: dict[str, str] = {}
    for photo in photos:
        if not (photo and photo.filename):
            continue
        try:
            photo_paths.append(_save_accessory_photo(await photo.read(), photo.content_type, settings))
        except Exception as exc:
            save_failures[photo.filename or "unnamed"] = str(exc)
            record_event(session, actor_type="user", actor_id=user, action="accessory.capture", resource_type="captured_accessory", resource_id=None, outcome="failed", correlation_id=str(uuid.uuid4()), details={"error": str(exc), "filename": photo.filename})

    batch = create_capture_batch(
        session, saved_count=len(photo_paths), save_failures=save_failures, user=user,
    )

    background_tasks.add_task(_run_accessory_capture_batch, batch.id, photo_paths, settings, user)
    return RedirectResponse(f"/accessories/capture/bulk/status/{batch.id}", status_code=303)


@router.get("/accessories/capture/bulk/status/{batch_id}", response_class=HTMLResponse)
def accessory_capture_bulk_status(batch_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    batch = session.get(CaptureBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")

    return capture_batch_status_response(
        batch, captured_noun="item", review_url="/accessories/review", style=_STYLE,
    )


def _pending(session: Session) -> list[CapturedAccessory]:
    return pending_captures(session, CapturedAccessory)


def _next_after(session: Session, current: CapturedAccessory | None) -> CapturedAccessory | None:
    return next_pending_capture(session, CapturedAccessory, current)


def _queue_done_page() -> HTMLResponse:
    body = (
        brand_header("Supply review queue")
        + "<div class='panel'><h2>Queue is empty</h2>"
          "<p>Everything captured so far has been confirmed or discarded.</p>"
          "<p><a class='btn' href='/accessories'>Back to card supplies</a> "
          "<a class='btn ghost' href='/accessories/capture'>Capture another</a></p></div>"
    )
    return HTMLResponse(page("EzBay — Supply review", body))


@router.get("/accessories/review", response_class=HTMLResponse)
def accessory_review_start(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    nxt = _next_after(session, None)
    if nxt is None:
        return _queue_done_page()
    return RedirectResponse(f"/accessories/review/{nxt.id}", status_code=303)


@router.get("/accessories/review/all", response_class=HTMLResponse)
def accessory_review_list(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = _pending(session)
    if not pending:
        return _queue_done_page()

    rows = "".join(
        f"<tr><td><a href='/accessories/review/{escape(c.id)}'>{escape(c.product_type or c.brand or '(unidentified)')}</a></td>"
        f"<td class='muted'>{escape(c.brand or '—')}</td><td class='muted'>{escape(c.pack_size or '—')}</td>"
        f"<td>{c.captured_at:%Y-%m-%d %H:%M}</td></tr>"
        for c in pending
    )
    body = (
        brand_header("Supply review queue")
        + f"<p class='subtitle'>{len(pending)} awaiting review. "
          "<a class='btn' href='/accessories/review'>Review one by one</a></p>"
        + f"<div class='panel'><div class='table-wrap'><table><thead><tr><th>Item</th><th>Brand</th><th>Pack size</th><th>Captured</th></tr></thead>"
        + f"<tbody>{rows}</tbody></table></div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Supply review", body))


@router.get("/accessories/review/{capture_id}", response_class=HTMLResponse)
def accessory_review_form(capture_id: str, user: str = Depends(require_dashboard_user),
                          session: Session = Depends(get_session)) -> HTMLResponse:
    capture = session.get(CapturedAccessory, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    def field(name: str, label: str, value: str | None, autofocus: bool = False) -> str:
        safe_value = escape(value or "")
        focus = " autofocus" if autofocus else ""
        return f"<label>{label}<input id='field-{name}' name='{name}' value='{safe_value}'{focus}></label>"

    queue = _pending(session)
    ids = [c.id for c in queue]
    position = ids.index(capture_id) + 1 if capture_id in ids else 0
    remaining = len(queue)
    progress = (
        f"<p class='subtitle'>Item {position} of {remaining} awaiting review · "
        "<a href='/accessories/review/all'>see the whole queue</a></p>"
        if position else "<p class='subtitle'>Already reviewed.</p>"
    )
    bar = (
        f"<div class='revbar'><span style='width:{(position - 1) / remaining * 100:.1f}%'></span></div>"
        if position and remaining else ""
    )
    focus_product_type = not (capture.product_type or "").strip()

    body = (
        brand_header("Review capture")
        + progress + bar
        + "<div class='panel review-one'>"
        + f"<a class='revshot' href='/accessories/capture/image/{escape(capture_id)}' target='_blank' rel='noopener'>"
          f"<img src='/accessories/capture/image/{escape(capture_id)}' alt='Captured item'></a>"
        + f"<form method='post' action='/accessories/review/{escape(capture_id)}' class='calc-form'>"
        + field("brand", "Brand", capture.brand)
        + field("product_type", "Product type", capture.product_type, autofocus=focus_product_type)
        + field("pack_size", "Pack size", capture.pack_size)
        + field("color", "Colour", capture.color)
        + "<label>Retail price<input name='retail_price' type='number' step='0.01'></label>"
        + "<label>Quantity owned<input name='quantity' type='number' step='1' min='0' value='1'></label>"
        + "<div class='revactions'>"
        + "<button name='action' value='confirm'>Confirm &amp; next</button>"
        + "<button name='action' value='skip' class='ghost'>Skip for now</button>"
        + "<button name='action' value='reject' class='ghost danger'>Discard</button>"
        + "</div></form></div>"
        + _REVIEW_STYLE
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Review capture", body))


@router.post("/accessories/review/{capture_id}")
def accessory_review_submit(
    capture_id: str,
    request: Request,
    action: str = Form("confirm"),
    brand: str = Form(""), product_type: str = Form(""), pack_size: str = Form(""), color: str = Form(""),
    retail_price: str = Form(""), quantity: str = Form("1"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    capture = session.get(CapturedAccessory, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    def onward() -> RedirectResponse:
        nxt = _next_after(session, capture)
        if nxt is None:
            return finish_flow(request, "/accessories/review")
        return RedirectResponse(f"/accessories/review/{nxt.id}", status_code=303)

    if action == "skip":
        return onward()

    if action == "reject":
        capture.status = CardCaptureStatus.REJECTED
        capture.reviewed_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="accessory.reject", resource_type="captured_accessory", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={})
        return onward()

    name = " ".join(part for part in (brand.strip(), product_type.strip(), pack_size.strip()) if part) or "Unnamed supply item"
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.ACCESSORY, name=name))
    session.add(AccessoryProduct(
        catalog_item_id=item_id, brand=brand or None, product_type=product_type or None,
        pack_size=pack_size or None, color=color or None,
        retail_price=_dec(retail_price) if retail_price.strip() else None,
    ))
    qty = _int(quantity, default=1)
    if qty > 0:
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL,
                                  condition="New", quantity=qty, scan_image_path=capture.image_path))
    capture.status = CardCaptureStatus.REVIEWED
    capture.reviewed_at = datetime.now(UTC)
    capture.catalog_item_id = item_id
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="accessory.confirm", resource_type="captured_accessory", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={"catalog_item_id": item_id})
    return onward()


@router.get("/accessories/capture/image/{capture_id}")
def accessory_capture_image(capture_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    capture = session.get(CapturedAccessory, capture_id)
    return serve_image(
        capture.image_path if capture else None,
        unset="capture not found",
        missing="photo file missing",
    )


_REVIEW_STYLE = """<style>
.revbar{height:5px;border-radius:99px;background:var(--surface-sunken);overflow:hidden;margin:0 0 18px}
.revbar span{display:block;height:100%;background:var(--accent);transition:width .2s ease}
.review-one{display:grid;gap:24px;grid-template-columns:minmax(200px,300px) 1fr;align-items:start}
.revshot{display:block;border-radius:10px;overflow:hidden;background:var(--surface-sunken);
  border:1px solid var(--panel-border)}
.revshot img{width:100%;display:block}
.revactions{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.revactions .danger{color:var(--danger);border-color:var(--danger)}
@media (max-width:720px){.review-one{grid-template-columns:1fr}
  .revshot{max-width:260px;margin:0 auto}}
@media (prefers-reduced-motion:reduce){.revbar span{transition:none}}
</style>"""

_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.muted{color:var(--text-dim);font-size:13px}
</style>"""
