"""Turn scripts/audit_inventory_photos.py's hourly findings into something a
human actually works through, instead of a log file nobody reads.

That script has been flagging real mismatches (2026-09-10: 237 of 1,619
items, ~15% of the catalogue) since inventory-audit.timer started running
it -- confirmed live against a "151 Tangela #178" listing whose stored
photo was an unrelated, uncropped multi-card sheet. Nothing was surfacing
that backlog anywhere a person would see it. This page shows the single
most-confident mismatch, lets a human either confirm it's a false positive
(a foil or edge-crop that legitimately hashes far from its own reference --
see that script's docstring) or delete the bad listing/item outright, and
moves to the next one. Mirrors /foil-review's tap-through shape.

find_mismatches hashes every item's photo against ~20k reference cards --
~28s confirmed live at 1,619 items. Recomputing that on every page load (and
every action redirects back to one) made the page feel broken, so results
are cached to a file exactly like foil_review.py's candidate cache, with
its own "Rescan" button rather than a silent background refresh.
"""

import json
import uuid
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .images import serve_image
from .inventory_audit import find_mismatches
from .models import CardSet, CatalogCard, InventoryItem, PhotoAuditDismissal, ShopListing
from .scan_ingest.commit import _image_quality, _store_scan_image
from .ui import brand_header, page

router = APIRouter(prefix="/inventory-audit", tags=["inventory-audit"])


def _cache_path() -> Path:
    return Path(get_settings().scan_media_dir) / "inventory_audit_cache.json"


def _save_mismatches(mismatches: list[dict]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mismatches))


def _load_or_compute(session: Session) -> list[dict]:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    mismatches = find_mismatches(session)
    _save_mismatches(mismatches)
    return mismatches


def _dismissed_item_ids(session: Session) -> set[str]:
    return set(session.scalars(select(PhotoAuditDismissal.inventory_item_id)))


def _pending(session: Session) -> list[dict]:
    """Cached candidates, filtered live against current DB state -- a
    dismissal, a delete, or a stock-photo swap all take effect immediately
    without needing a full ~28s rescan, since checking whether an item
    still exists / still has its flagged photo is a cheap per-row lookup,
    unlike recomputing every hash from scratch."""
    dismissed = _dismissed_item_ids(session)
    pending = []
    for m in _load_or_compute(session):
        if m["item_id"] in dismissed:
            continue
        item = session.get(InventoryItem, m["item_id"])
        if item is None or item.scan_image_path != m.get("_scan_image_path"):
            continue
        pending.append(m)
    return pending


@router.get("", response_class=HTMLResponse)
def inventory_audit_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = _pending(session)
    dismissed_count = session.query(PhotoAuditDismissal).count()

    header = (
        brand_header("Inventory photo audit")
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Flagged, awaiting review</div><div class='value'>{len(pending)}</div></div>"
        + f"<div class='stat-card'><div class='label'>Dismissed as false positives</div><div class='value'>{dismissed_count}</div></div>"
        + "</div>"
        + "<div class='toolbar'><form method='post' action='/inventory-audit/rescan'>"
          "<button class='chip' type='submit'>Rescan inventory (~30s)</button></form></div>"
    )

    if not pending:
        body = header + "<div class='panel'><p>Nothing flagged right now.</p></div>"
        return HTMLResponse(page("EzBay — Inventory photo audit", body + _STYLE))

    m = pending[0]
    thumb = f"<img src='/inventory-audit/image/{escape(m['item_id'])}' alt='' loading='lazy' decoding='async'>"

    # The refile form used to make a human retype the set code and number
    # by hand off the photo, even though "looks like" is exactly that
    # answer already sitting in the mismatch row -- so pre-fill it as a
    # one-click confirm rather than a blank form next to its own answer.
    # Still editable, and still requires the click: this never auto-submits.
    looks_like_card = session.get(CatalogCard, m["looks_like_id"])
    suggested_set_code = (looks_like_card.card_set.code or looks_like_card.set_id) if looks_like_card and looks_like_card.card_set else ""
    suggested_number = looks_like_card.number if looks_like_card else ""

    body = (
        header
        + "<div class='audit-card'>"
        + f"<a class='audit-face' href='/inventory-audit/image/{escape(m['item_id'])}' target='_blank' rel='noopener'>{thumb}</a>"
        + f"<div class='audit-row'><span class='audit-tag'>filed as</span>{escape(m['filed_label'])}"
          f"<span class='audit-d'>d{m['own_distance']}</span></div>"
        + f"<div class='audit-row'><span class='audit-tag'>looks like</span>{escape(m['looks_like_label'])}"
          f"<span class='audit-d'>d{m['looks_like_distance']}</span></div>"
        + "<div class='audit-prompt'>What should happen to this listing?</div>"
        + "<div class='audit-actions'>"
        + "<form method='post' action='/inventory-audit/dismiss'>"
        + f"<input type='hidden' name='item_id' value='{escape(m['item_id'])}'>"
        + f"<input type='hidden' name='own_distance' value='{m['own_distance']}'>"
        + "<button class='audit-btn audit-btn-keep' type='submit'>Photo's fine, keep it</button></form>"
        + (
            "<form method='post' action='/inventory-audit/use-stock-photo'>"
            + f"<input type='hidden' name='item_id' value='{escape(m['item_id'])}'>"
            + "<button class='audit-btn audit-btn-stock' type='submit'>Replace with stock photo</button></form>"
            if m["reference_image_url"] else ""
        )
        + "<form method='post' action='/inventory-audit/delete' "
          "onsubmit=\"return confirm('Delete this listing and inventory item? This cannot be undone here.');\">"
        + f"<input type='hidden' name='item_id' value='{escape(m['item_id'])}'>"
        + "<button class='audit-btn audit-btn-delete' type='submit'>Delete listing &amp; item</button></form>"
        + "</div>"
        + "<div class='audit-refile'>"
        + "<div class='audit-prompt'>Or, if it's the wrong card entirely -- re-file it</div>"
        + "<form method='post' action='/inventory-audit/refile' class='audit-refile-form'>"
        + f"<input type='hidden' name='item_id' value='{escape(m['item_id'])}'>"
        + f"<input name='set_code' value='{escape(suggested_set_code)}' placeholder='Set code, e.g. WHT' required>"
        + f"<input name='number' value='{escape(suggested_number)}' placeholder='Number, e.g. 055' required>"
        + "<button class='audit-btn audit-btn-refile' type='submit'>Re-file to this card</button>"
        + "</form>"
        + ("<p class='audit-refile-hint'>Pre-filled from &ldquo;looks like&rdquo; above -- check it against "
           "the photo before confirming. This drops any listing built for the wrong card and re-owns the "
           "item under the right one, merging into an existing holding if you already have one.</p>"
           if suggested_set_code else
           "<p class='audit-refile-hint'>Read the set code and number straight off the photo -- "
           "this drops any listing built for the wrong card and re-owns the item under the right one, "
           "merging into an existing holding of that card if you already have one.</p>")
        + "</div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Inventory photo audit", body))


@router.post("/rescan")
def inventory_audit_rescan(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    _save_mismatches(find_mismatches(session))
    return RedirectResponse("/inventory-audit", status_code=303)


@router.post("/dismiss")
def inventory_audit_dismiss(
    item_id: str = Form(...),
    own_distance: int = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    existing = session.scalar(select(PhotoAuditDismissal).where(PhotoAuditDismissal.inventory_item_id == item_id))
    if existing is None:
        session.add(PhotoAuditDismissal(
            id=str(uuid.uuid4()), inventory_item_id=item_id,
            own_distance_at_dismissal=own_distance, dismissed_by=user,
        ))
        session.commit()
    return RedirectResponse("/inventory-audit", status_code=303)


@router.post("/use-stock-photo")
def inventory_audit_use_stock_photo(
    item_id: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Clears the item's local scan photo -- typically an uncropped or
    mis-split sheet region, per the reason this landed in the queue -- so
    both this page and the storefront (which already prefers publisher
    reference art over a seller's own scan, see storefront/common.py::_art)
    show the correct card instead of the wrong crop. Keeps the inventory
    item and listing themselves untouched: the card is still genuinely
    owned, only its photo was wrong."""
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inventory item not found")

    item.scan_image_path = None
    dismissal = session.scalar(select(PhotoAuditDismissal).where(PhotoAuditDismissal.inventory_item_id == item_id))
    if dismissal is not None:
        session.delete(dismissal)
    session.commit()
    return RedirectResponse("/inventory-audit", status_code=303)


@router.post("/refile")
def inventory_audit_refile(
    item_id: str = Form(...),
    set_code: str = Form(...),
    number: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """The photo can be perfectly correct while the *identification* was
    confidently wrong -- see scripts/audit_inventory_photos.py's own
    docstring for a prior real case (a Joltik committed as a Gengar). Photo
    audit's other actions all assume the photo is the problem; this is the
    one for when the card it's filed under is. Reuses the same
    merge-or-create logic /foil-review's variant correction already uses:
    if the correct card is already held in this variant/condition, fold
    this item's quantity and (if better) photo into it rather than leaving
    a duplicate row.

    Any listing built for the wrong card is dropped outright rather than
    silently re-pointed -- its title and price were both set for a
    different card, so it needs a fresh listing decision, not a patch."""
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inventory item not found")

    card_set = session.scalar(select(CardSet).where(CardSet.code == set_code.strip().upper()))
    if card_set is None:
        raise HTTPException(status_code=400, detail=f"no set found with code '{set_code}'")

    number = number.strip()
    correct_card = session.scalar(
        select(CatalogCard).where(CatalogCard.set_id == card_set.id, CatalogCard.number == number)
    )
    if correct_card is None and number.lstrip("0"):
        correct_card = session.scalar(
            select(CatalogCard).where(CatalogCard.set_id == card_set.id, CatalogCard.number == number.lstrip("0"))
        )
    if correct_card is None:
        raise HTTPException(status_code=400, detail=f"no card found for {set_code} #{number}")

    for listing in session.scalars(select(ShopListing).where(ShopListing.inventory_item_id == item_id)):
        session.delete(listing)

    existing = session.scalar(
        select(InventoryItem).where(
            InventoryItem.card_id == correct_card.id,
            InventoryItem.variant == item.variant,
            InventoryItem.condition == item.condition,
            InventoryItem.id != item.id,
        )
    )
    media_dir = Path(get_settings().scan_media_dir)
    set_code_for_filename = card_set.code or card_set.id
    if existing is not None:
        existing.quantity += item.quantity
        if item.scan_image_path and (
            not existing.scan_image_path
            or _image_quality(Path(item.scan_image_path)) > _image_quality(Path(existing.scan_image_path))
        ):
            stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code_for_filename,
                                        correct_card.number, item.variant, item.condition)
            existing.scan_image_path = str(stored)
        session.delete(item)
    else:
        if item.scan_image_path:
            stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code_for_filename,
                                        correct_card.number, item.variant, item.condition)
            item.scan_image_path = str(stored)
        item.card_id = correct_card.id
        item.catalog_item_id = correct_card.id

    dismissal = session.scalar(select(PhotoAuditDismissal).where(PhotoAuditDismissal.inventory_item_id == item_id))
    if dismissal is not None:
        session.delete(dismissal)
    session.commit()
    return RedirectResponse("/inventory-audit", status_code=303)


@router.post("/delete")
def inventory_audit_delete(
    item_id: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inventory item not found")

    for listing in session.scalars(select(ShopListing).where(ShopListing.inventory_item_id == item_id)):
        session.delete(listing)
    dismissal = session.scalar(select(PhotoAuditDismissal).where(PhotoAuditDismissal.inventory_item_id == item_id))
    if dismissal is not None:
        session.delete(dismissal)
    session.delete(item)
    session.commit()
    return RedirectResponse("/inventory-audit", status_code=303)


@router.get("/image/{item_id}")
def inventory_audit_image(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    return serve_image(
        item.scan_image_path if item else None,
        unset="no photo for that item",
        missing="photo file missing",
    )


_STYLE = """<style>
.toolbar{display:flex;gap:10px;align-items:center;margin:14px 0 22px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;padding:10px 16px;border-radius:999px;font-size:14px;
  border:1px solid var(--panel-border);color:var(--text-dim);background:var(--surface-sunken);cursor:pointer}
.chip:hover{border-color:var(--accent);color:var(--accent)}

.audit-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:16px;
  padding:20px;display:flex;flex-direction:column;gap:10px;max-width:480px;margin:0 auto}
.audit-face{display:block;border-radius:12px;overflow:hidden;background:var(--surface-sunken);
  aspect-ratio:5/7;max-height:56vh;margin:0 auto}
.audit-face img{width:100%;height:100%;object-fit:contain;display:block}
.audit-row{display:flex;align-items:center;gap:8px;font-size:14px}
.audit-tag{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
  color:var(--text-dim);background:var(--surface-sunken);padding:2px 7px;border-radius:999px;flex-shrink:0}
.audit-d{margin-left:auto;font-family:monospace;font-size:12px;color:var(--text-dim)}
.audit-prompt{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);
  text-align:center;margin-top:6px}
.audit-actions{display:flex;gap:10px;flex-wrap:wrap}
.audit-actions form{flex:1 1 140px}
.audit-btn{width:100%;padding:14px 10px;border-radius:10px;font-size:14px;font-weight:700;
  cursor:pointer;border:1px solid var(--panel-border);background:none;color:var(--text)}
.audit-btn-keep:hover{border-color:var(--accent-2);color:var(--accent-2)}
.audit-btn-stock:hover{border-color:var(--accent);color:var(--accent)}
.audit-btn-delete{color:#e5484d;border-color:#e5484d55}
.audit-btn-delete:hover{background:#e5484d15}

.audit-refile{border-top:1px solid var(--panel-border);padding-top:14px;margin-top:4px}
.audit-refile-form{display:flex;gap:8px;flex-wrap:wrap}
.audit-refile-form input[type=text],.audit-refile-form input:not([type]){
  flex:1 1 120px;padding:10px 12px;border-radius:8px;border:1px solid var(--panel-border);
  background:var(--surface-sunken);color:var(--text);font-size:14px;
}
.audit-btn-refile{flex:1 1 160px;border-color:var(--accent);color:var(--accent)}
.audit-btn-refile:hover{background:var(--accent);color:var(--panel)}
.audit-refile-hint{font-size:12px;color:var(--text-dim);margin:8px 0 0}
</style>"""
