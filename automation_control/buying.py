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

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    BuyThresholdConfig, CatalogItem, PotentialPurchase, PotentialPurchaseStatus,
    PurchaseLot, PurchaseLotItem, PurchaseLotStatus, Supplier, SupplierProduct,
)
from .purchase_receiving import receive_purchase_lot
from .ui import brand_header, page, pill, quick_price_widget

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


def _positive_int(value: str) -> int:
    try:
        quantity = int(value.strip())
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="quantity must be a whole number") from exc
    if quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be at least one")
    return quantity


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
        + quick_price_widget("market_price", label="Identify a card to fill in market value")
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
def purchase_lots_page(
    supplier_id: str = Query(""), user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    lots = session.scalars(select(PurchaseLot).order_by(PurchaseLot.created_at.desc())).all()
    config = _default_thresholds(session)
    suppliers = session.scalars(select(Supplier).order_by(Supplier.name)).all()
    supplier_names = {supplier.id: supplier.name for supplier in suppliers}

    rows = []
    for lot in lots:
        items = session.scalars(select(PurchaseLotItem).where(PurchaseLotItem.purchase_lot_id == lot.id)).all()
        market_total = sum((i.market_value * i.quantity for i in items), Decimal("0"))
        basis = lot.offered_price or lot.asking_price
        acquisition_pct = (basis / market_total * 100) if market_total else None
        kind, label = traffic_light(acquisition_pct, config)
        rows.append(
            "<tr>"
            f"<td><a href='/purchase-lots/{escape(lot.id)}'>{escape(lot.source)}</a>"
            f"{' — ' + escape(supplier_names.get(lot.supplier_id, lot.seller or '')) if lot.supplier_id or lot.seller else ''}</td>"
            f"<td>${lot.asking_price:,.2f}</td>"
            f"<td>${market_total:,.2f}</td>"
            f"<td>{f'{acquisition_pct:.0f}%' if acquisition_pct is not None else '—'}</td>"
            f"<td>{pill(label, kind)}</td>"
            f"<td>{pill(lot.status.value.replace('_', ' '), 'neutral')}"
            f"{' ' + pill('inventory received', 'ok') if lot.inventory_received_at else ''}</td>"
            "</tr>"
        )
    table = (
        f"<div class='panel'><div class='table-wrap'><table>"
        f"<thead><tr><th>Lot</th><th>Asking</th><th>Market value</th><th>Buy %</th><th>Verdict</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=6>No purchase lots yet.</td></tr>'}</tbody></table></div></div>"
    )

    supplier_options = "<option value=''>Private seller / other</option>" + "".join(
        f"<option value='{escape(supplier.id)}'{' selected' if supplier.id == supplier_id else ''}>"
        f"{escape(supplier.name)}</option>" for supplier in suppliers
    )

    form = (
        "<div class='panel'><h2>New purchase lot</h2>"
        "<form method='post' action='/purchase-lots' class='calc-form'>"
        f"<label>Supplier<select name='supplier_id'>{supplier_options}</select></label>"
        "<label>Source<input name='source' required placeholder='Facebook Marketplace Collection'></label>"
        "<label>Seller<input name='seller'></label>"
        "<label>Asking price<input name='asking_price' type='number' step='0.01' required></label>"
        f"<label>Target buy %<input name='target_buy_pct' type='number' step='0.1' value='{config.green_max_pct}'></label>"
        "<button type='submit'>Create lot</button>"
        "</form></div>"
    )

    body = (
        brand_header("Purchase lots")
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Purchase lots", body))


@router.post("/purchase-lots")
def create_purchase_lot(
    source: str = Form(...), seller: str = Form(""), asking_price: str = Form(...), target_buy_pct: str = Form("55"),
    supplier_id: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    clean_source = source.strip()[:256]
    if not clean_source:
        raise HTTPException(status_code=400, detail="purchase source is required")
    supplier = session.get(Supplier, supplier_id) if supplier_id else None
    if supplier_id and supplier is None:
        raise HTTPException(status_code=404, detail="supplier not found")
    asking = _amount(asking_price, "asking price", positive=True)
    target = _amount(target_buy_pct, "target buy percentage", positive=True)
    if target > 100:
        raise HTTPException(status_code=400, detail="target buy percentage cannot exceed 100")
    lot = PurchaseLot(
        id=str(uuid.uuid4()), source=clean_source,
        seller=supplier.name if supplier else seller.strip() or None,
        supplier_id=supplier.id if supplier else None,
        asking_price=asking, target_buy_pct=target,
    )
    session.add(lot)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="purchase_lot.create",
                 resource_type="purchase_lot", resource_id=lot.id, outcome="success",
                 correlation_id=lot.id, details={"supplier_id": lot.supplier_id})
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
        f"<tr><td>{escape(i.description)}</td><td>{i.quantity}</td><td>${i.market_value:,.2f}</td>"
        f"<td>${i.market_value * i.quantity:,.2f}</td>"
        f"<td>{f'${i.quoted_unit_cost:,.2f}' if i.quoted_unit_cost is not None else '—'}</td>"
        f"<td>{f'${i.allocated_total_cost:,.2f}' if i.allocated_total_cost is not None else '—'}</td>"
        f"<td>{'Linked' if i.catalog_item_id else pill('Missing', 'bad')}</td>"
        f"<td>{'Received' if i.inventory_item_id else '—'}</td></tr>"
        for i in items
    ) or "<tr><td colspan=8>No items added yet.</td></tr>"

    basis = lot.offered_price or lot.asking_price
    acquisition_pct = (basis / market_total * 100) if market_total else None
    kind, label = traffic_light(acquisition_pct, config)

    status_options = "".join(
        f"<option value='{s.value}'{' selected' if s == lot.status else ''}>{s.value.replace('_', ' ').title()}</option>"
        for s in PurchaseLotStatus
    )
    offered_price_value = str(lot.offered_price) if lot.offered_price is not None else ""
    shipping_cost_value = str(lot.shipping_cost) if lot.shipping_cost is not None else ""
    automated = lot.retailer_id is not None
    supplier_products = session.scalars(
        select(SupplierProduct).where(SupplierProduct.supplier_id == lot.supplier_id)
        .order_by(SupplierProduct.description)
    ).all() if lot.supplier_id else []
    supplier_product_options = "".join(
        f"<option value='{escape(product.id)}'>{escape(product.description)} · ${product.cost:,.2f}</option>"
        for product in supplier_products if product.catalog_item_id
    )
    if lot.supplier_id:
        add_panel = (
            "<div class='panel'><h2>Add supplier product</h2>"
            + (
                f"<form method='post' action='/purchase-lots/{lot_id}/items' class='calc-form'>"
                f"<label>Supplier product<select name='supplier_product_id' required>{supplier_product_options}</select></label>"
                "<label>Expected market value (each)<input name='market_value' type='number' step='0.01' required></label>"
                "<label>Quantity<input name='quantity' type='number' value='1' min='1'></label>"
                "<label>Condition<input name='condition' value='Sealed' required></label>"
                "<button type='submit'>Add item</button></form>"
                if supplier_product_options else
                f"<p class='muted'>Link catalogue products on <a href='/suppliers/{escape(lot.supplier_id)}'>the supplier page</a> first.</p>"
            ) + "</div>"
        )
    else:
        add_panel = (
            "<div class='panel'><h2>Add catalogue-linked item</h2>"
            + quick_price_widget("market_value", "description")
            + f"<form method='post' action='/purchase-lots/{lot_id}/items' class='calc-form'>"
            + "<label>Description<input name='description' required></label>"
            + "<label>Catalogue item ID<input name='catalog_item_id' required></label>"
            + "<label>Market value (each)<input name='market_value' type='number' step='0.01' required></label>"
            + "<label>Quantity<input name='quantity' type='number' value='1' min='1'></label>"
            + "<label>Condition<input name='condition' value='Near Mint' required></label>"
            + "<button type='submit'>Add item</button></form></div>"
        )
    status_panel = (
        "<div class='panel'><h2>Deal status &amp; inventory receipt</h2>"
        "<p class='muted'>Selecting Purchased receives every line into inventory and permanently locks its cost allocation.</p>"
        f"<form method='post' action='/purchase-lots/{lot_id}/status' class='calc-form'>"
        f"<label>Final offered/purchase price<input name='offered_price' type='number' step='0.01' value='{offered_price_value}'></label>"
        f"<label>Shipping paid<input name='shipping_cost' type='number' min='0' step='0.01' value='{shipping_cost_value}' "
        "placeholder='Required; enter 0 for free pickup'></label>"
        f"<label>Status<select name='status'>{status_options}</select></label>"
        "<button type='submit'>Update status</button></form></div>"
    )
    lot_controls = (
        "<div class='notice'>This lot was created automatically from retailer purchases. "
        "Use Retailer Goals to record or refund its lines so inventory, fund and loyalty totals stay in sync.</div>"
        if automated else
        "<div class='notice ok'>Inventory was received from this lot. Cost allocation and stock links are locked.</div>"
        if lot.inventory_received_at else add_panel + status_panel
    )

    body = (
        brand_header(f"Purchase lot: {lot.source}")
        + f"<div class='panel'><div class='calc-grid'>"
        + f"<div><div class='label'>Asking price</div><div class='value'>${lot.asking_price:,.2f}</div></div>"
        + f"<div><div class='label'>Market value (lines)</div><div class='value'>${market_total:,.2f}</div></div>"
        + f"<div><div class='label'>Recommended offer ({lot.target_buy_pct}%)</div><div class='value'>${recommended_offer:,.2f}</div></div>"
        + f"<div><div class='label'>Verdict</div><div class='value'>{pill(label, kind)}</div></div>"
        + "</div></div>"
        + "<div class='panel'><h2>Items</h2><div class='table-wrap'><table>"
        + f"<thead><tr><th>Item</th><th>Qty</th><th>Market ea.</th><th>Total</th><th>Quoted ea.</th><th>Allocated</th><th>Catalogue</th><th>Inventory</th></tr></thead><tbody>{item_rows}</tbody></table></div></div>"
        + lot_controls
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {lot.source}", body))


@router.post("/purchase-lots/{lot_id}/items")
def add_purchase_lot_item(
    lot_id: str, description: str = Form(""), catalog_item_id: str = Form(""),
    supplier_product_id: str = Form(""), market_value: str = Form(...),
    quantity: str = Form("1"), condition: str = Form("Sealed"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    lot = session.get(PurchaseLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="purchase lot not found")
    if lot.retailer_id is not None:
        raise HTTPException(status_code=409, detail="retailer purchase lots are managed from Retailer Goals")
    if lot.inventory_received_at is not None or lot.status == PurchaseLotStatus.PURCHASED:
        raise HTTPException(status_code=409, detail="inventory has already been received from this lot")
    supplier_product = session.get(SupplierProduct, supplier_product_id) if supplier_product_id else None
    if lot.supplier_id:
        if supplier_product is None or supplier_product.supplier_id != lot.supplier_id:
            raise HTTPException(status_code=400, detail="choose a product linked to this supplier")
        catalog_item_id = supplier_product.catalog_item_id or ""
        description = supplier_product.description
    catalog = session.get(CatalogItem, catalog_item_id) if catalog_item_id else None
    if catalog is None:
        raise HTTPException(status_code=404, detail="catalogue item not found")
    clean_condition = condition.strip()[:64]
    if not clean_condition:
        raise HTTPException(status_code=400, detail="condition is required")
    item = PurchaseLotItem(
        id=str(uuid.uuid4()), purchase_lot_id=lot_id,
        supplier_product_id=supplier_product.id if supplier_product else None,
        catalog_item_id=catalog.id, description=(description.strip() or catalog.name)[:256],
        market_value=_amount(market_value, "market value", positive=True),
        quoted_unit_cost=supplier_product.cost if supplier_product else None,
        quantity=_positive_int(quantity), condition=clean_condition,
    )
    session.add(item)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="purchase_lot.item.add",
                 resource_type="purchase_lot_item", resource_id=item.id, outcome="success",
                 correlation_id=lot.id, details={"catalog_item_id": catalog.id})
    return RedirectResponse(f"/purchase-lots/{lot_id}", status_code=303)


@router.post("/purchase-lots/{lot_id}/status")
def update_purchase_lot_status(
    lot_id: str, status: str = Form(...), offered_price: str = Form(""), shipping_cost: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    lot = session.get(PurchaseLot, lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="purchase lot not found")
    if lot.retailer_id is not None:
        raise HTTPException(status_code=409, detail="retailer purchase lots are managed from Retailer Goals")
    if lot.inventory_received_at is not None:
        raise HTTPException(status_code=409, detail="received purchase lots are locked")
    try:
        selected = PurchaseLotStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid purchase-lot status") from exc
    if offered_price.strip():
        lot.offered_price = _amount(offered_price, "offered price", positive=True)
    if shipping_cost.strip():
        lot.shipping_cost = _amount(shipping_cost, "shipping")
    if selected == PurchaseLotStatus.PURCHASED:
        lot.purchase_price = lot.offered_price or lot.asking_price
        try:
            received = receive_purchase_lot(session, lot)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="purchase_lot.receive",
                     resource_type="purchase_lot", resource_id=lot.id, outcome="success",
                     correlation_id=lot.id, details={"inventory_rows": len(received)})
    else:
        lot.status = selected
        session.commit()
    return RedirectResponse(f"/purchase-lots/{lot_id}", status_code=303)


# --- Module 4: potential stock watchlist ------------------------------------

@router.get("/potential-stock", response_class=HTMLResponse)
def potential_stock_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    # Kept as a compatibility route during the observation window. The
    # old table was empty in production; all new candidates now live on
    # their discovered MarketListing instead of being copied here.
    return RedirectResponse("/market-watch?view=candidates", status_code=303)


@router.post("/potential-stock")
def create_potential_purchase(
    description: str = Form(...), seller: str = Form(""), source: str = Form(""), url: str = Form(""),
    asking_price: str = Form(""), market_value: str = Form(""), target_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    return RedirectResponse(
        "/market-watch?view=candidates&error=Add+the+marketplace+listing+first%2C+then+track+it+as+a+candidate",
        status_code=303,
    )


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
.calc-form input,.calc-form select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
.watch-board{display:flex;gap:14px;overflow-x:auto;padding-bottom:10px;margin-bottom:20px}
.watch-col{flex:0 0 220px;background:var(--panel);border:1px solid var(--panel-border);border-radius:12px;padding:12px}
.watch-col h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin:0 0 10px}
.watch-card{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:10px;padding:10px;margin-bottom:8px;
  display:flex;flex-direction:column;gap:6px}
.watch-name{font-weight:650;font-size:13.5px}
.watch-prices{font-size:12px;color:var(--text-dim)}
.muted{color:var(--text-dim);font-size:12px}
.chip{align-self:flex-start;font-size:11px;padding:4px 10px;border-radius:999px;border:1px solid var(--panel-border);
  color:var(--text-dim);text-decoration:none}
.chip:hover{border-color:var(--accent);color:var(--accent)}
</style>"""
