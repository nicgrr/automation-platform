""""Is this worth buying?" -- Modules 3, 4, 6.

Three related but distinct things live here:
  - the buying calculator (Module 6): a stateless "what would this deal
    actually net me" computation, nothing persisted
  - purchase lots (Module 3): a real negotiation over a group of items from
    one seller, with a target acquisition % and a traffic-light verdict
  - potential purchases (Module 4): the pre-negotiation watchlist -- things
    being evaluated before an actual offer goes out

All three share the same traffic-light logic, driven by BuyThresholdConfig
rather than a hardcoded triple of numbers, per the explicit requirement
that these bands be configurable.
"""

import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import (
    BuyThresholdConfig, PotentialPurchase, PotentialPurchaseStatus,
    PurchaseLot, PurchaseLotItem, PurchaseLotStatus,
)
from .ui import brand_header, page, pill

router = APIRouter(tags=["buying"])


def _default_thresholds(session: Session) -> BuyThresholdConfig:
    config = session.scalar(select(BuyThresholdConfig).where(BuyThresholdConfig.is_default == True))  # noqa: E712
    return config or BuyThresholdConfig(label="Fallback", green_max_pct=Decimal("55"), yellow_max_pct=Decimal("70"), is_default=True)


def traffic_light(acquisition_pct: Decimal | None, config: BuyThresholdConfig) -> tuple[str, str]:
    """(pill-kind, label) for an acquisition percentage -- <=green is GREEN,
    <=yellow is YELLOW, anything higher (or unknown) is RED. Lower
    acquisition % is always better (paying less of market value), matching
    every example in the spec (55% good, 65%+ marginal)."""
    if acquisition_pct is None:
        return "neutral", "UNKNOWN"
    if acquisition_pct <= config.green_max_pct:
        return "ok", "GREEN"
    if acquisition_pct <= config.yellow_max_pct:
        return "warn", "YELLOW"
    return "bad", "RED"


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


# --- Module 6: buying calculator ------------------------------------------

@router.get("/buying-calculator", response_class=HTMLResponse)
def buying_calculator(
    market_price: str = Query(""), seller_price: str = Query(""), expected_sale_price: str = Query(""),
    shipping_cost: str = Query("0"), packaging_cost: str = Query("0"), other_costs: str = Query("0"),
    marketplace_fee_pct: str = Query("0"), payment_fee_pct: str = Query("0"), gst_pct: str = Query("0"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    result_html = ""
    if market_price or seller_price:
        market = _dec(market_price)
        seller = _dec(seller_price)
        expected_sale = _dec(expected_sale_price)
        ship = _dec(shipping_cost)
        packaging = _dec(packaging_cost)
        other = _dec(other_costs)
        fee_pct_total = _dec(marketplace_fee_pct) + _dec(payment_fee_pct)
        gst = _dec(gst_pct)

        fees_amount = expected_sale * fee_pct_total / 100
        gst_amount = expected_sale * gst / 100
        expected_payout = expected_sale - fees_amount - gst_amount
        total_costs = seller + ship + packaging + other
        net_profit = expected_payout - total_costs
        gross_profit = expected_sale - seller
        roi = (net_profit / seller * 100) if seller else None
        margin = (net_profit / expected_sale * 100) if expected_sale else None
        acquisition_pct = (seller / market * 100) if market else None
        deduction_pct = (fee_pct_total + gst) / 100
        break_even_sale = (total_costs / (1 - deduction_pct)) if deduction_pct < 1 else None

        config = _default_thresholds(session)
        recommended_max = market * config.yellow_max_pct / 100 if market else None
        target_price = market * config.green_max_pct / 100 if market else None
        kind, label = traffic_light(acquisition_pct, config)

        def money(v: Decimal | None) -> str:
            return f"${v:,.2f}" if v is not None else "—"

        def pct(v: Decimal | None) -> str:
            return f"{v:.1f}%" if v is not None else "—"

        result_html = (
            "<div class='panel'>"
            f"<h2>Verdict {pill(label, kind)}</h2>"
            "<div class='calc-grid'>"
            f"<div><div class='label'>Buy % of market</div><div class='value'>{pct(acquisition_pct)}</div></div>"
            f"<div><div class='label'>Break-even sale price</div><div class='value'>{money(break_even_sale)}</div></div>"
            f"<div><div class='label'>Expected payout</div><div class='value'>{money(expected_payout)}</div></div>"
            f"<div><div class='label'>Gross profit</div><div class='value'>{money(gross_profit)}</div></div>"
            f"<div><div class='label'>Net profit</div><div class='value'>{money(net_profit)}</div></div>"
            f"<div><div class='label'>ROI</div><div class='value'>{pct(roi)}</div></div>"
            f"<div><div class='label'>Margin</div><div class='value'>{pct(margin)}</div></div>"
            f"<div><div class='label'>Target price ({config.green_max_pct}%)</div><div class='value'>{money(target_price)}</div></div>"
            f"<div><div class='label'>Max price ({config.yellow_max_pct}%)</div><div class='value'>{money(recommended_max)}</div></div>"
            "</div></div>"
        )

    def field(name: str, label: str, value: str, step: str = "0.01") -> str:
        return f"<label>{label}<input name='{name}' value='{escape(value)}' type='number' step='{step}'></label>"

    body = (
        brand_header("Buying calculator")
        + "<p class='subtitle'>\"Is this worth buying?\" -- Module 6.</p>"
        + "<form method='get' action='/buying-calculator' class='calc-form panel'>"
        + field("market_price", "Market value", market_price)
        + field("seller_price", "Seller asking price", seller_price)
        + field("expected_sale_price", "Expected sale price", expected_sale_price)
        + field("marketplace_fee_pct", "Marketplace commission %", marketplace_fee_pct)
        + field("payment_fee_pct", "Payment processing %", payment_fee_pct)
        + field("gst_pct", "GST/tax on sale %", gst_pct)
        + field("shipping_cost", "Shipping cost", shipping_cost)
        + field("packaging_cost", "Packaging cost", packaging_cost)
        + field("other_costs", "Other costs", other_costs)
        + "<button type='submit'>Calculate</button>"
        + "</form>"
        + result_html
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Buying calculator", body))


# --- Module 3: purchase lots -----------------------------------------------

@router.get("/purchase-lots", response_class=HTMLResponse)
def purchase_lots_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    lots = session.scalars(select(PurchaseLot).order_by(PurchaseLot.created_at.desc())).all()
    config = _default_thresholds(session)

    rows = []
    for lot in lots:
        items = session.scalars(select(PurchaseLotItem).where(PurchaseLotItem.purchase_lot_id == lot.id)).all()
        market_total = sum((i.market_value * i.quantity for i in items), Decimal("0"))
        basis = lot.offered_price or lot.asking_price
        acquisition_pct = (basis / market_total * 100) if market_total else None
        kind, label = traffic_light(acquisition_pct, config)
        rows.append(
            "<tr>"
            f"<td><a href='/purchase-lots/{escape(lot.id)}'>{escape(lot.source)}</a>{' — ' + escape(lot.seller) if lot.seller else ''}</td>"
            f"<td>${lot.asking_price:,.2f}</td>"
            f"<td>${market_total:,.2f}</td>"
            f"<td>{f'{acquisition_pct:.0f}%' if acquisition_pct is not None else '—'}</td>"
            f"<td>{pill(label, kind)}</td>"
            f"<td>{pill(lot.status.value.replace('_', ' '), 'neutral')}</td>"
            "</tr>"
        )
    table = (
        f"<div class='panel'><div class='table-wrap'><table>"
        f"<thead><tr><th>Lot</th><th>Asking</th><th>Market value</th><th>Buy %</th><th>Verdict</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=6>No purchase lots yet.</td></tr>'}</tbody></table></div></div>"
    )

    form = (
        "<div class='panel'><h2>New purchase lot</h2>"
        "<form method='post' action='/purchase-lots' class='calc-form'>"
        "<label>Source<input name='source' required placeholder='Facebook Marketplace Collection'></label>"
        "<label>Seller<input name='seller'></label>"
        "<label>Asking price<input name='asking_price' type='number' step='0.01' required></label>"
        f"<label>Target buy %<input name='target_buy_pct' type='number' step='0.1' value='{config.green_max_pct}'></label>"
        "<button type='submit'>Create lot</button>"
        "</form></div>"
    )

    body = (
        brand_header("Purchase lots")
        + "<p class='subtitle'>Collections being negotiated or bought -- Module 3.</p>"
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Purchase lots", body))


@router.post("/purchase-lots")
def create_purchase_lot(
    source: str = Form(...), seller: str = Form(""), asking_price: str = Form(...), target_buy_pct: str = Form("55"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    lot = PurchaseLot(
        id=str(uuid.uuid4()), source=source, seller=seller or None,
        asking_price=_dec(asking_price), target_buy_pct=_dec(target_buy_pct, "55"),
    )
    session.add(lot)
    session.commit()
    return RedirectResponse(f"/purchase-lots/{lot.id}", status_code=303)


@router.get("/purchase-lots/{lot_id}", response_class=HTMLResponse)
def purchase_lot_detail(lot_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    lot = session.get(PurchaseLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="purchase lot not found")
    items = session.scalars(select(PurchaseLotItem).where(PurchaseLotItem.purchase_lot_id == lot_id)).all()
    market_total = sum((i.market_value * i.quantity for i in items), Decimal("0"))
    config = _default_thresholds(session)
    recommended_offer = market_total * lot.target_buy_pct / 100

    item_rows = "".join(
        f"<tr><td>{escape(i.description)}</td><td>{i.quantity}</td><td>${i.market_value:,.2f}</td><td>${i.market_value * i.quantity:,.2f}</td></tr>"
        for i in items
    ) or "<tr><td colspan=4>No items added yet.</td></tr>"

    basis = lot.offered_price or lot.asking_price
    acquisition_pct = (basis / market_total * 100) if market_total else None
    kind, label = traffic_light(acquisition_pct, config)

    status_options = "".join(
        f"<option value='{s.value}'{' selected' if s == lot.status else ''}>{s.value.replace('_', ' ').title()}</option>"
        for s in PurchaseLotStatus
    )
    offered_price_value = str(lot.offered_price) if lot.offered_price is not None else ""

    body = (
        brand_header(f"Purchase lot: {lot.source}")
        + f"<div class='panel'><div class='calc-grid'>"
        + f"<div><div class='label'>Asking price</div><div class='value'>${lot.asking_price:,.2f}</div></div>"
        + f"<div><div class='label'>Market value (lines)</div><div class='value'>${market_total:,.2f}</div></div>"
        + f"<div><div class='label'>Recommended offer ({lot.target_buy_pct}%)</div><div class='value'>${recommended_offer:,.2f}</div></div>"
        + f"<div><div class='label'>Verdict</div><div class='value'>{pill(label, kind)}</div></div>"
        + "</div></div>"
        + "<div class='panel'><h2>Items</h2><div class='table-wrap'><table>"
        + f"<thead><tr><th>Item</th><th>Qty</th><th>Market ea.</th><th>Total</th></tr></thead><tbody>{item_rows}</tbody></table></div>"
        + "<form method='post' action='" + f"/purchase-lots/{lot_id}/items" + "' class='calc-form' style='margin-top:14px'>"
        + "<label>Description<input name='description' required></label>"
        + "<label>Market value (each)<input name='market_value' type='number' step='0.01' required></label>"
        + "<label>Quantity<input name='quantity' type='number' value='1' min='1'></label>"
        + "<button type='submit'>Add item</button></form></div>"
        + "<div class='panel'><h2>Deal status</h2>"
        + f"<form method='post' action='/purchase-lots/{lot_id}/status' class='calc-form'>"
        + f"<label>Offered price<input name='offered_price' type='number' step='0.01' value='{offered_price_value}'></label>"
        + f"<label>Status<select name='status'>{status_options}</select></label>"
        + "<button type='submit'>Update</button></form></div>"
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {lot.source}", body))


@router.post("/purchase-lots/{lot_id}/items")
def add_purchase_lot_item(
    lot_id: str, description: str = Form(...), market_value: str = Form(...), quantity: str = Form("1"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    if session.get(PurchaseLot, lot_id) is None:
        raise HTTPException(status_code=404, detail="purchase lot not found")
    session.add(PurchaseLotItem(
        id=str(uuid.uuid4()), purchase_lot_id=lot_id, description=description,
        market_value=_dec(market_value), quantity=int(quantity or 1),
    ))
    session.commit()
    return RedirectResponse(f"/purchase-lots/{lot_id}", status_code=303)


@router.post("/purchase-lots/{lot_id}/status")
def update_purchase_lot_status(
    lot_id: str, status: str = Form(...), offered_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    lot = session.get(PurchaseLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="purchase lot not found")
    lot.status = PurchaseLotStatus(status)
    if offered_price.strip():
        lot.offered_price = _dec(offered_price)
    if lot.status == PurchaseLotStatus.PURCHASED and lot.purchase_price is None:
        lot.purchase_price = lot.offered_price or lot.asking_price
    session.commit()
    return RedirectResponse(f"/purchase-lots/{lot_id}", status_code=303)


# --- Module 4: potential stock watchlist ------------------------------------

@router.get("/potential-stock", response_class=HTMLResponse)
def potential_stock_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    items = session.scalars(select(PotentialPurchase).order_by(PotentialPurchase.discovered_at.desc())).all()
    config = _default_thresholds(session)

    columns: dict[PotentialPurchaseStatus, list[str]] = {s: [] for s in PotentialPurchaseStatus}
    for it in items:
        acquisition_pct = (it.target_price / it.market_value * 100) if (it.target_price and it.market_value) else None
        kind, label = traffic_light(acquisition_pct, config)
        card = (
            "<div class='watch-card'>"
            f"<div class='watch-name'>{escape(it.description)}</div>"
            f"<div class='muted'>{escape(it.seller or '')}</div>"
            f"<div class='watch-prices'>ask ${it.asking_price or 0:,.2f} &middot; market ${it.market_value or 0:,.2f}</div>"
            f"<div>{pill(label, kind)}</div>"
            f"<a class='chip' href='/potential-stock/{escape(it.id)}'>Update</a>"
            "</div>"
        )
        columns[it.status].append(card)

    board = "".join(
        f"<div class='watch-col'><h3>{s.value.replace('_', ' ').title()} ({len(columns[s])})</h3>{''.join(columns[s]) or '<p class=\"muted\">—</p>'}</div>"
        for s in PotentialPurchaseStatus
    )

    form = (
        "<div class='panel'><h2>Add to watchlist</h2>"
        "<form method='post' action='/potential-stock' class='calc-form'>"
        "<label>Description<input name='description' required></label>"
        "<label>Seller<input name='seller'></label>"
        "<label>Source<input name='source' placeholder='Facebook Marketplace'></label>"
        "<label>URL<input name='url'></label>"
        "<label>Asking price<input name='asking_price' type='number' step='0.01'></label>"
        "<label>Market value<input name='market_value' type='number' step='0.01'></label>"
        f"<label>Target price ({config.green_max_pct}% of market)<input name='target_price' type='number' step='0.01'></label>"
        "<button type='submit'>Add</button>"
        "</form></div>"
    )

    body = (
        brand_header("Potential stock")
        + "<p class='subtitle'>The buy watchlist -- items not yet owned. Module 4.</p>"
        + f"<div class='watch-board'>{board}</div>"
        + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Potential stock", body))


@router.post("/potential-stock")
def create_potential_purchase(
    description: str = Form(...), seller: str = Form(""), source: str = Form(""), url: str = Form(""),
    asking_price: str = Form(""), market_value: str = Form(""), target_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    session.add(PotentialPurchase(
        id=str(uuid.uuid4()), description=description, seller=seller or None, source=source or None, url=url or None,
        asking_price=_dec(asking_price) if asking_price.strip() else None,
        market_value=_dec(market_value) if market_value.strip() else None,
        target_price=_dec(target_price) if target_price.strip() else None,
    ))
    session.commit()
    return RedirectResponse("/potential-stock", status_code=303)


@router.get("/potential-stock/{item_id}", response_class=HTMLResponse)
def potential_purchase_detail(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    item = session.get(PotentialPurchase, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    status_options = "".join(
        f"<option value='{s.value}'{' selected' if s == item.status else ''}>{s.value.replace('_', ' ').title()}</option>"
        for s in PotentialPurchaseStatus
    )
    body = (
        brand_header(item.description)
        + "<div class='panel'><form method='post' action='" + f"/potential-stock/{item_id}/status" + "' class='calc-form'>"
        + f"<label>Status<select name='status'>{status_options}</select></label>"
        + "<button type='submit'>Update</button></form></div>"
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {item.description}", body))


@router.post("/potential-stock/{item_id}/status")
def update_potential_purchase_status(
    item_id: str, status: str = Form(...),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item = session.get(PotentialPurchase, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    item.status = PotentialPurchaseStatus(status)
    session.commit()
    return RedirectResponse("/potential-stock", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#04101a;font-weight:700;cursor:pointer;margin-top:4px}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
.watch-board{display:flex;gap:14px;overflow-x:auto;padding-bottom:10px;margin-bottom:20px}
.watch-col{flex:0 0 220px;background:var(--panel);border:1px solid var(--panel-border);border-radius:12px;padding:12px}
.watch-col h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin:0 0 10px}
.watch-card{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:10px;padding:10px;margin-bottom:8px;
  display:flex;flex-direction:column;gap:6px}
.watch-name{font-weight:650;font-size:13.5px}
.watch-prices{font-size:12px;color:var(--text-dim)}
.muted{color:var(--text-dim);font-size:12px}
.chip{align-self:flex-start;font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid var(--panel-border);
  color:var(--text-dim);text-decoration:none}
.chip:hover{border-color:var(--accent);color:var(--accent)}
</style>"""
