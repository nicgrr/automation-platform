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

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_locate import LOCATE_STYLE, PACK_STYLE, line_detail, location_pill_html, pack_card_html, thumb_src
from .config import get_settings
from .database import get_session
from .ebay_sync import sync_ebay_after_sale
from .labels import label_buttons_html
from .models import ShopListing, ShopOrder, ShopOrderItem, ShopOrderStatus
from .order_stock import StockUnavailable, cancel_pending_order, mark_order_paid, restore_order_stock
from .ui import brand_header, page, pill

router = APIRouter(tags=["shop-orders"])
STRIPE_SESSIONS_API = "https://api.stripe.com/v1/checkout/sessions"

STATUS_PILLS = {
    ShopOrderStatus.PENDING_PAYMENT: "warn",
    ShopOrderStatus.PAID: "ok",
    ShopOrderStatus.PACKED: "ok",
    ShopOrderStatus.SHIPPED: "ok",
    ShopOrderStatus.CANCELLED: "bad",
    ShopOrderStatus.REFUNDED: "bad",
}

FILTERS = {
    "action_needed": "Needs action",  # unpaid, paid or packed, not yet shipped
    "all": "All orders",
    ShopOrderStatus.PENDING_PAYMENT.value: "Pending payment",
    ShopOrderStatus.PAID.value: "Paid",
    ShopOrderStatus.PACKED.value: "Packed",
    ShopOrderStatus.SHIPPED.value: "Shipped",
    ShopOrderStatus.CANCELLED.value: "Cancelled",
    ShopOrderStatus.REFUNDED.value: "Refunded",
}


def _money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _item_inventory_id(session: Session, item: ShopOrderItem) -> str | None:
    """The physical holding behind this line item, or None -- a Bulk
    Master lot line or the synthetic discount line checkout.py inserts
    both have `listing_id=None`, per ShopOrderItem's own schema."""
    if not item.listing_id:
        return None
    listing = session.get(ShopListing, item.listing_id)
    return listing.inventory_item_id if listing else None


def _item_line_html(session: Session, item: ShopOrderItem) -> str:
    price = _money(item.unit_price * item.quantity)
    detail = line_detail(session, _item_inventory_id(session, item))
    if detail is None:
        return (
            "<li class='order-line'><div class='line-text'>"
            f"<div><strong>{escape(item.title)}</strong> × {item.quantity} &mdash; {price}</div>"
            "</div></li>"
        )

    src = thumb_src(detail)
    thumb = f"<img class='line-thumb' src='{src}' alt='' loading='lazy'>" if src else "<div class='line-thumb noart'></div>"
    meta_bits = [b for b in (detail["set_name"], f"#{detail['number']}" if detail["number"] else "", detail["rarity"], detail["variant"]) if b]
    meta = " · ".join(escape(b) for b in meta_bits)
    return (
        "<li class='order-line'>"
        f"{thumb}"
        "<div class='line-text'>"
        f"<div><strong>{escape(item.title)}</strong> × {item.quantity} &mdash; {price}</div>"
        f"<div class='muted line-meta'>{meta}</div>"
        f"<div>{location_pill_html(detail)}</div>"
        "</div></li>"
    )


def _order_actions_html(
    order: ShopOrder, *, size: str = "small", pack_sheet_link: bool = True, label_link: bool = True,
) -> str:
    """The pack/ship actions for one order, shared by the list row and the
    order-detail page so the two never drift out of sync with each other.
    `pack_sheet_link` is left out on the detail page itself -- linking to
    the pack sheet from the page a click on that same order already opens
    would just be a loop back to a narrower view of what's already here."""
    btn = f"btn {size}".strip()
    label = label_buttons_html('shop', order.id, btn) if label_link else ""
    sheet = (
        f"<a class='{btn}' href='/shop-orders/{escape(order.id)}/pack-sheet'>Pack assistant</a>"
        if pack_sheet_link else ""
    )
    refund = (
        "<details class='refund-action'><summary>Record refund</summary>"
        f"<form method='post' action='/shop-orders/{escape(order.id)}/refund'>"
        "<label><input type='checkbox' name='refund_confirmed' value='true' required> "
        "Refund completed in Stripe/bank</label>"
        "<label><input type='checkbox' name='restock' value='true'> Returned stock is back in hand</label>"
        "<input name='reason' placeholder='Reason (optional)' maxlength='500'>"
        f"<button class='{btn} danger'>Record refunded</button></form></details>"
    )
    if order.status == ShopOrderStatus.PENDING_PAYMENT:
        return (
            f"<form method='post' action='/shop-orders/{escape(order.id)}/paid'>"
            "<label class='confirm-action'><input type='checkbox' name='payment_confirmed' value='true' required> "
            "Funds received</label>"
            f"<button class='{btn}'>Record manual payment</button></form>"
            f"<form method='post' action='/shop-orders/{escape(order.id)}/cancel'>"
            f"<button class='{btn} danger'>Cancel &amp; restore stock</button></form>"
        )
    if order.status == ShopOrderStatus.PAID:
        return (
            f"{sheet}{label}"
            f"<form method='post' action='/shop-orders/{escape(order.id)}/pack'>"
            f"<button class='{btn}'>Mark packed</button></form>{refund}"
        )
    if order.status == ShopOrderStatus.PACKED:
        return (
            f"{sheet}{label}"
            f"<form method='post' action='/shop-orders/{escape(order.id)}/ship' class='ship-form'>"
            "<input name='tracking_number' placeholder='Tracking number' required>"
            f"<button class='{btn}'>Confirm shipped</button></form>{refund}"
        )
    if order.status == ShopOrderStatus.SHIPPED and order.tracking_number:
        return f"<span class='muted'>Shipped &middot; tracking {escape(order.tracking_number)}</span>{refund}"
    if order.status == ShopOrderStatus.SHIPPED:
        return refund
    return ""


def _order_row(session: Session, order: ShopOrder, items: list[ShopOrderItem]) -> str:
    lines = "".join(_item_line_html(session, item) for item in items)
    address = escape(order.ship_to_address).replace("\n", "<br>")
    actions = _order_actions_html(order)

    return (
        "<div class='panel order-card'>"
        "<div class='order-head'>"
        f"<div><a class='order-ref-link' href='/shop-orders/{escape(order.id)}'><strong>{escape(order.reference)}</strong></a>"
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
        f"<div class='order-actions' style='margin-top:10px'>{actions}</div>"
        "</div></div></div>"
    )


@router.get("/shop-orders", response_class=HTMLResponse)
def shop_orders_page(
    status: str = Query("action_needed"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    stmt = select(ShopOrder).order_by(ShopOrder.created_at.desc())
    if status == "action_needed":
        stmt = stmt.where(ShopOrder.status.in_([
            ShopOrderStatus.PENDING_PAYMENT, ShopOrderStatus.PAID, ShopOrderStatus.PACKED,
        ]))
    elif status != "all" and status in {s.value for s in ShopOrderStatus}:
        stmt = stmt.where(ShopOrder.status == ShopOrderStatus(status))
    orders = session.scalars(stmt.limit(200)).all()

    items_by_order: dict[str, list[ShopOrderItem]] = {}
    if orders:
        for item in session.scalars(
            select(ShopOrderItem).where(ShopOrderItem.order_id.in_([o.id for o in orders]))
        ):
            items_by_order.setdefault(item.order_id, []).append(item)

    cards = "".join(_order_row(session, o, items_by_order.get(o.id, [])) for o in orders) or (
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
        + cards + _STYLE + LOCATE_STYLE
    )
    return HTMLResponse(page("EzBay — Shop orders", body))


@router.get("/shop-orders/{order_id}", response_class=HTMLResponse)
def order_detail(
    order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    """One order's full detail: every card on it, the customer's address,
    and whatever action it still needs -- reachable by clicking any order
    on the list, at any status, not just the ones still awaiting packing
    (see /pack-sheet, which stays workflow-focused and PAID/PACKED-only).
    No print button here yet -- that's a deliberately separate, later
    addition, not something this page should stub out half-built."""
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    items = session.scalars(select(ShopOrderItem).where(ShopOrderItem.order_id == order_id)).all()
    lines = "".join(_item_line_html(session, item) for item in items) or (
        "<p class='muted'>No line items on this order.</p>"
    )
    address = escape(order.ship_to_address).replace("\n", "<br>")
    actions = _order_actions_html(order, size="", pack_sheet_link=order.status in (ShopOrderStatus.PAID, ShopOrderStatus.PACKED))

    timeline_bits = [f"Placed {order.created_at:%Y-%m-%d %H:%M}"]
    if order.paid_at:
        timeline_bits.append(f"paid {order.paid_at:%Y-%m-%d %H:%M}")
    if order.shipped_at:
        timeline_bits.append(f"shipped {order.shipped_at:%Y-%m-%d %H:%M}")
    if order.cancelled_at:
        timeline_bits.append(f"cancelled {order.cancelled_at:%Y-%m-%d %H:%M}")
    if order.refunded_at:
        timeline_bits.append(f"refunded {order.refunded_at:%Y-%m-%d %H:%M}")

    body = (
        brand_header(f"Order {escape(order.reference)}")
        + "<a class='back-link' href='/shop-orders'>&larr; Back to shop orders</a>"
        + "<div class='panel order-detail'>"
        + "<div class='order-head'>"
        + f"<div><strong>{escape(order.reference)}</strong>"
        + f"<span class='muted'> · {escape(' · '.join(timeline_bits))}</span></div>"
        + f"{pill(order.status.value.replace('_', ' ').upper(), STATUS_PILLS.get(order.status, 'neutral'))}"
        + "</div>"
        + "<div class='order-grid'>"
        + "<div>"
        + "<div class='detail-subhead'>Ship to</div>"
        + f"<div>{escape(order.ship_to_name)} &lt;{escape(order.email)}&gt;</div>"
        + f"<div class='muted'>{address}, {escape(order.ship_to_country)}</div>"
        + "<div class='detail-subhead' style='margin-top:16px'>Cards</div>"
        + f"<ul class='order-items'>{lines}</ul>"
        + "</div>"
        + "<div class='order-total'>"
        + f"<div class='muted'>Items {_money(order.items_total)}</div>"
        + f"<div class='muted'>Shipping {_money(order.shipping_total)}</div>"
        + f"<div class='detail-price' style='font-size:22px'>{_money(order.grand_total)}</div>"
        + f"<div class='muted'>{order.zone.value.upper()} · {escape(order.currency)}</div>"
        + f"<div class='order-actions' style='margin-top:10px'>{actions}</div>"
        + "</div></div>"
        + (f"<div class='order-notes muted'>{escape(order.notes)}</div>" if order.notes else "")
        + "</div>"
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — Order {order.reference}", body))


def _pack_card_html(session: Session, item: ShopOrderItem) -> str:
    return pack_card_html(
        session, _item_inventory_id(session, item), title=item.title, qty=item.quantity,
        price_html=_money(item.unit_price * item.quantity),
        back_url=f"/shop-orders/{item.order_id}/pack-sheet",
        empty_note="No single physical item behind this line (a bulk lot, or a discount) "
                   "-- nothing here to individually locate.",
    )


@router.get("/shop-orders/{order_id}/pack-sheet", response_class=HTMLResponse)
def pack_sheet(
    order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    """The packing assistant: one card per line item with a photo, its set
    and printing details, and wherever it's currently marked as living --
    plus a one-field form to set or correct that location on the spot.
    Deliberately reuses the exact same 'mark packed'/'mark shipped' actions
    as the /shop-orders list, just closer to the information a person
    actually needs while physically pulling cards."""
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    items = session.scalars(select(ShopOrderItem).where(ShopOrderItem.order_id == order_id)).all()

    cards = "".join(_pack_card_html(session, item) for item in items) or (
        "<div class='panel'><p class='muted'>No line items on this order.</p></div>"
    )

    actions = _order_actions_html(order, size="", pack_sheet_link=False, label_link=False) or (
        f"<span class='muted'>{escape(order.status.value.replace('_', ' ').title())}</span>"
    )

    address = escape(order.ship_to_address).replace("\n", "<br>")
    body = (
        brand_header(f"Pack {escape(order.reference)}")
        + "<a class='back-link' href='/shop-orders'>&larr; Back to shop orders</a>"
        + f"<p class='subtitle'>{escape(order.ship_to_name)} &mdash; {address}, {escape(order.ship_to_country)}</p>"
        + f"<div class='pack-label-actions'>{label_buttons_html('shop', order.id, 'btn')}</div>"
        + f"<div class='pack-grid'>{cards}</div>"
        + f"<div class='panel pack-footer'>{actions}</div>"
        + _STYLE + PACK_STYLE + LOCATE_STYLE
    )
    return HTMLResponse(page(f"EzBay — Pack {order.reference}", body))


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


@router.post("/shop-orders/{order_id}/paid")
def mark_manual_payment(
    order_id: str, background_tasks: BackgroundTasks,
    payment_confirmed: bool = Form(False),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if not payment_confirmed:
        raise HTTPException(status_code=400, detail="confirm that funds were received first")
    try:
        claimed, changed = mark_order_paid(session, order)
    except StockUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    for listing_id, quantity in changed:
        background_tasks.add_task(sync_ebay_after_sale, listing_id, quantity)
    if claimed:
        record_event(session, actor_type="user", actor_id=user, action="shop_order.manual_payment",
                     resource_type="shop_order", resource_id=order_id, outcome="success",
                     correlation_id=order.reference, details={})
    return RedirectResponse(f"/shop-orders/{order_id}", status_code=303)


@router.post("/shop-orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status == ShopOrderStatus.PENDING_PAYMENT:
        if order.stripe_session_id:
            await _expire_stripe_session(order.stripe_session_id)
        cancelled, changed = cancel_pending_order(session, order)
        session.commit()
        if cancelled:
            record_event(session, actor_type="user", actor_id=user, action="shop_order.cancel",
                         resource_type="shop_order", resource_id=order_id, outcome="success",
                         correlation_id=order.reference, details={"stock_restored": bool(changed)})
    return RedirectResponse(f"/shop-orders/{order_id}", status_code=303)


async def _expire_stripe_session(session_id: str) -> None:
    """Make cancellation recoverable across a Stripe/DB crash boundary.

    GET first: if an earlier attempt expired Stripe and crashed before the
    local commit, a retry sees ``expired`` and safely continues. A completed
    payment is never cancelled as unpaid.
    """
    secret = getattr(get_settings(), "stripe_secret_key", None)
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe is not configured; cannot safely cancel this session")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{STRIPE_SESSIONS_API}/{session_id}", auth=(secret, ""))
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="could not verify the Stripe session")
            status = (response.json() or {}).get("status")
            if status == "expired":
                return
            if status == "complete":
                raise HTTPException(status_code=409, detail="Stripe reports this session completed; reconcile payment first")
            response = await client.post(f"{STRIPE_SESSIONS_API}/{session_id}/expire", auth=(secret, ""))
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="Stripe did not expire the checkout session")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="could not reach Stripe to cancel this session") from exc


@router.post("/shop-orders/{order_id}/refund")
def record_refund(
    order_id: str,
    refund_confirmed: bool = Form(False), restock: bool = Form(False), reason: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    """Record a refund already completed outside EZBay.

    This route deliberately does not call Stripe. The operator must explicitly
    confirm the money has been returned; stock is restored only when they also
    confirm the physical goods are back in hand.
    """
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if not refund_confirmed:
        raise HTTPException(status_code=400, detail="confirm the external refund first")
    if order.status not in (ShopOrderStatus.PAID, ShopOrderStatus.PACKED, ShopOrderStatus.SHIPPED):
        raise HTTPException(status_code=409, detail="only a paid order can be refunded")

    changed = restore_order_stock(session, order) if restock else []
    order.status = ShopOrderStatus.REFUNDED
    order.refunded_at = datetime.now(UTC)
    clean_reason = reason.strip()[:500]
    if clean_reason:
        order.notes = f"{order.notes}\n".lstrip() + f"Refund: {clean_reason}"
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="shop_order.refund_recorded",
                 resource_type="shop_order", resource_id=order_id, outcome="success",
                 correlation_id=order.reference,
                 details={"stock_restored": bool(changed), "reason": clean_reason})
    return RedirectResponse(f"/shop-orders/{order_id}", status_code=303)


@router.post("/shop-orders/{order_id}/ship")
def mark_shipped(
    order_id: str, tracking_number: str = Form(...),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    order = session.get(ShopOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status in (ShopOrderStatus.PAID, ShopOrderStatus.PACKED):
        clean_tracking = tracking_number.strip()
        if not clean_tracking or len(clean_tracking) > 128:
            raise HTTPException(status_code=400, detail="tracking number must be 1 to 128 characters")
        order.status = ShopOrderStatus.SHIPPED
        order.tracking_number = clean_tracking
        order.shipped_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_order.ship",
                    resource_type="shop_order", resource_id=order_id, outcome="success",
                    correlation_id=order.reference, details={"tracking_number": order.tracking_number})
    return RedirectResponse("/shop-orders", status_code=303)


_STYLE = """<style>
.pack-label-actions{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px}
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.order-card{margin-bottom:14px}
.order-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.order-grid{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start}
.order-items{list-style:none;padding:0;margin:10px 0 0;font-size:13.5px;color:var(--text-dim);display:flex;flex-direction:column;gap:10px}
.order-line{display:flex;gap:10px;align-items:flex-start}
.line-thumb{width:40px;height:56px;object-fit:cover;border-radius:4px;border:1px solid var(--panel-border);flex-shrink:0;background:var(--surface-sunken)}
.line-thumb.noart{display:flex}
.line-meta{margin:2px 0}
.order-total{text-align:right;min-width:160px}
.order-actions{display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.ship-form{display:flex;gap:8px}
.ship-form input{padding:6px 9px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px;width:150px}
.btn.small{padding:6px 12px;font-size:13px}
.btn.danger{border-color:#b84646;color:#b84646;background:transparent}
.refund-action{font-size:12px;text-align:left;max-width:250px}
.refund-action summary{cursor:pointer;color:var(--text-dim)}
.refund-action form{display:flex;flex-direction:column;gap:7px;margin-top:8px}
.refund-action label{font-size:12px;font-weight:400;text-transform:none;letter-spacing:0}
.confirm-action{font-size:12px;font-weight:400;text-transform:none;letter-spacing:0}
.refund-action input[type=text],.refund-action input:not([type]){padding:6px 9px;border:1px solid var(--panel-border);border-radius:6px}
.back-link{display:inline-block;margin-bottom:10px;font-size:13.5px;color:var(--text-dim)}
.order-ref-link{color:inherit;text-decoration:none}
.order-ref-link:hover{text-decoration:underline}
.order-detail{max-width:820px}
.detail-subhead{font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint)}
.order-notes{margin-top:16px;padding-top:16px;border-top:1px solid var(--panel-border);white-space:pre-wrap}
@media (max-width:640px){.order-grid{grid-template-columns:1fr}.order-total{text-align:left}.order-actions{align-items:flex-start}}
</style>"""
