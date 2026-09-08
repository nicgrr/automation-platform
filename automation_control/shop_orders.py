"""Order visibility for the operator -- Module 26.

Until now nothing in the admin app read ShopOrder at all: it's written by
the storefront's checkout and never read anywhere else. The only way to
see what had actually been ordered was a direct database query. This is
the fix -- a queue of what's been paid and needs packing, and a way to
mark it shipped with a tracking number.

Read-mostly by design, same shape as the rest of this app's review
pages: two actions only (mark packed, mark shipped), both logged like
every other mutating action here.
"""

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import ShopOrder, ShopOrderItem, ShopOrderStatus
from .ui import brand_header, page, pill

router = APIRouter(tags=["shop-orders"])

STATUS_PILLS = {
    ShopOrderStatus.PENDING_PAYMENT: "warn",
    ShopOrderStatus.PAID: "ok",
    ShopOrderStatus.PACKED: "ok",
    ShopOrderStatus.SHIPPED: "ok",
    ShopOrderStatus.CANCELLED: "bad",
    ShopOrderStatus.REFUNDED: "bad",
}

FILTERS = {
    "action_needed": "Needs action",  # paid or packed, not yet shipped
    "all": "All orders",
    ShopOrderStatus.PENDING_PAYMENT.value: "Pending payment",
    ShopOrderStatus.PAID.value: "Paid",
    ShopOrderStatus.PACKED.value: "Packed",
    ShopOrderStatus.SHIPPED.value: "Shipped",
    ShopOrderStatus.CANCELLED.value: "Cancelled",
}


def _money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _order_row(order: ShopOrder, items: list[ShopOrderItem]) -> str:
    lines = "".join(
        f"<li>{escape(item.title)} × {item.quantity} &mdash; {_money(item.unit_price * item.quantity)}</li>"
        for item in items
    )
    address = escape(order.ship_to_address).replace("\n", "<br>")

    actions = ""
    if order.status == ShopOrderStatus.PAID:
        actions = (
            f"<form method='post' action='/shop-orders/{escape(order.id)}/pack'>"
            "<button class='btn small'>Mark packed</button></form>"
        )
    elif order.status == ShopOrderStatus.PACKED:
        actions = (
            f"<form method='post' action='/shop-orders/{escape(order.id)}/ship' class='ship-form'>"
            "<input name='tracking_number' placeholder='Tracking number' required>"
            "<button class='btn small'>Mark shipped</button></form>"
        )
    elif order.status == ShopOrderStatus.SHIPPED and order.tracking_number:
        actions = f"<span class='muted'>Tracking: {escape(order.tracking_number)}</span>"

    return (
        "<div class='panel order-card'>"
        "<div class='order-head'>"
        f"<div><strong>{escape(order.reference)}</strong>"
        f"<span class='muted'> · {order.created_at:%Y-%m-%d %H:%M}</span></div>"
        f"{pill(order.status.value.replace('_', ' ').upper(), STATUS_PILLS.get(order.status, 'neutral'))}"
        "</div>"
        "<div class='order-grid'>"
        "<div>"
        f"<div>{escape(order.ship_to_name)} &lt;{escape(order.email)}&gt;</div>"
        f"<div class='muted'>{address}, {escape(order.ship_to_country)}</div>"
        f"<ul class='order-items'>{lines}</ul>"
        "</div>"
        "<div class='order-total'>"
        f"<div class='detail-price' style='font-size:22px'>{_money(order.grand_total)}</div>"
        f"<div class='muted'>{order.zone.value.upper()} · {escape(order.currency)}</div>"
        f"<div style='margin-top:10px'>{actions}</div>"
        "</div></div></div>"
    )


@router.get("/shop-orders", response_class=HTMLResponse)
def shop_orders_page(
    status: str = Query("action_needed"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    stmt = select(ShopOrder).order_by(ShopOrder.created_at.desc())
    if status == "action_needed":
        stmt = stmt.where(ShopOrder.status.in_([ShopOrderStatus.PAID, ShopOrderStatus.PACKED]))
    elif status != "all" and status in {s.value for s in ShopOrderStatus}:
        stmt = stmt.where(ShopOrder.status == ShopOrderStatus(status))
    orders = session.scalars(stmt.limit(200)).all()

    items_by_order: dict[str, list[ShopOrderItem]] = {}
    if orders:
        for item in session.scalars(
            select(ShopOrderItem).where(ShopOrderItem.order_id.in_([o.id for o in orders]))
        ):
            items_by_order.setdefault(item.order_id, []).append(item)

    cards = "".join(_order_row(o, items_by_order.get(o.id, [])) for o in orders) or (
        "<div class='panel'><p class='muted'>Nothing here.</p></div>"
    )

    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' href='/shop-orders?status={key}'>{escape(label)}</a>"
        for key, label in FILTERS.items()
    )

    body = (
        brand_header("Shop orders")
        + f"<p class='subtitle'>{len(orders)} order(s).</p>"
        + f"<div class='filter-tabs'>{filter_bar}</div>"
        + cards + _STYLE
    )
    return HTMLResponse(page("EzBay — Shop orders", body))


@router.post("/shop-orders/{order_id}/pack")
def mark_packed(order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status == ShopOrderStatus.PAID:
        order.status = ShopOrderStatus.PACKED
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_order.pack",
                    resource_type="shop_order", resource_id=order_id, outcome="success",
                    correlation_id=order.reference, details={})
    return RedirectResponse("/shop-orders", status_code=303)


@router.post("/shop-orders/{order_id}/ship")
def mark_shipped(
    order_id: str, tracking_number: str = Form(...),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status in (ShopOrderStatus.PAID, ShopOrderStatus.PACKED):
        order.status = ShopOrderStatus.SHIPPED
        order.tracking_number = tracking_number.strip()
        order.shipped_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_order.ship",
                    resource_type="shop_order", resource_id=order_id, outcome="success",
                    correlation_id=order.reference, details={"tracking_number": order.tracking_number})
    return RedirectResponse("/shop-orders", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.order-card{margin-bottom:14px}
.order-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.order-grid{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start}
.order-items{list-style:none;padding:0;margin:8px 0 0;font-size:13.5px;color:var(--text-dim)}
.order-total{text-align:right;min-width:160px}
.ship-form{display:flex;gap:8px}
.ship-form input{padding:6px 9px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px;width:150px}
.btn.small{padding:6px 12px;font-size:13px}
@media (max-width:640px){.order-grid{grid-template-columns:1fr}.order-total{text-align:left}}
</style>"""
