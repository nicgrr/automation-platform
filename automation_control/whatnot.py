"""Whatnot show tracking -- Module 7's actual show workflow, distinct from
MarketplaceFeeRule (which just holds Whatnot's fee structure and already
exists via commerce.py).

Marking an item SOLD here creates a real Sale/SaleItem automatically, using
whichever MarketplaceFeeRule is active for Whatnot right now -- so a show's
numbers flow straight into /sales and /analytics without re-entering
anything, closing the loop the platform vision describes ("sell through
Whatnot -> record fees -> calculate real profit"). The fee is computed, not
hardcoded, and can still be corrected on /sales afterward if the actual
fee charged differs from the rule.
"""

import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .commerce import active_fee_rule
from .database import get_session
from .models import (
    InventoryItem, Marketplace, Sale, SaleItem, WhatnotShow, WhatnotShowItem, WhatnotShowItemOutcome,
)
from .ui import brand_header, page, pill

router = APIRouter(prefix="/whatnot", tags=["whatnot"])

OUTCOME_KIND = {
    WhatnotShowItemOutcome.SOLD: "ok", WhatnotShowItemOutcome.UNSOLD: "neutral",
    WhatnotShowItemOutcome.GIVEAWAY: "warn", WhatnotShowItemOutcome.PENDING: "neutral",
}


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


def _whatnot_marketplace(session: Session) -> Marketplace | None:
    return session.scalar(select(Marketplace).where(Marketplace.name == "Whatnot"))


def _show_stats(session: Session, show_id: str) -> dict:
    items = session.scalars(select(WhatnotShowItem).where(WhatnotShowItem.whatnot_show_id == show_id)).all()
    sold = [i for i in items if i.outcome == WhatnotShowItemOutcome.SOLD]
    revenue = sum((i.final_price or Decimal("0")) for i in sold)
    settled = [i for i in items if i.outcome != WhatnotShowItemOutcome.PENDING]
    sell_through = (len(sold) / len(settled) * 100) if settled else None
    return {"items": items, "sold_count": len(sold), "total_count": len(items), "revenue": revenue, "sell_through": sell_through}


@router.get("", response_class=HTMLResponse)
def whatnot_shows_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    shows = session.scalars(select(WhatnotShow).order_by(WhatnotShow.show_date.desc())).all()
    rows = []
    for show in shows:
        stats = _show_stats(session, show.id)
        sell_through_text = f"{stats['sell_through']:.0f}%" if stats["sell_through"] is not None else "—"
        rows.append(
            "<tr>"
            f"<td><a href='/whatnot/{escape(show.id)}'>{escape(show.title)}</a></td>"
            f"<td>{show.show_date:%Y-%m-%d}</td>"
            f"<td>{stats['sold_count']} / {stats['total_count']}</td>"
            f"<td>${stats['revenue']:,.2f}</td>"
            f"<td>{sell_through_text}</td>"
            "</tr>"
        )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Show</th><th>Date</th><th>Sold / total</th><th>Revenue</th><th>Sell-through</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=5>No shows yet.</td></tr>'}</tbody></table></div></div>"
    )
    form = (
        "<div class='panel'><h2>New show</h2>"
        "<form method='post' action='/whatnot' class='calc-form'>"
        "<label>Title<input name='title' required placeholder='Friday One Piece Night'></label>"
        "<label>Show date<input name='show_date' type='date'></label>"
        "<button type='submit'>Create</button></form></div>"
    )
    body = brand_header("Whatnot shows") + "<p class='subtitle'>Module 7 -- your Whatnot control panel.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Whatnot", body))


@router.post("")
def create_show(
    title: str = Form(...), show_date: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    from datetime import UTC, datetime

    show = WhatnotShow(
        id=str(uuid.uuid4()), title=title,
        show_date=datetime.fromisoformat(show_date).replace(tzinfo=UTC) if show_date else datetime.now(UTC),
    )
    session.add(show)
    session.commit()
    return RedirectResponse(f"/whatnot/{show.id}", status_code=303)


@router.get("/{show_id}", response_class=HTMLResponse)
def show_detail(show_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    stats = _show_stats(session, show_id)

    item_rows = []
    for i in stats["items"]:
        outcome_form = "".join(
            f"<button name='outcome' value='{o.value}' class='outcome-btn'>{o.value.title()}</button>"
            for o in WhatnotShowItemOutcome if o != WhatnotShowItemOutcome.PENDING
        )
        item_rows.append(
            "<tr>"
            f"<td>{escape(i.description)}</td>"
            f"<td>{f'${i.starting_price:,.2f}' if i.starting_price else '—'}</td>"
            f"<td>{f'${i.final_price:,.2f}' if i.final_price else '—'}</td>"
            f"<td>{pill(i.outcome.value, OUTCOME_KIND[i.outcome])}</td>"
            + ("<td>—</td>" if i.outcome != WhatnotShowItemOutcome.PENDING else
               f"<td><form method='post' action='/whatnot/{escape(show_id)}/items/{escape(i.id)}/outcome' class='outcome-form'>"
               "<input name='final_price' type='number' step='0.01' placeholder='final price'>" + outcome_form + "</form></td>")
            + "</tr>"
        )
    items_table = (
        "<div class='panel'><h2>Items</h2><div class='table-wrap'><table>"
        "<thead><tr><th>Item</th><th>Starting</th><th>Final</th><th>Outcome</th><th>Settle</th></tr></thead>"
        f"<tbody>{''.join(item_rows) or '<tr><td colspan=5>No items queued yet.</td></tr>'}</tbody></table></div>"
        "<form method='post' action='" + f"/whatnot/{show_id}/items" + "' class='calc-form' style='margin-top:14px'>"
        "<label>Description<input name='description' required></label>"
        "<label>Starting price<input name='starting_price' type='number' step='0.01'></label>"
        "<button type='submit'>Add to queue</button></form></div>"
    )

    sell_through_text = f"{stats['sell_through']:.0f}%" if stats["sell_through"] is not None else "—"
    stat_grid = (
        "<div class='panel'><div class='calc-grid'>"
        f"<div><div class='label'>Sold / queued</div><div class='value'>{stats['sold_count']} / {stats['total_count']}</div></div>"
        f"<div><div class='label'>Revenue</div><div class='value'>${stats['revenue']:,.2f}</div></div>"
        f"<div><div class='label'>Sell-through</div><div class='value'>{sell_through_text}</div></div>"
        "</div></div>"
    )

    body = brand_header(show.title) + f"<p class='subtitle'>{show.show_date:%Y-%m-%d}</p>" + stat_grid + items_table + _STYLE
    return HTMLResponse(page(f"EzBay — {show.title}", body))


@router.post("/{show_id}/items")
def add_show_item(
    show_id: str, description: str = Form(...), starting_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    if session.get(WhatnotShow, show_id) is None:
        raise HTTPException(status_code=404, detail="show not found")
    session.add(WhatnotShowItem(
        id=str(uuid.uuid4()), whatnot_show_id=show_id, description=description,
        starting_price=_dec(starting_price) if starting_price.strip() else None,
    ))
    session.commit()
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


@router.post("/{show_id}/items/{item_id}/outcome")
def mark_outcome(
    show_id: str, item_id: str, outcome: str = Form(...), final_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item = session.get(WhatnotShowItem, item_id)
    if item is None or item.whatnot_show_id != show_id:
        raise HTTPException(status_code=404, detail="show item not found")
    item.outcome = WhatnotShowItemOutcome(outcome)

    if item.outcome == WhatnotShowItemOutcome.SOLD:
        price = _dec(final_price) if final_price.strip() else (item.starting_price or Decimal("0"))
        item.final_price = price

        marketplace = _whatnot_marketplace(session)
        fee_amount = Decimal("0")
        if marketplace is not None:
            rule = active_fee_rule(session, marketplace.id)
            if rule is not None:
                fee_amount = price * (rule.commission_pct + rule.processing_pct) / 100 + rule.fixed_fee

        cost_basis = Decimal("0")
        if item.inventory_item_id:
            inv = session.get(InventoryItem, item.inventory_item_id)
            if inv and inv.allocated_cost_basis:
                cost_basis = inv.allocated_cost_basis

        sale = Sale(
            id=str(uuid.uuid4()), marketplace_id=marketplace.id if marketplace else None,
            gross_amount=price, fees_amount=fee_amount,
        )
        session.add(sale)
        session.flush()
        session.add(SaleItem(
            id=str(uuid.uuid4()), sale_id=sale.id, inventory_item_id=item.inventory_item_id,
            description=item.description, quantity=1, unit_price=price, cost_basis=cost_basis,
        ))
        item.sale_id = sale.id

    session.commit()
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
.outcome-form{display:flex;gap:6px;align-items:center}
.outcome-form input{width:90px;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:6px;
  padding:6px;color:var(--text);font-size:12px}
.outcome-btn{padding:6px 10px;border-radius:6px;border:1px solid var(--panel-border);background:none;
  color:var(--text-dim);font-size:11px;cursor:pointer}
.outcome-btn:hover{border-color:var(--accent);color:var(--accent)}
</style>"""
