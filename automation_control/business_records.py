"""Suppliers and the release calendar -- Modules 10 and 15.

Grouped in one file because each is simple reference-data CRUD with no
shared logic worth its own module, unlike buying.py (shares the
traffic-light calculation) or commerce.py (shares fee-rule lookups).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CatalogItem, PurchaseLot, ReleaseCalendarEntry,
    Supplier, SupplierProduct, SupplierStatus,
)
from .ui import brand_header, page, pill

router = APIRouter(tags=["business-records"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


def _amount(value: str, label: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a valid amount") from exc
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise HTTPException(
            status_code=400,
            detail=f"{label} must be {'greater than zero' if positive else 'zero or more'}",
        )
    return number.quantize(Decimal("0.01"))


# --- Module 10: suppliers ----------------------------------------------------

@router.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    suppliers = session.scalars(select(Supplier).order_by(Supplier.name).limit(500)).all()
    status_kind = {
        SupplierStatus.ACTIVE: "ok", SupplierStatus.APPROVED: "ok",
        SupplierStatus.APPLY: "warn", SupplierStatus.APPLICATION_SENT: "warn", SupplierStatus.PAUSED: "warn",
        SupplierStatus.DECLINED: "bad",
    }
    rows = "".join(
        "<tr>"
        f"<td><a href='/suppliers/{escape(s.id)}'>{escape(s.name)}</a></td>"
        f"<td>{escape(s.categories or '—')}</td>"
        f"<td>{pill(s.account_status.value.replace('_', ' ').title(), status_kind.get(s.account_status, 'neutral'))}</td>"
        f"<td>{f'{s.wholesale_discount_pct}%' if s.wholesale_discount_pct else '—'}</td>"
        f"<td>{escape(s.website or '—')}</td>"
        "</tr>"
        for s in suppliers
    ) or "<tr><td colspan=5>No suppliers yet.</td></tr>"
    table = f"<div class='panel'><div class='table-wrap'><table><thead><tr><th>Supplier</th><th>Categories</th><th>Status</th><th>Discount</th><th>Website</th></tr></thead><tbody>{rows}</tbody></table></div></div>"

    status_options = "".join(f"<option value='{s.value}'>{s.value.replace('_', ' ').title()}</option>" for s in SupplierStatus)
    form = (
        "<div class='panel'><h2>Add a supplier</h2>"
        "<form method='post' action='/suppliers' class='calc-form'>"
        "<label>Name<input name='name' required></label>"
        "<label>Contact<input name='contact'></label>"
        "<label>Website<input name='website'></label>"
        "<label>Categories<input name='categories' placeholder='TCG, blind box'></label>"
        f"<label>Status<select name='account_status'>{status_options}</select></label>"
        "<label>Wholesale discount %<input name='wholesale_discount_pct' type='number' step='0.1'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = brand_header("Suppliers") + table + form + _STYLE
    return HTMLResponse(page("EzBay — Suppliers", body))


@router.post("/suppliers")
def create_supplier(
    name: str = Form(...), contact: str = Form(""), website: str = Form(""), categories: str = Form(""),
    account_status: str = Form(SupplierStatus.RESEARCHING.value), wholesale_discount_pct: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    clean_name = name.strip()[:256]
    if not clean_name:
        raise HTTPException(status_code=400, detail="supplier name is required")
    if session.scalar(select(Supplier.id).where(func.lower(Supplier.name) == clean_name.lower())):
        raise HTTPException(status_code=409, detail="that supplier already exists")
    try:
        status = SupplierStatus(account_status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid supplier status") from exc
    discount = _amount(wholesale_discount_pct, "wholesale discount") if wholesale_discount_pct.strip() else None
    if discount is not None and discount > 100:
        raise HTTPException(status_code=400, detail="wholesale discount cannot exceed 100%")
    supplier = Supplier(
        id=str(uuid.uuid4()), name=clean_name, contact=contact.strip() or None,
        website=website.strip() or None, categories=categories.strip() or None,
        account_status=status, wholesale_discount_pct=discount,
    )
    session.add(supplier)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="that supplier already exists") from exc
    record_event(session, actor_type="user", actor_id=user, action="supplier.create",
                 resource_type="supplier", resource_id=supplier.id, outcome="success",
                 correlation_id=supplier.id, details={"status": status.value})
    return RedirectResponse("/suppliers", status_code=303)


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_detail_page(
    supplier_id: str, q: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    supplier = session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="supplier not found")

    products = session.scalars(select(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id).order_by(SupplierProduct.description)).all()
    rows = "".join(
        "<tr>"
        f"<td>{escape(p.description)}</td>"
        f"<td>${p.cost:,.2f}</td>"
        f"<td>{p.min_order_qty if p.min_order_qty is not None else '—'}</td>"
        f"<td>{'Linked' if p.catalog_item_id else pill('Missing', 'bad')}</td>"
        "</tr>"
        for p in products
    ) or "<tr><td colspan=4>No products on file for this supplier yet.</td></tr>"
    table = f"<div class='panel'><h2>What they sell</h2><div class='table-wrap'><table><thead><tr><th>Product</th><th>Cost</th><th>Min order qty</th><th>Catalogue</th></tr></thead><tbody>{rows}</tbody></table></div></div>"

    catalog_query = q.strip()
    catalog_statement = select(CatalogItem).order_by(CatalogItem.name).limit(100)
    if catalog_query:
        catalog_statement = catalog_statement.where(CatalogItem.name.ilike(f"%{catalog_query}%"))
    catalog_options = "".join(
        f"<option value='{escape(item.id)}'>{escape(item.name)} · {escape(item.item_type.value)}</option>"
        for item in session.scalars(catalog_statement)
    )

    form = (
        "<div class='panel'><h2>Add a product</h2>"
        f"<form method='get' action='/suppliers/{escape(supplier_id)}' class='calc-form'>"
        f"<label>Find catalogue item<input name='q' value='{escape(q)}' placeholder='Search exact product name'></label>"
        "<button type='submit'>Search catalogue</button></form>"
        f"<form method='post' action='/suppliers/{escape(supplier_id)}/products' class='calc-form'>"
        f"<label>Canonical product<select name='catalog_item_id' required>{catalog_options}</select></label>"
        "<label>Cost<input name='cost' type='number' step='0.01' required></label>"
        "<label>Min order qty<input name='min_order_qty' type='number' step='1'></label>"
        "<button type='submit'>Add</button></form></div>"
    )

    details = (
        "<div class='panel'>"
        f"<p><strong>Contact:</strong> {escape(supplier.contact or '—')}</p>"
        f"<p><strong>Website:</strong> {escape(supplier.website or '—')}</p>"
        f"<p><strong>Categories:</strong> {escape(supplier.categories or '—')}</p>"
        f"<p><strong>Wholesale discount:</strong> {f'{supplier.wholesale_discount_pct}%' if supplier.wholesale_discount_pct else '—'}</p>"
        f"<p><strong>Minimum order:</strong> {f'${supplier.minimum_order:,.2f}' if supplier.minimum_order else '—'}</p>"
        "</div>"
    )
    body = (
        brand_header(escape(supplier.name)) + "<p class='subtitle'><a href='/suppliers'>&larr; All suppliers</a></p>"
        + details
        + f"<p><a class='btn' href='/purchase-lots?supplier_id={escape(supplier.id)}'>Create a purchase lot from this supplier</a></p>"
        + table + form + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {supplier.name}", body))


@router.post("/suppliers/{supplier_id}/products")
def create_supplier_product(
    supplier_id: str, catalog_item_id: str = Form(...), cost: str = Form("0"), min_order_qty: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    if session.get(Supplier, supplier_id) is None:
        raise HTTPException(status_code=404, detail="supplier not found")
    catalog = session.get(CatalogItem, catalog_item_id)
    if catalog is None:
        raise HTTPException(status_code=404, detail="catalogue item not found")
    try:
        minimum = int(min_order_qty) if min_order_qty.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="minimum order quantity must be a whole number") from exc
    if minimum is not None and minimum < 1:
        raise HTTPException(status_code=400, detail="minimum order quantity must be at least one")
    product = SupplierProduct(
        id=str(uuid.uuid4()), supplier_id=supplier_id, catalog_item_id=catalog.id,
        description=catalog.name, cost=_amount(cost, "supplier cost", positive=True),
        min_order_qty=minimum,
    )
    session.add(product)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="that catalogue item is already linked to this supplier") from exc
    record_event(session, actor_type="user", actor_id=user, action="supplier.product.link",
                 resource_type="supplier_product", resource_id=product.id, outcome="success",
                 correlation_id=supplier_id, details={"catalog_item_id": catalog.id})
    return RedirectResponse(f"/suppliers/{supplier_id}", status_code=303)


# --- Module 15: release calendar ---------------------------------------------
#
# Was a flat add-only table: no edit, no delete, and nothing seeding it,
# so it launched at 0 rows with no way to fix a typo short of the
# database. Two things changed: real edit/delete, matching the pattern
# shop_listings_admin.py's listing editor already established, and two
# genuinely upcoming English Pokemon TCG releases seeded in (see
# seed_release_calendar.py) rather than an empty form -- cross-checked
# against two independent release-calendar sources before landing, the
# same rule every other seeded date in this app has followed.
#
# Dates are stored as plain "YYYY-MM-DD" strings (matching the <input
# type=date> format and the model's own String(32) column) rather than a
# real Date type -- consistent with how the row already worked, not
# changed here. _parse_date below is what lets "how urgent is this"
# comparisons happen without a schema change.

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _release_status(entry: ReleaseCalendarEntry, today: date) -> tuple[str, str]:
    """(urgency bucket, pill kind) -- the bucket controls which group of
    the page an entry sorts into, the pill kind its colour."""
    release = _parse_date(entry.release_date)
    deadline = _parse_date(entry.supplier_deadline)
    if release and release < today:
        return "released", "neutral"
    if deadline and deadline < today and (release is None or release >= today):
        return "deadline passed", "bad"
    if deadline is not None:
        days = (deadline - today).days
        if days <= 7:
            return "order deadline soon", "bad" if days <= 2 else "warn"
    if release is not None:
        days = (release - today).days
        if days <= 14:
            return "releasing soon", "ok"
    return "upcoming", "neutral"


def _release_row(e: ReleaseCalendarEntry, today: date) -> str:
    status, kind = _release_status(e, today)
    return (
        "<tr>"
        f"<td><strong>{escape(e.product_name)}</strong> {pill(status, kind)}"
        f"{f"<div class='muted'>{e.ordered_quantity} on order</div>" if e.ordered_quantity else ''}</td>"
        f"<td>{escape(e.release_date or '—')}</td>"
        f"<td>{escape(e.supplier_deadline or '—')}</td>"
        f"<td>{f'${e.wholesale_price:,.2f}' if e.wholesale_price else '—'}</td>"
        f"<td>{f'${e.retail_price:,.2f}' if e.retail_price else '—'}</td>"
        f"<td class='listing-actions'>"
        f"<a class='btn small ghost' href='/release-calendar/{escape(e.id)}/edit'>Edit</a>"
        f"<form method='post' action='/release-calendar/{escape(e.id)}/delete' "
        "onsubmit=\"return confirm('Remove this release from the calendar?');\">"
        "<button class='btn small ghost' type='submit'>Delete</button></form>"
        "</td>"
        "</tr>"
    )


@router.get("/release-calendar", response_class=HTMLResponse)
def release_calendar_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    today = date.today()
    entries = session.scalars(select(ReleaseCalendarEntry)).all()

    # Urgent first, not just date order -- an order deadline in 2 days
    # matters more than sort position, the same reason job_health leads
    # with what needs attention rather than an alphabetical service list.
    order = {"order deadline soon": 0, "deadline passed": 0, "releasing soon": 1, "upcoming": 2, "released": 3}
    entries = sorted(entries, key=lambda e: (order.get(_release_status(e, today)[0], 2),
                                             _parse_date(e.release_date) or date.max))
    total = len(entries)
    entries = entries[:200]

    rows = "".join(_release_row(e, today) for e in entries) or "<tr><td colspan=6>No upcoming releases tracked yet.</td></tr>"
    cap_note = (
        f"<p class='muted'>{len(entries)} of {total} shown, capped at 200 rows.</p>" if total > len(entries) else ""
    )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Product</th><th>Release date</th><th>Order deadline</th><th>Wholesale</th><th>Retail</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>{cap_note}</div>"
    )
    form = (
        "<div class='panel'><h2>Add a release</h2>"
        "<form method='post' action='/release-calendar' class='calc-form'>"
        "<label>Product<input name='product_name' required></label>"
        "<label>Release date<input name='release_date' type='date'></label>"
        "<label>Supplier order deadline<input name='supplier_deadline' type='date'></label>"
        "<label>Wholesale price<input name='wholesale_price' type='number' step='0.01'></label>"
        "<label>Retail price<input name='retail_price' type='number' step='0.01'></label>"
        "<label>Ordered quantity<input name='ordered_quantity' type='number'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = (
        brand_header("Release calendar")
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Release calendar", body))


@router.post("/release-calendar")
def create_release_entry(
    product_name: str = Form(...), release_date: str = Form(""), supplier_deadline: str = Form(""),
    wholesale_price: str = Form(""), retail_price: str = Form(""), ordered_quantity: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    session.add(ReleaseCalendarEntry(
        id=str(uuid.uuid4()), product_name=product_name, release_date=release_date or None, supplier_deadline=supplier_deadline or None,
        wholesale_price=_dec(wholesale_price) if wholesale_price.strip() else None,
        retail_price=_dec(retail_price) if retail_price.strip() else None,
        ordered_quantity=int(ordered_quantity) if ordered_quantity.strip() else None,
    ))
    session.commit()
    return RedirectResponse("/release-calendar", status_code=303)


@router.get("/release-calendar/{entry_id}/edit", response_class=HTMLResponse)
def edit_release_entry_form(
    entry_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    entry = session.get(ReleaseCalendarEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="release calendar entry not found")
    body = (
        brand_header("Edit release")
        + f"<div class='panel'><form method='post' action='/release-calendar/{escape(entry.id)}/edit' class='calc-form'>"
        + f"<label>Product<input name='product_name' value='{escape(entry.product_name)}' required></label>"
        + f"<label>Release date<input name='release_date' type='date' value='{escape(entry.release_date or '')}'></label>"
        + f"<label>Supplier order deadline<input name='supplier_deadline' type='date' value='{escape(entry.supplier_deadline or '')}'></label>"
        + f"<label>Wholesale price<input name='wholesale_price' type='number' step='0.01' value='{entry.wholesale_price or ''}'></label>"
        + f"<label>Retail price<input name='retail_price' type='number' step='0.01' value='{entry.retail_price or ''}'></label>"
        + f"<label>Ordered quantity<input name='ordered_quantity' type='number' value='{entry.ordered_quantity if entry.ordered_quantity is not None else ''}'></label>"
        + "<div class='listing-actions'><button type='submit'>Save</button>"
        + "<a class='btn small ghost' href='/release-calendar'>Cancel</a></div>"
        + "</form></div>" + _STYLE
    )
    return HTMLResponse(page("EzBay — Edit release", body))


@router.post("/release-calendar/{entry_id}/edit")
def edit_release_entry_save(
    entry_id: str, product_name: str = Form(...), release_date: str = Form(""), supplier_deadline: str = Form(""),
    wholesale_price: str = Form(""), retail_price: str = Form(""), ordered_quantity: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    entry = session.get(ReleaseCalendarEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="release calendar entry not found")
    entry.product_name = product_name
    entry.release_date = release_date or None
    entry.supplier_deadline = supplier_deadline or None
    entry.wholesale_price = _dec(wholesale_price) if wholesale_price.strip() else None
    entry.retail_price = _dec(retail_price) if retail_price.strip() else None
    entry.ordered_quantity = int(ordered_quantity) if ordered_quantity.strip() else None
    session.commit()
    return RedirectResponse("/release-calendar", status_code=303)


@router.post("/release-calendar/{entry_id}/delete")
def delete_release_entry(
    entry_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    entry = session.get(ReleaseCalendarEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="release calendar entry not found")
    session.delete(entry)
    session.commit()
    return RedirectResponse("/release-calendar", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.muted{color:var(--text-dim);font-size:13px}
.listing-actions{display:flex;gap:6px}
</style>"""
