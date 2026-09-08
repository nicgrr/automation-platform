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
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import extract_collectible_details
from .config import get_settings
from .database import SessionLocal, get_session
from .image_processing import auto_orient, crop_card, remove_background
from .models import CaptureBatch, CaptureBatchStatus, CapturedCollectible, CardCaptureStatus, CardPrice, CardVariant, CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem
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


COLLECTIBLE_SORTS = {
    "name": "Name (A-Z)",
    "ebay_desc": "eBay price (high to low)",
    "ebay_asc": "eBay price (low to high)",
    "owned_desc": "Most owned",
    "secret_first": "Secrets first",
    "brand": "Brand",
    "series": "Series",
}

EBAY_LOW = "ebay_active_low"
EBAY_MEDIAN = "ebay_active_median"


def _ebay_prices(session: Session, item_ids: set[str]) -> dict[str, dict[str, CardPrice]]:
    """Latest eBay low/median per catalogue item.

    Same shape and caveat as the inventory view's: these are *active
    listing* asking prices, not sold comps -- eBay retired the sold API in
    Feb 2025 -- so they're an upper bound on what something moves for.
    """
    if not item_ids:
        return {}
    rows = session.scalars(
        select(CardPrice)
        .where(CardPrice.card_id.in_(item_ids), CardPrice.source.in_([EBAY_LOW, EBAY_MEDIAN]))
        .order_by(CardPrice.fetched_at.desc())
    )
    latest: dict[str, dict[str, CardPrice]] = {}
    for row in rows:
        bucket = latest.setdefault(row.card_id, {})
        if row.source not in bucket:  # ordered newest-first, so first wins
            bucket[row.source] = row
    return latest


@router.get("/collectibles", response_class=HTMLResponse)
def collectibles_page(
    sort: str = Query("name"),
    brand: str = Query(""),
    secret_only: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    sort_key = sort if sort in COLLECTIBLE_SORTS else "name"

    stmt = (
        select(CatalogItem, CollectibleProduct)
        .join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
    )
    if brand.strip():
        stmt = stmt.where(CollectibleProduct.brand.ilike(f"%{brand.strip()}%"))
    if secret_only:
        stmt = stmt.where(CollectibleProduct.is_secret.is_(True))
    rows = session.execute(stmt).all()

    owned_by_item = dict(session.execute(
        select(InventoryItem.catalog_item_id, func.sum(InventoryItem.quantity))
        .where(InventoryItem.catalog_item_id.isnot(None)).group_by(InventoryItem.catalog_item_id)
    ).all())
    prices = _ebay_prices(session, {item.id for item, _ in rows})

    def low_of(item_id: str) -> float | None:
        row = prices.get(item_id, {}).get(EBAY_LOW)
        return float(row.price) if row else None

    def median_of(item_id: str) -> float | None:
        row = prices.get(item_id, {}).get(EBAY_MEDIAN)
        return float(row.price) if row else None

    # Sorted in Python rather than SQL: the eBay figure is assembled from
    # the price history above, so it isn't a column to ORDER BY. Unpriced
    # items sort last in both directions rather than counting as zero,
    # which would put every unpriced figure at the top of "low to high".
    def price_key(pair, reverse: bool):
        value = median_of(pair[0].id)
        missing = 1  # always last
        if value is None:
            return (missing, 0.0)
        return (0, -value if reverse else value)

    orders = {
        "name": lambda r: sorted(r, key=lambda p: p[0].name.lower()),
        "brand": lambda r: sorted(r, key=lambda p: ((p[1].brand or "~").lower(), p[0].name.lower())),
        "series": lambda r: sorted(r, key=lambda p: ((p[1].series or "~").lower(), p[0].name.lower())),
        "owned_desc": lambda r: sorted(r, key=lambda p: (-(owned_by_item.get(p[0].id) or 0), p[0].name.lower())),
        "secret_first": lambda r: sorted(r, key=lambda p: (not p[1].is_secret, p[0].name.lower())),
        "ebay_desc": lambda r: sorted(r, key=lambda p: price_key(p, reverse=True)),
        "ebay_asc": lambda r: sorted(r, key=lambda p: price_key(p, reverse=False)),
    }
    rows = orders[sort_key](rows)

    def money(value: float | None) -> str:
        return "&mdash;" if value is None else f"${value:,.2f}"

    table_rows = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td>"
        f"<td class='muted'>{escape(cp.brand or '—')}</td>"
        f"<td class='muted opt'>{escape(cp.series or '—')}</td>"
        f"<td class='muted opt'>{escape(cp.variant or '—')}</td>"
        f"<td>{pill('secret', 'warn') if cp.is_secret else '—'}</td>"
        f"<td>{owned_by_item.get(item.id, 0)}</td>"
        f"<td class='num opt'>{money(low_of(item.id))}</td>"
        f"<td class='num strong'>{money(median_of(item.id))}</td>"
        f"<td class='num muted opt'>{f'${cp.retail_price:,.2f}' if cp.retail_price else '&mdash;'}</td>"
        "</tr>"
        for item, cp in rows
    ) or "<tr><td colspan=9>No collectibles match those filters.</td></tr>"

    priced_count = sum(1 for item, _ in rows if median_of(item.id) is not None)
    filters = (
        "<form class='panel filters' method='get' action='/collectibles'>"
        f"<label class='field'>Brand<input name='brand' value='{escape(brand)}' placeholder='Sonny Angel…'></label>"
        f"<label class='field'>Sort<select name='sort'>"
        + "".join(
            f"<option value='{escape(k)}'{' selected' if k == sort_key else ''}>{escape(v)}</option>"
            for k, v in COLLECTIBLE_SORTS.items()
        )
        + "</select></label>"
        f"<label class='field checkbox'><input type='checkbox' name='secret_only' value='1'"
        f"{' checked' if secret_only else ''}> Secrets only</label>"
        "<button type='submit'>Apply</button>"
        "<a class='btn ghost' href='/collectibles'>Reset</a>"
        "</form>"
    )

    table = (
        f"<p class='subtitle'>{len(rows)} catalogued · {priced_count} with an eBay price.</p>"
        + "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Name</th><th class='opt'>Brand</th><th class='opt'>Series</th>"
        "<th class='opt'>Variant</th><th></th>"
        "<th>Owned</th><th class='num opt'>eBay low</th><th class='num'>eBay median</th>"
        "<th class='num opt'>Retail</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div>"
        "<p class='muted' style='margin:10px 0 0;font-size:12.5px'>eBay figures are "
        "<strong>active listing</strong> asking prices, not completed sales &mdash; an upper "
        "bound, not a valuation.</p></div>"
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
          f"<a href='/collectibles/review'>Review queue ({pending_count})</a> &nbsp; "
          f"<a href='/collectibles/identify'>Identify from a link</a></p></div>"
        + filters + table + form + _COLLECTIBLE_LIST_STYLE + _STYLE
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
        + "<label>Photo<input type='file' name='photo' accept='image/*' required></label>"
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


def _figure_image(photo_path: Path, extracted, index: int) -> Path:
    """Crop one figure out of a shared photo for its review page, then
    clean up the background -- a cluttered counter or cabinet edge
    behind the figure reads as sloppy on a product page.

    A cabinet shelf holds forty figures. Without the crop, all forty
    review pages show the same wide shot and there is no way to tell
    which detected name belongs to which figure -- the same problem, and
    the same fix, as _card_front_image in capture.py. Falls back to the
    uncropped photo if cropping fails, and to the cropped-but-not-cleaned
    photo if background removal fails -- neither is worth failing a
    capture over.
    """
    if not getattr(extracted, "bounding_box", None):
        return photo_path
    cropped = photo_path.parent / f"figure{index}_{photo_path.name}"
    try:
        crop_card(photo_path, cropped, extracted.bounding_box)
    except Exception:
        return photo_path
    remove_background(cropped)
    return cropped


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
        for index, extracted in enumerate(extracted_list):
            values = {field: (None if field in extracted.unreadable_fields else getattr(extracted, field)) for field in CAPTURE_FIELDS}
            records.append(CapturedCollectible(
                id=str(uuid.uuid4()), image_path=str(_figure_image(photo_path, extracted, index)),
                ai_raw_response=extracted.model_dump(),
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


def _pending(session: Session) -> list[CapturedCollectible]:
    return session.scalars(
        select(CapturedCollectible)
        .where(CapturedCollectible.status == CardCaptureStatus.PENDING_REVIEW)
        .order_by(CapturedCollectible.captured_at, CapturedCollectible.id)
    ).all()


def _next_after(session: Session, current: CapturedCollectible | None) -> CapturedCollectible | None:
    """The next figure still awaiting a decision.

    Reviewing forty figures one page at a time only works if the next one
    arrives on its own -- going back to a list between every decision is
    most of the work. Wraps to the top so a skipped figure comes back
    round rather than being stranded at the end of the queue.
    """
    queue = _pending(session)
    if not queue:
        return None
    if current is None:
        return queue[0]
    ids = [c.id for c in queue]
    if current.id in ids:
        index = ids.index(current.id)
        return queue[(index + 1) % len(queue)] if len(queue) > 1 else None
    return queue[0]


def _queue_done_page() -> HTMLResponse:
    body = (
        brand_header("Collectible review queue")
        + "<div class='panel'><h2>Queue is empty</h2>"
          "<p>Everything captured so far has been confirmed or discarded.</p>"
          "<p><a class='btn' href='/collectibles'>Back to collectibles</a> "
          "<a class='btn ghost' href='/collectibles/capture'>Capture another</a></p></div>"
    )
    return HTMLResponse(page("EzBay — Collectible review", body))


@router.get("/collectibles/review", response_class=HTMLResponse)
def collectible_review_start(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Straight into the first figure rather than a list -- the queue is
    the point. The full list is still at /collectibles/review/all."""
    nxt = _next_after(session, None)
    if nxt is None:
        return _queue_done_page()
    return RedirectResponse(f"/collectibles/review/{nxt.id}", status_code=303)


@router.get("/collectibles/review/all", response_class=HTMLResponse)
def collectible_review_list(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = _pending(session)
    if not pending:
        return _queue_done_page()

    rows = "".join(
        f"<tr><td><a href='/collectibles/review/{escape(c.id)}'>{escape(c.character or c.brand or '(unidentified)')}</a></td>"
        f"<td class='muted'>{escape(c.brand or '—')}</td><td class='muted'>{escape(c.series or '—')}</td>"
        f"<td>{c.captured_at:%Y-%m-%d %H:%M}</td></tr>"
        for c in pending
    )
    body = (
        brand_header("Collectible review queue")
        + f"<p class='subtitle'>{len(pending)} awaiting review. "
          "<a class='btn' href='/collectibles/review'>Review one by one</a></p>"
        + f"<div class='panel'><div class='table-wrap'><table><thead><tr><th>Figure</th><th>Brand</th><th>Series</th><th>Captured</th></tr></thead>"
        + f"<tbody>{rows}</tbody></table></div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Collectible review", body))


@router.get("/collectibles/review/{capture_id}", response_class=HTMLResponse)
def collectible_review_form(capture_id: str, filled: str = Query(""), problem: str = Query(""),
                            user: str = Depends(require_dashboard_user),
                            session: Session = Depends(get_session)) -> HTMLResponse:
    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    fill_note = problem or (f"Filled from the link: {filled}." if filled else "")
    fill_kind = "bad" if problem else "ok"

    def field(name: str, label: str, value: str | None, autofocus: bool = False) -> str:
        safe_value = escape(value or "")
        focus = " autofocus" if autofocus else ""
        return f"<label>{label}<input name='{name}' value='{safe_value}'{focus}></label>"

    # Position in the queue, so a run of forty has a visible end.
    queue = _pending(session)
    ids = [c.id for c in queue]
    position = ids.index(capture_id) + 1 if capture_id in ids else 0
    remaining = len(queue)
    progress = (
        f"<p class='subtitle'>Figure {position} of {remaining} awaiting review · "
        "<a href='/collectibles/review/all'>see the whole queue</a></p>"
        if position else "<p class='subtitle'>Already reviewed.</p>"
    )
    bar = (
        f"<div class='revbar'><span style='width:{(position - 1) / remaining * 100:.1f}%'></span></div>"
        if position and remaining else ""
    )

    # The AI leaves character blank rather than guessing, so that's the
    # field the cursor belongs in -- it's the one being typed most often.
    focus_character = not (capture.character or "").strip()

    body = (
        brand_header("Review capture")
        + progress + bar
        + "<div class='panel review-one'>"
        + f"<a class='revshot' href='/collectibles/capture/image/{escape(capture_id)}' target='_blank' rel='noopener'>"
          f"<img src='/collectibles/capture/image/{escape(capture_id)}' alt='Captured figure'></a>"
        + (f"<p class='linkfill-note {escape(fill_kind)}'>{escape(fill_note)}</p>" if fill_note else "")
        + f"<form method='post' action='/collectibles/review/{escape(capture_id)}/from-link' class='linkfill'>"
          "<label>Fill from a product or listing link"
          f"<input name='url' type='url' placeholder='https://... the shop page for this figure' required></label>"
          "<button type='submit' class='ghost'>Fill fields</button></form>"
        + f"<form method='post' action='/collectibles/review/{escape(capture_id)}' class='calc-form'>"
        + field("brand", "Brand", capture.brand)
        + field("series", "Series", capture.series)
        + field("character", "Character", capture.character, autofocus=focus_character)
        + field("variant", "Variant", capture.variant)
        + field("blind_box_series", "Blind box series", capture.blind_box_series)
        + f"<label><input name='is_secret' type='checkbox' value='true' style='width:auto'{' checked' if capture.is_secret else ''}> Secret / chase figure</label>"
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


@router.post("/collectibles/review/{capture_id}/from-link")
def collectible_review_from_link(
    capture_id: str,
    url: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Fill a pending capture's fields from a shop or listing page.

    Reviewing a shelf photo, the AI often knows it's a Sonny Angel and
    nothing else -- the series name simply isn't visible on the figure.
    The product page is where that lives, so pasting its link beats typing
    the fields by hand.

    Writes only to the pending row, never the catalogue: the figure still
    has to be confirmed, and this just means confirming something already
    filled in. Fields the extractor wasn't sure about are left alone
    rather than blanked, so a link that only names the series can't wipe a
    character the photo already got right.
    """
    from .card_recognition import RecognitionError, extract_collectible_listing
    from .collectible_link import _strip_html
    from .link_fetch import FetchFailed, UnsafeUrl, fetch_text

    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")

    def back(**params) -> RedirectResponse:
        query = urlencode(params) if params else ""
        return RedirectResponse(
            f"/collectibles/review/{capture_id}" + (f"?{query}" if query else ""), status_code=303
        )

    settings = get_settings()
    if not settings.anthropic_api_key:
        return back(problem="No Anthropic API key is configured, so the link can't be read.")
    try:
        text = _strip_html(fetch_text(url))
    except (UnsafeUrl, FetchFailed) as exc:
        return back(problem=str(exc))

    try:
        found = extract_collectible_listing(text, api_key=settings.anthropic_api_key)
    except RecognitionError:
        return back(problem="That page was fetched but couldn't be read as a product listing.")
    except Exception:
        return back(problem="Reading that link failed -- it may be a login wall or a captcha.")
    if not found:
        return back(problem="That page doesn't look like a listing for a collectible figure.")

    extracted = found[0]
    unread = set(extracted.unreadable_fields or [])
    applied = []
    for attribute in ("brand", "series", "character", "variant"):
        if attribute in unread:
            continue
        value = (getattr(extracted, attribute) or "").strip()
        if value:
            setattr(capture, attribute, value)
            applied.append(attribute)
    # A listing has no separate blind-box field -- for a sealed box the
    # series IS the box line, which is the only case where it's known.
    if extracted.is_sealed and (extracted.series or "").strip() and "series" not in unread:
        capture.blind_box_series = extracted.series.strip()
        applied.append("blind_box_series")
    if "is_secret" not in unread:
        capture.is_secret = bool(extracted.is_secret)
        applied.append("is_secret")

    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="collectible.fill_from_link",
        resource_type="captured_collectible", resource_id=capture_id,
        outcome="success" if applied else "no_change",
        correlation_id=str(uuid.uuid4()), details={"fields": applied},
    )
    if not applied:
        return back(problem="Nothing on that page was clear enough to fill in.")
    return back(filled=", ".join(applied))


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

    def onward() -> RedirectResponse:
        """Straight to the next figure, or the done page when the queue
        empties -- never back to a list mid-run."""
        nxt = _next_after(session, capture)
        if nxt is None:
            return RedirectResponse("/collectibles/review", status_code=303)
        return RedirectResponse(f"/collectibles/review/{nxt.id}", status_code=303)

    if action == "skip":
        # Deliberately writes nothing: a skip is "not now", not a decision,
        # so the row stays pending and comes back round on the next pass.
        return onward()

    if action == "reject":
        capture.status = CardCaptureStatus.REJECTED
        capture.reviewed_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="collectible.reject", resource_type="captured_collectible", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={})
        return onward()

    name = character.strip() or brand.strip() or "Unnamed collectible"
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(
        catalog_item_id=item_id, brand=brand or None, series=series or None, character=character or None,
        variant=variant or None, blind_box_series=blind_box_series or None, is_secret=bool(is_secret),
    ))
    qty = _int(quantity, default=1)
    if qty > 0:
        # The crop made during capture IS a photo of the actual figure,
        # not reference art -- carrying it forward is what lets the shop
        # show the real item instead of "no image" the way TCG scans
        # already do via ShopListing's own-photo preference.
        session.add(InventoryItem(catalog_item_id=item_id, card_id=None, variant=CardVariant.NORMAL,
                                  condition="New", quantity=qty, scan_image_path=capture.image_path))
    capture.status = CardCaptureStatus.REVIEWED
    capture.reviewed_at = datetime.now(UTC)
    capture.catalog_item_id = item_id
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="collectible.confirm", resource_type="captured_collectible", resource_id=capture_id, outcome="success", correlation_id=str(uuid.uuid4()), details={"catalog_item_id": item_id})
    return onward()


@router.get("/collectibles/capture/image/{capture_id}")
def collectible_capture_image(capture_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    capture = session.get(CapturedCollectible, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    return FileResponse(capture.image_path)


_COLLECTIBLE_LIST_STYLE = """<style>
.filters{display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap}
.filters .field{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.filters .field.checkbox{flex-direction:row;align-items:center;gap:7px}
.filters .field.checkbox input{width:auto}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.num.strong{font-weight:600}
/* A nine-column table on a phone puts the prices past the right edge,
   where they may as well not exist -- this is a list you read on mobile.
   Drop to what the question actually needs: which figure, is it a secret,
   how many, what's it worth. */
@media (max-width:760px){
  .opt{display:none}
  table{white-space:normal}
  td:first-child{font-weight:600}
}
</style>"""

_REVIEW_STYLE = """<style>
.revbar{height:5px;border-radius:99px;background:var(--surface-sunken);overflow:hidden;margin:0 0 18px}
.revbar span{display:block;height:100%;background:var(--accent);transition:width .2s ease}
.review-one{display:grid;gap:24px;grid-template-columns:minmax(200px,300px) 1fr;align-items:start}
.revshot{display:block;border-radius:10px;overflow:hidden;background:var(--surface-sunken);
  border:1px solid var(--panel-border)}
.revshot img{width:100%;display:block}
.revactions{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.revactions .danger{color:var(--danger);border-color:var(--danger)}
.linkfill{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin:0 0 16px}
.linkfill label{display:flex;flex-direction:column;gap:5px;flex:1;min-width:220px;
  font-size:13px;color:var(--text-dim)}
.linkfill-note{margin:0 0 12px;padding:10px 13px;border-radius:8px;font-size:13.5px}
.linkfill-note.ok{background:var(--success-bg);color:var(--success)}
.linkfill-note.bad{background:var(--warning-bg);color:var(--warning)}
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
