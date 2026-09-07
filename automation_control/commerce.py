"""Marketplace fee rules, sales, and the customer CRM -- Modules 7-9.

Fee rules are time-boxed rows (see MarketplaceFeeRule's own docstring) so a
"0% commission weekend" is a new row with its own effective dates, never a
code change. A sale's fee amount is still entered explicitly at record
time rather than solely computed from the active rule, because the sale
record should keep saying what was actually true even after a rule is
later superseded -- the active rule is offered as a prefill/reference, not
forced.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import Customer, Marketplace, MarketplaceFeeRule, Sale, SaleItem
from .ui import brand_header, page, pill

router = APIRouter(tags=["commerce"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


def active_fee_rule(session: Session, marketplace_id: str, category: str | None = None, at: datetime | None = None) -> MarketplaceFeeRule | None:
    """The fee rule in effect for a sale -- narrowest category match first,
    then whichever row's [effective_from, effective_to) window contains
    `at`. A category-specific rule always wins over a category=None
    catch-all, even if the catch-all is more recently added.

    The effective_to/effective_from comparison is done in the SQL WHERE
    clause rather than in Python -- SQLite has no native timezone-aware
    datetime type, so a DateTime(timezone=True) column can round-trip back
    as a naive datetime, and comparing that in Python against an
    aware `datetime.now(UTC)` raises TypeError (caught by
    test_an_expired_promotion_is_not_active). Comparing inside the query
    lets the DB driver's own bind-parameter handling normalize both sides
    instead.
    """
    at = at or datetime.now(UTC)
    candidates = session.scalars(
        select(MarketplaceFeeRule).where(
            MarketplaceFeeRule.marketplace_id == marketplace_id,
            MarketplaceFeeRule.effective_from <= at,
            or_(MarketplaceFeeRule.effective_to.is_(None), MarketplaceFeeRule.effective_to > at),
        )
    ).all()
    category_match = [r for r in candidates if r.category == category] if category else []
    pool = category_match or [r for r in candidates if r.category is None]
    return max(pool, key=lambda r: r.effective_from) if pool else None


# --- Module 7: marketplaces & fee rules -------------------------------------

@router.get("/marketplaces", response_class=HTMLResponse)
def marketplaces_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    marketplaces = session.scalars(select(Marketplace).order_by(Marketplace.name)).all()
    rule_rows = []
    for m in marketplaces:
        rules = session.scalars(select(MarketplaceFeeRule).where(MarketplaceFeeRule.marketplace_id == m.id).order_by(MarketplaceFeeRule.effective_from.desc())).all()
        for r in rules:
            active = active_fee_rule(session, m.id, r.category)
            is_active = active is not None and active.id == r.id
            window = f"{r.effective_from:%Y-%m-%d} → {r.effective_to:%Y-%m-%d}" if r.effective_to else f"{r.effective_from:%Y-%m-%d} → ongoing"
            rule_rows.append(
                "<tr>"
                f"<td>{escape(m.name)}</td>"
                f"<td>{escape(r.category or 'all categories')}{' — ' + escape(r.promotion_label) if r.promotion_label else ''}</td>"
                f"<td>{r.commission_pct}% + {r.processing_pct}% + ${r.fixed_fee}</td>"
                f"<td>{escape(window)}</td>"
                f"<td>{pill('active', 'ok') if is_active else pill('inactive', 'neutral')}</td>"
                "</tr>"
            )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Marketplace</th><th>Category / promo</th><th>Fees</th><th>Window</th><th></th></tr></thead>"
        f"<tbody>{''.join(rule_rows) or '<tr><td colspan=5>No fee rules yet.</td></tr>'}</tbody></table></div></div>"
    )
    marketplace_options = "".join(f"<option value='{escape(m.id)}'>{escape(m.name)}</option>" for m in marketplaces)
    form = (
        "<div class='panel'><h2>Add a fee rule</h2>"
        "<form method='post' action='/marketplaces/fee-rules' class='calc-form'>"
        f"<label>Marketplace<select name='marketplace_id'>{marketplace_options}</select></label>"
        "<label>Category (blank = all)<input name='category'></label>"
        "<label>Commission %<input name='commission_pct' type='number' step='0.01' value='0'></label>"
        "<label>Payment processing %<input name='processing_pct' type='number' step='0.01' value='0'></label>"
        "<label>Fixed fee<input name='fixed_fee' type='number' step='0.01' value='0'></label>"
        "<label>Promotion label (optional)<input name='promotion_label' placeholder='0% TCG weekend'></label>"
        "<label>Effective to (optional)<input name='effective_to' type='date'></label>"
        "<button type='submit'>Add rule</button>"
        "</form></div>"
    )
    body = brand_header("Marketplaces & fees") + "<p class='subtitle'>Module 7 -- fee structures change, so these are dated rows, not constants.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Marketplaces", body))


@router.post("/marketplaces/fee-rules")
def create_fee_rule(
    marketplace_id: str = Form(...), category: str = Form(""), commission_pct: str = Form("0"),
    processing_pct: str = Form("0"), fixed_fee: str = Form("0"), promotion_label: str = Form(""),
    effective_to: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    if session.get(Marketplace, marketplace_id) is None:
        raise HTTPException(status_code=404, detail="marketplace not found")
    session.add(MarketplaceFeeRule(
        id=str(uuid.uuid4()), marketplace_id=marketplace_id, category=category or None,
        commission_pct=_dec(commission_pct), processing_pct=_dec(processing_pct), fixed_fee=_dec(fixed_fee),
        promotion_label=promotion_label or None,
        effective_to=datetime.fromisoformat(effective_to).replace(tzinfo=UTC) if effective_to else None,
    ))
    session.commit()
    return RedirectResponse("/marketplaces", status_code=303)


# --- Module 8: sales ---------------------------------------------------------

@router.get("/sales", response_class=HTMLResponse)
def sales_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    sales = session.scalars(select(Sale).order_by(Sale.sold_at.desc()).limit(100)).all()
    marketplaces = {m.id: m.name for m in session.scalars(select(Marketplace)).all()}
    customers = {c.id: c.display_name for c in session.scalars(select(Customer)).all()}

    rows = []
    total_profit = Decimal("0")
    for s in sales:
        items = session.scalars(select(SaleItem).where(SaleItem.sale_id == s.id)).all()
        cost_basis_total = sum((i.cost_basis or Decimal("0")) * i.quantity for i in items)
        net_profit = s.gross_amount + s.shipping_revenue - s.fees_amount - s.shipping_cost - s.packaging_cost - s.tax_amount - cost_basis_total
        total_profit += net_profit
        item_desc = "; ".join(i.description for i in items) or "—"
        rows.append(
            "<tr>"
            f"<td>{s.sold_at:%Y-%m-%d}</td>"
            f"<td>{escape(item_desc)}</td>"
            f"<td>{escape(marketplaces.get(s.marketplace_id, '—'))}</td>"
            f"<td>{escape(customers.get(s.customer_id, '—'))}</td>"
            f"<td>${s.gross_amount:,.2f}</td>"
            f"<td>${net_profit:,.2f}</td>"
            "</tr>"
        )
    table = (
        f"<div class='panel'><div class='calc-grid' style='margin-bottom:16px'>"
        f"<div><div class='label'>Sales shown</div><div class='value'>{len(sales)}</div></div>"
        f"<div><div class='label'>Total net profit</div><div class='value'>${total_profit:,.2f}</div></div>"
        f"</div><div class='table-wrap'><table>"
        f"<thead><tr><th>Date</th><th>Item(s)</th><th>Channel</th><th>Customer</th><th>Gross</th><th>Net profit</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=6>No sales recorded yet.</td></tr>'}</tbody></table></div></div>"
    )

    marketplace_options = "".join(f"<option value='{escape(m.id)}'>{escape(m.name)}</option>" for m in session.scalars(select(Marketplace)).all())
    form = (
        "<div class='panel'><h2>Record a sale</h2>"
        "<form method='post' action='/sales' class='calc-form'>"
        f"<label>Marketplace<select name='marketplace_id'>{marketplace_options}</select></label>"
        "<label>Customer name (optional)<input name='customer_name'></label>"
        "<label>Item description<input name='description' required></label>"
        "<label>Quantity<input name='quantity' type='number' value='1' min='1'></label>"
        "<label>Sale price (each)<input name='unit_price' type='number' step='0.01' required></label>"
        "<label>Cost basis (each)<input name='cost_basis' type='number' step='0.01' value='0'></label>"
        "<label>Fees<input name='fees_amount' type='number' step='0.01' value='0'></label>"
        "<label>Shipping revenue<input name='shipping_revenue' type='number' step='0.01' value='0'></label>"
        "<label>Shipping cost<input name='shipping_cost' type='number' step='0.01' value='0'></label>"
        "<label>Packaging cost<input name='packaging_cost' type='number' step='0.01' value='0'></label>"
        "<button type='submit'>Record sale</button>"
        "</form></div>"
    )
    body = brand_header("Sales") + "<p class='subtitle'>Every channel, one ledger -- Module 8.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Sales", body))


@router.post("/sales")
def create_sale(
    marketplace_id: str = Form(""), customer_name: str = Form(""), description: str = Form(...),
    quantity: str = Form("1"), unit_price: str = Form(...), cost_basis: str = Form("0"),
    fees_amount: str = Form("0"), shipping_revenue: str = Form("0"), shipping_cost: str = Form("0"), packaging_cost: str = Form("0"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    qty = int(quantity or 1)
    price = _dec(unit_price)
    customer_id = None
    if customer_name.strip():
        customer = session.scalar(select(Customer).where(Customer.display_name == customer_name.strip()))
        if customer is None:
            customer = Customer(id=str(uuid.uuid4()), display_name=customer_name.strip())
            session.add(customer)
            session.flush()
        customer_id = customer.id

    sale = Sale(
        id=str(uuid.uuid4()), marketplace_id=marketplace_id or None, customer_id=customer_id,
        gross_amount=price * qty, fees_amount=_dec(fees_amount), shipping_revenue=_dec(shipping_revenue),
        shipping_cost=_dec(shipping_cost), packaging_cost=_dec(packaging_cost),
    )
    session.add(sale)
    session.flush()
    session.add(SaleItem(
        id=str(uuid.uuid4()), sale_id=sale.id, description=description, quantity=qty,
        unit_price=price, cost_basis=_dec(cost_basis),
    ))
    session.commit()
    return RedirectResponse("/sales", status_code=303)


# --- Module 9: customer CRM --------------------------------------------------

@router.get("/customers", response_class=HTMLResponse)
def customers_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    customers = session.scalars(select(Customer).order_by(Customer.display_name)).all()
    rows = []
    for c in customers:
        stats = session.execute(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.gross_amount), 0), func.max(Sale.sold_at))
            .where(Sale.customer_id == c.id)
        ).first()
        order_count, lifetime_value, last_purchase = stats
        rows.append(
            "<tr>"
            f"<td>{escape(c.display_name)}</td>"
            f"<td>{pill(c.segment, 'neutral') if c.segment else '—'}</td>"
            f"<td>{order_count}</td>"
            f"<td>${lifetime_value:,.2f}</td>"
            f"<td>{last_purchase.strftime('%Y-%m-%d') if last_purchase else '—'}</td>"
            "</tr>"
        )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Customer</th><th>Segment</th><th>Orders</th><th>Lifetime value</th><th>Last purchase</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=5>No customers yet -- they appear automatically when you record a sale.</td></tr>'}</tbody></table></div></div>"
    )
    form = (
        "<div class='panel'><h2>Add a customer</h2>"
        "<form method='post' action='/customers' class='calc-form'>"
        "<label>Name<input name='display_name' required></label>"
        "<label>Segment<input name='segment' placeholder='VIP, One Piece, ...'></label>"
        "<label>Notes<input name='notes'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = brand_header("Customers") + "<p class='subtitle'>Module 9 -- customers are also created automatically from sales.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Customers", body))


@router.post("/customers")
def create_customer(
    display_name: str = Form(...), segment: str = Form(""), notes: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    existing = session.scalar(select(Customer).where(Customer.display_name == display_name.strip()))
    if existing is None:
        session.add(Customer(id=str(uuid.uuid4()), display_name=display_name.strip(), segment=segment or None, notes=notes or None))
        session.commit()
    return RedirectResponse("/customers", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
</style>"""
