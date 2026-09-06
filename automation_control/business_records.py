"""Suppliers, goals, and the release calendar -- Modules 10, 14, 15.

Grouped in one file because each is simple reference-data CRUD with no
shared logic worth its own module, unlike buying.py (shares the
traffic-light calculation) or commerce.py (shares fee-rule lookups).
"""

import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import Goal, GoalKind, ReleaseCalendarEntry, Supplier, SupplierStatus
from .ui import brand_header, page, pill

router = APIRouter(tags=["business-records"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


# --- Module 10: suppliers ----------------------------------------------------

@router.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    suppliers = session.scalars(select(Supplier).order_by(Supplier.name)).all()
    status_kind = {
        SupplierStatus.ACTIVE: "ok", SupplierStatus.APPROVED: "ok",
        SupplierStatus.APPLY: "warn", SupplierStatus.APPLICATION_SENT: "warn", SupplierStatus.PAUSED: "warn",
        SupplierStatus.DECLINED: "bad",
    }
    rows = "".join(
        "<tr>"
        f"<td>{escape(s.name)}</td>"
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
    body = brand_header("Suppliers") + "<p class='subtitle'>Module 10 -- wholesale accounts and distributors.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Suppliers", body))


@router.post("/suppliers")
def create_supplier(
    name: str = Form(...), contact: str = Form(""), website: str = Form(""), categories: str = Form(""),
    account_status: str = Form(SupplierStatus.RESEARCHING.value), wholesale_discount_pct: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    session.add(Supplier(
        id=str(uuid.uuid4()), name=name, contact=contact or None, website=website or None, categories=categories or None,
        account_status=SupplierStatus(account_status),
        wholesale_discount_pct=_dec(wholesale_discount_pct) if wholesale_discount_pct.strip() else None,
    ))
    session.commit()
    return RedirectResponse("/suppliers", status_code=303)


# --- Module 14: goals ---------------------------------------------------------

@router.get("/goals", response_class=HTMLResponse)
def goals_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    goals = session.scalars(select(Goal).order_by(Goal.achieved_at.is_(None).desc(), Goal.created_at)).all()
    cards = []
    for g in goals:
        pct = min(100, float(g.current_value / g.target_value * 100)) if g.target_value else 0
        achieved = g.achieved_at is not None
        cards.append(
            "<div class='goal-card" + (" achieved" if achieved else "") + "'>"
            f"<div class='goal-label'>{escape(g.label)}</div>"
            f"<div class='goal-bar'><div class='goal-fill' style='width:{pct:.0f}%'></div></div>"
            f"<div class='goal-value'>{g.current_value:,.0f} / {g.target_value:,.0f}"
            + (f" {pill('achieved', 'ok')}" if achieved else "") + "</div>"
            + f"<form method='post' action='/goals/{escape(g.id)}/progress' class='goal-form'>"
            + "<input name='current_value' type='number' step='0.01' placeholder='update progress'>"
            + "<button type='submit'>Update</button></form>"
            "</div>"
        )
    board = f"<div class='goal-grid'>{''.join(cards) or '<p class=\"muted\">No goals yet.</p>'}</div>"
    form = (
        "<div class='panel'><h2>Add a goal</h2>"
        "<form method='post' action='/goals' class='calc-form'>"
        "<label>Label<input name='label' required placeholder='First $1,000 sales month'></label>"
        f"<label>Kind<select name='kind'>{''.join(f'<option value=\"{k.value}\">{k.value.title()}</option>' for k in GoalKind)}</select></label>"
        "<label>Target value<input name='target_value' type='number' step='0.01' value='1' required></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = brand_header("Goals") + "<p class='subtitle'>Module 14 -- business milestones, tracked visually.</p>" + board + form + _STYLE
    return HTMLResponse(page("EzBay — Goals", body))


@router.post("/goals")
def create_goal(
    label: str = Form(...), kind: str = Form(GoalKind.MILESTONE.value), target_value: str = Form("1"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    session.add(Goal(id=str(uuid.uuid4()), label=label, kind=GoalKind(kind), target_value=_dec(target_value, "1")))
    session.commit()
    return RedirectResponse("/goals", status_code=303)


@router.post("/goals/{goal_id}/progress")
def update_goal_progress(
    goal_id: str, current_value: str = Form(...),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    from datetime import UTC, datetime

    goal = session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    goal.current_value = _dec(current_value)
    if goal.current_value >= goal.target_value and goal.achieved_at is None:
        goal.achieved_at = datetime.now(UTC)
    session.commit()
    return RedirectResponse("/goals", status_code=303)


# --- Module 15: release calendar ---------------------------------------------

@router.get("/release-calendar", response_class=HTMLResponse)
def release_calendar_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    entries = session.scalars(select(ReleaseCalendarEntry).order_by(ReleaseCalendarEntry.release_date)).all()
    rows = "".join(
        "<tr>"
        f"<td>{escape(e.product_name)}</td>"
        f"<td>{escape(e.release_date or '—')}</td>"
        f"<td>{escape(e.supplier_deadline or '—')}</td>"
        f"<td>{f'${e.wholesale_price:,.2f}' if e.wholesale_price else '—'}</td>"
        f"<td>{f'${e.retail_price:,.2f}' if e.retail_price else '—'}</td>"
        f"<td>{e.ordered_quantity if e.ordered_quantity is not None else '—'}</td>"
        "</tr>"
        for e in entries
    ) or "<tr><td colspan=6>No upcoming releases tracked yet.</td></tr>"
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Product</th><th>Release date</th><th>Order deadline</th><th>Wholesale</th><th>Retail</th><th>Ordered</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></div>"
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
    body = brand_header("Release calendar") + "<p class='subtitle'>Module 15 -- upcoming releases and supplier ordering deadlines.</p>" + table + form + _STYLE
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


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#04101a;font-weight:700;cursor:pointer;margin-top:4px}
.goal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-bottom:20px}
.goal-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:12px;padding:16px}
.goal-card.achieved{border-color:rgba(52,211,153,.4)}
.goal-label{font-weight:650;margin-bottom:10px}
.goal-bar{height:8px;border-radius:999px;background:#0a0f1c;overflow:hidden;margin-bottom:8px}
.goal-fill{height:100%;background:linear-gradient(120deg,var(--accent),var(--accent-2))}
.goal-value{font-size:13px;color:var(--text-dim);margin-bottom:10px}
.goal-form{display:flex;gap:8px}
.goal-form input{flex:1;background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;padding:8px;color:var(--text)}
.goal-form button{padding:8px 14px;border-radius:8px;border:none;background:var(--accent-2);color:#04101a;font-weight:700;cursor:pointer}
.muted{color:var(--text-dim);font-size:13px}
</style>"""
