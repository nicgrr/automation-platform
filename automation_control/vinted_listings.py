"""Post collectibles to Vinted from the CRM, and track them once they're live.

Vinted has no listing API for personal sellers and its terms forbid
automated posting, so the operator posts each item in the Vinted app. This
does the slow parts around that: a phone screen with the item's photos ready
to save to the camera roll in one tap, the title, description and price with
copy buttons, then a box to paste the Vinted item link back. Recording the
link reserves one unit of stock, as a Whatnot queue does. "Sold on Vinted"
consumes it and records a Sale; "Remove" puts it back.

Drafts (title, description, confirmed price) come from vinted_prep.py.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CatalogItem,
    InventoryItem,
    InventoryStatus,
    Marketplace,
    Sale,
    SaleItem,
    VintedListing,
    VintedListingStatus,
)
from .ui import brand_header, page
from .vinted_prep import _draft, _get_row, _status

router = APIRouter(prefix="/collectibles/vinted", tags=["vinted-listings"])

_ITEM_PATH = re.compile(r"^/items/(\d{4,20})(?:[-/]|$)")
_VINTED_DOMAINS = {
    "vinted.com", "vinted.com.au", "vinted.au", "vinted.co.uk", "vinted.fr",
    "vinted.de", "vinted.it", "vinted.es", "vinted.pt", "vinted.nl", "vinted.be",
    "vinted.at", "vinted.ie", "vinted.lu", "vinted.pl", "vinted.cz", "vinted.sk",
    "vinted.lt", "vinted.lv", "vinted.ee", "vinted.dk", "vinted.se", "vinted.fi",
    "vinted.hr", "vinted.hu", "vinted.ro", "vinted.gr", "vinted.si", "vinted.ca",
}


def parse_vinted_url(raw: str) -> tuple[str, str] | None:
    """(item id, clean URL) for a Vinted item link, else None.

    Accepts the share link the Vinted app gives (https://www.vinted.com.au/
    items/1234567890-title?referrer=...) on any Vinted country domain.
    """
    raw = (raw or "").strip()
    match = re.search(r"https?://\S+", raw)
    if not match:
        return None
    try:
        parts = urlsplit(match.group(0))
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    domain = host.removeprefix("www.")
    if (domain not in _VINTED_DOMAINS or parts.username is not None
            or parts.password is not None or port not in (None, 80, 443)):
        return None
    found = _ITEM_PATH.match(parts.path)
    if not found:
        return None
    return found.group(1), f"https://{host}/items/{found.group(1)}"


def _money(raw: str | Decimal, label: str, *, allow_zero: bool = False) -> Decimal:
    """Validate before reserving stock or writing a sale, including direct callers."""
    try:
        amount = Decimal(raw)
        valid = (amount.is_finite() and amount >= 0 if allow_zero else
                 amount.is_finite() and amount > 0)
        if not valid or amount > Decimal("99999999.99") or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        sign = "nonnegative" if allow_zero else "positive"
        raise HTTPException(status_code=400, detail=f"Enter a valid {sign} {label} in AUD (up to two decimal places).") from None
    return amount


def _marketplace(session: Session) -> Marketplace:
    market = session.scalar(select(Marketplace).where(func.lower(Marketplace.name) == "vinted"))
    if market is None:
        raise HTTPException(status_code=500, detail="the Vinted marketplace record is missing; run migrations")
    return market


def _row_or_none(session: Session, key: str):
    """The prep row, or None once its last unit is reserved (the prep page
    lists only collectibles with stock left, so a live item drops off it)."""
    try:
        return _get_row(session, key)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


_NO_STOCK_LEFT = RedirectResponse(
    f"/collectibles/vinted/live?_saved={quote('Already live: no unlisted stock left')}", status_code=303)


def _copy(label: str, value: str, field_id: str, multiline: bool = False) -> str:
    body = (f"<textarea id='{field_id}' readonly rows='6'>{escape(value)}</textarea>" if multiline else
            f"<input id='{field_id}' readonly value='{escape(value, quote=True)}'>")
    return (f"<div class='copy-row'><label for='{field_id}'>{escape(label)}</label>{body}"
            f"<button type='button' class='btn small' data-copy='{field_id}'>Copy</button></div>")


@router.get("/{key}/post", response_class=HTMLResponse)
def post_screen(key: str, error: str = "", user: str = Depends(require_dashboard_user),
                session: Session = Depends(get_session)):
    row = _row_or_none(session, key)
    if row is None:
        return RedirectResponse(_NO_STOCK_LEFT.headers["location"], status_code=303)
    draft = _draft(session, row)
    inventory = row["inventory"]
    live = session.scalars(select(VintedListing).where(
        VintedListing.inventory_item_id == (inventory.id if inventory else None),
        VintedListing.status == VintedListingStatus.ACTIVE)).all() if inventory else []
    blockers = []
    if inventory is None:
        blockers.append("This photo isn't linked to stock yet. Review its identity and quantity first.")
    elif inventory.quantity < 1 or inventory.status != InventoryStatus.AVAILABLE:
        blockers.append("No unlisted stock left for this item.")
    if not draft.get("confirmed_price"):
        blockers.append("Confirm a price on the Vinted preparation page first.")
    review_notes = [note for note in _status(row, draft) if note != "Price needs your confirmation."]

    photos = "".join(
        f"<img src='/collectibles/vinted/{escape(key)}/photo/{i}' alt='Photo {i + 1}' data-photo='{i}'>"
        for i in range(len(row["paths"])))
    brand = row["brand"] or ""
    lines = [
        _copy("Title", draft["title"], "v-title"),
        _copy("Description", draft["description"], "v-desc", multiline=True),
        _copy("Price (AUD)", str(draft.get("confirmed_price") or ""), "v-price"),
    ]
    hints = ("<ul class='hints'>"
             + (f"<li><b>Brand:</b> {escape(brand)}</li>" if brand else "")
             + "<li><b>Category:</b> pick the closest figures/collectibles category in the drop-down</li>"
             + "<li><b>Condition:</b> match the description (opened/used figures are not \"New with tags\")</li>"
             + "<li><b>Parcel size:</b> small for a single figure</li></ul>")
    already = "".join(
        f"<p class='callout'>Already live on Vinted: <a href='{escape(l.url, quote=True)}' target='_blank' "
        f"rel='noopener'>{escape(l.title)}</a> · ${l.price}</p>" for l in live)
    body = (
        brand_header("Post to Vinted")
        + f"<h2 class='item-name'>{escape(row['name'])}</h2>"
        + (f"<div class='callout bad'>{escape(error)}</div>" if error else "")
        + "".join(f"<div class='callout warn'>{escape(b)}</div>" for b in blockers)
        + ("<div class='callout warn'><b>Review before publishing:</b><ul>"
           + "".join(f"<li>{escape(note)}</li>" for note in review_notes)
           + "</ul><p>If you already posted the item, record its link below so stock stays accurate. "
           "Recording the link does not clear these review notes.</p></div>" if review_notes else "")
        + already
        + "<ol class='steps'>"
        + "<li><h3>Save the photos</h3>"
        + f"<div class='photos'>{photos}</div>"
        + "<p class='photo-actions'><button type='button' class='btn' id='share-photos'>Save all photos</button> "
        + f"<a class='btn ghost' href='/collectibles/vinted/{escape(key)}/photos.zip'>Download photos</a></p>"
        + "<small id='share-help'>On your phone, <b>Save all photos</b> opens the share sheet: choose "
        + "<b>Save Images</b>. On a computer, download them and use Vinted's <b>Upload photos</b>.</small></li>"
        + "<li><h3>Create the listing in the Vinted app</h3>"
        + "<p><a class='btn ghost' href='https://www.vinted.com.au/items/new' target='_blank' rel='noopener'>Open Vinted</a></p>"
        + "".join(lines) + hints + "</li>"
        + "<li><h3>Paste the Vinted link here</h3>"
        + "<p>In the app, open your new listing, tap <b>Share</b>, then <b>Copy link</b>.</p>"
        + f"<form method='post' action='/collectibles/vinted/{escape(key)}/posted' class='posted'>"
        + "<input name='url' inputmode='url' required placeholder='https://www.vinted.com.au/items/…'>"
        + f"<input type='hidden' name='price' value='{escape(str(draft.get('confirmed_price') or ''), quote=True)}'>"
        + "<button class='btn' " + ("disabled" if blockers else "") + ">Mark as live on Vinted</button></form>"
        + "<small>This reserves one unit of stock so it can't sell anywhere else.</small></li></ol>"
        + "<p><a class='btn ghost' href='/collectibles/vinted'>Back to Vinted preparation</a> "
        + "<a class='btn ghost' href='/collectibles/vinted/live'>Live on Vinted</a></p>"
        + _SCRIPT + _STYLE
    )
    return HTMLResponse(page("EzBay — Post to Vinted", body))


@router.get("/{key}/photos.zip")
def photos_zip(key: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """This item's photos, in listing order, for Vinted's desktop Upload photos."""
    row = _get_row(session, key)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as z:
        for i, raw in enumerate(row["paths"]):
            path = Path(raw)
            if path.is_file():
                z.write(path, f"{i + 1}-{re.sub(r'[^A-Za-z0-9]+', '-', row['name']).strip('-')[:40]}{path.suffix}")
    name = re.sub(r"[^A-Za-z0-9]+", "-", row["name"]).strip("-")[:40] or "item"
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="vinted-{name}.zip"', "Cache-Control": "no-store"})


@router.post("/{key}/posted")
def mark_posted(key: str, url: str = Form(...), price: str = Form(""),
                user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    row = _row_or_none(session, key)
    if row is None:
        return RedirectResponse(_NO_STOCK_LEFT.headers["location"], status_code=303)
    draft = _draft(session, row)
    parsed = parse_vinted_url(url)

    def back(message: str) -> RedirectResponse:
        return RedirectResponse(f"/collectibles/vinted/{key}/post?error={quote(message)}", status_code=303)

    if parsed is None:
        return back("That doesn't look like a Vinted item link. In the app use Share → Copy link.")
    if row["inventory"] is None:
        return back("Link this photo to stock before posting.")
    try:
        amount = _money(price or draft.get("confirmed_price") or "", "listing price")
    except HTTPException as exc:
        return back(exc.detail)
    item_id, clean_url = parsed
    inventory_id = row["inventory"].id
    reserved = session.execute(
        update(InventoryItem)
        .where(InventoryItem.id == inventory_id, InventoryItem.status == InventoryStatus.AVAILABLE,
               InventoryItem.quantity >= 1)
        .values(quantity=InventoryItem.quantity - 1)
        .execution_options(synchronize_session=False)
    )
    if reserved.rowcount != 1:
        session.rollback()
        return back("No unlisted stock left for this item.")
    inventory = session.get(InventoryItem, inventory_id)
    session.refresh(inventory)
    if inventory.quantity == 0:
        inventory.status = InventoryStatus.RESERVED
    now = datetime.now(UTC)
    listing = VintedListing(
        inventory_item_id=inventory_id, catalog_item_id=inventory.catalog_item_id, vinted_item_id=item_id,
        url=clean_url, title=draft["title"][:256], price=amount, currency="AUD", source="manual",
        posted_at=now, stock_reserved_at=now,
    )
    session.add(listing)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return back("That Vinted item is already recorded.")
    record_event(session, actor_type="user", actor_id=user, action="vinted.listing.posted",
                 resource_type="vinted_listing", resource_id=listing.id, outcome="success",
                 correlation_id=str(uuid.uuid4()),
                 details={"vinted_item_id": item_id, "inventory_item_id": inventory_id, "price": str(amount)})
    return RedirectResponse(f"/collectibles/vinted/live?_saved={quote('Live on Vinted')}", status_code=303)


@router.get("/live", response_class=HTMLResponse)
def live_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    rows = session.scalars(select(VintedListing).order_by(
        VintedListing.status, VintedListing.posted_at.desc())).all()
    cards = []
    for l in rows:
        name = session.get(CatalogItem, l.catalog_item_id).name if l.catalog_item_id else l.title
        actions = ""
        if l.status == VintedListingStatus.ACTIVE:
            actions = (
                f"<form method='post' action='/collectibles/vinted/listing/{l.id}/sold' class='inline'>"
                f"<label>Sold for (AUD)<input name='price' type='number' step='0.01' min='0.01' value='{l.price}'></label>"
                "<label>Fees (AUD)<input name='fees' type='number' step='0.01' min='0' value='0'></label>"
                "<button class='btn small'>Sold on Vinted</button></form>"
                f"<form method='post' action='/collectibles/vinted/listing/{l.id}/remove' class='inline'>"
                "<button class='btn small ghost'>Removed from Vinted</button></form>")
        cards.append(
            f"<article class='panel vinted-live {l.status.value}'><h3>{escape(name)}</h3>"
            f"<p><a href='{escape(l.url, quote=True)}' target='_blank' rel='noopener'>{escape(l.title)}</a></p>"
            f"<p class='muted'>{l.status.value.title()} · ${l.price} · posted {l.posted_at:%d %b}"
            + (f" · sold {l.sold_at:%d %b}" if l.sold_at else "") + f"</p>{actions}</article>")
    body = (brand_header("Live on Vinted")
            + "<p><a class='btn ghost' href='/collectibles/vinted'>Vinted preparation</a></p>"
            + ("".join(cards) or "<p>Nothing posted yet. Use <b>Post to Vinted</b> on the preparation page.</p>")
            + _STYLE)
    return HTMLResponse(page("EzBay — Live on Vinted", body))


def _active(session: Session, listing_id: str) -> VintedListing:
    listing = session.get(VintedListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Vinted listing not found")
    return listing


@router.post("/listing/{listing_id}/sold")
def mark_sold(listing_id: str, price: str = Form(...), fees: str = Form("0"),
              user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    listing = _active(session, listing_id)
    if listing.status != VintedListingStatus.ACTIVE:
        return RedirectResponse("/collectibles/vinted/live", status_code=303)
    amount = _money(price, "sale price")
    fee = _money(fees or "0", "fee", allow_zero=True)
    record_vinted_sale(session, listing, amount, fee, actor=user)
    return RedirectResponse(f"/collectibles/vinted/live?_saved={quote('Sale recorded')}", status_code=303)


def record_vinted_sale(session: Session, listing: VintedListing, amount: Decimal, fee: Decimal, *,
                       actor: str, email_id: str | None = None) -> Sale:
    """Consume the reserved unit and record the Sale (shared with the sale-email reader)."""
    amount = _money(amount, "sale price")
    fee = _money(fee, "fee", allow_zero=True)
    claimed = session.execute(
        update(VintedListing).where(VintedListing.id == listing.id,
                                    VintedListing.status == VintedListingStatus.ACTIVE)
        .values(status=VintedListingStatus.SOLD).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="this Vinted listing is no longer active")
    session.refresh(listing)
    inventory = session.scalar(select(InventoryItem).where(InventoryItem.id == listing.inventory_item_id)
                               .with_for_update().execution_options(populate_existing=True)) if listing.inventory_item_id else None
    if inventory is not None and inventory.quantity == 0:
        another_reserved = session.scalar(select(VintedListing.id).where(
            VintedListing.inventory_item_id == inventory.id,
            VintedListing.status == VintedListingStatus.ACTIVE).limit(1))
        inventory.status = InventoryStatus.RESERVED if another_reserved else InventoryStatus.SOLD
    sale = Sale(id=str(uuid.uuid4()), marketplace_id=_marketplace(session).id, source="vinted",
                gross_amount=amount, fees_amount=fee,
                notes=f"Vinted item {listing.vinted_item_id}" + (f"; from sale email {email_id}" if email_id else ""))
    session.add(sale)
    session.flush()
    session.add(SaleItem(id=str(uuid.uuid4()), sale_id=sale.id, inventory_item_id=listing.inventory_item_id,
                         description=listing.title[:256], quantity=1, unit_price=amount,
                         cost_basis=inventory.purchase_cost_basis if inventory is not None else None))
    listing.sale_id, listing.sold_at, listing.sale_email_id = sale.id, datetime.now(UTC), email_id
    session.commit()
    record_event(session, actor_type="user", actor_id=actor, action="vinted.listing.sold",
                 resource_type="vinted_listing", resource_id=listing.id, outcome="success",
                 correlation_id=str(uuid.uuid4()),
                 details={"sale_id": sale.id, "price": str(amount), "fees": str(fee), "email_id": email_id})
    return sale


@router.post("/listing/{listing_id}/remove")
def mark_removed(listing_id: str, user: str = Depends(require_dashboard_user),
                 session: Session = Depends(get_session)):
    listing = _active(session, listing_id)
    claimed = session.execute(
        update(VintedListing).where(VintedListing.id == listing.id,
                                    VintedListing.status == VintedListingStatus.ACTIVE)
        .values(status=VintedListingStatus.REMOVED).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        session.rollback()
        return RedirectResponse("/collectibles/vinted/live", status_code=303)
    session.refresh(listing)
    if listing.inventory_item_id and listing.stock_reserved_at and not listing.stock_released_at:
        released = session.execute(update(InventoryItem).where(InventoryItem.id == listing.inventory_item_id)
                                   .values(quantity=InventoryItem.quantity + 1)
                                   .execution_options(synchronize_session=False))
        inventory = session.get(InventoryItem, listing.inventory_item_id)
        if released.rowcount and inventory is not None:
            session.refresh(inventory)
            if inventory.status in (InventoryStatus.RESERVED, InventoryStatus.SOLD):
                inventory.status = InventoryStatus.AVAILABLE
        listing.stock_released_at = datetime.now(UTC)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="vinted.listing.removed",
                 resource_type="vinted_listing", resource_id=listing.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"vinted_item_id": listing.vinted_item_id})
    return RedirectResponse(f"/collectibles/vinted/live?_saved={quote('Back in stock')}", status_code=303)


_SCRIPT = r"""<script>
(function(){
  document.addEventListener('click', function(e){
    var b = e.target.closest('[data-copy]'); if (!b) return;
    var el = document.getElementById(b.getAttribute('data-copy'));
    function done(){ b.textContent = 'Copied'; setTimeout(function(){ b.textContent = 'Copy'; }, 1500); }
    if (navigator.clipboard) navigator.clipboard.writeText(el.value).then(done, function(){ el.select(); document.execCommand('copy'); done(); });
    else { el.select(); document.execCommand('copy'); done(); }
  });
  // Fetch the photos up front: iOS only opens the share sheet straight
  // from a tap, so there's no time to download them after it.
  var files = [];
  Promise.all(Array.prototype.map.call(document.querySelectorAll('img[data-photo]'), function(img, i){
    return fetch(img.src, {credentials: 'same-origin'}).then(function(r){ return r.blob(); })
      .then(function(b){ files[i] = new File([b], 'vinted-' + (i + 1) + '.jpg', {type: b.type || 'image/jpeg'}); });
  })).catch(function(){});
  var btn = document.getElementById('share-photos');
  if (btn) btn.addEventListener('click', function(){
    var ready = files.filter(Boolean);
    if (navigator.canShare && ready.length && navigator.canShare({files: ready})) {
      navigator.share({files: ready}).catch(function(){});
    } else {
      document.getElementById('share-help').textContent = 'Press and hold each photo above and choose Save to Photos.';
    }
  });
})();
</script>"""

_STYLE = """<style>
.item-name{margin:4px 0 12px}
.steps{display:grid;gap:22px;padding-left:20px}.steps h3{margin:0 0 8px}
.photo-actions{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 6px}.photos{display:flex;gap:8px;overflow-x:auto;margin-bottom:10px}.photos img{height:200px;border-radius:10px;object-fit:contain;background:var(--surface-sunken)}
.copy-row{display:grid;grid-template-columns:1fr auto;gap:6px 8px;align-items:end;margin:10px 0}
.copy-row label{grid-column:1/-1;font-weight:600}.copy-row input,.copy-row textarea{width:100%;min-width:0;box-sizing:border-box;padding:10px}
.hints{margin:8px 0;padding-left:18px;display:grid;gap:4px}
.posted{display:grid;gap:10px}.posted input{padding:12px;font-size:16px}
.vinted-live.sold,.vinted-live.removed{opacity:.6}.inline{display:flex;flex-wrap:wrap;gap:8px;align-items:end;margin-top:8px}
.inline label{display:grid;gap:4px}.inline input{width:110px;padding:8px}
</style>"""
