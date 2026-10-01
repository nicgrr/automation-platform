"""eBay sales -- where the cards that already sold on eBay actually are.

Nothing in the admin app read EbayImportedOrder at all until now: it's
written by scripts/import_ebay_orders.py (Phase 5 of the two-way
storefront <-> eBay stock sync) and never displayed anywhere -- the only
way to see what had actually sold on eBay was a direct database query.
This is the fix: for every real eBay order line that's been applied
against a ShopListing, show a photo, the set/number/rarity, and wherever
it's currently marked as living, the same pack-assistant shape
shop_orders.py already has for website checkout sales (see
card_locate.py, shared by both).

Still no packed/shipped state machine here -- eBay tracks its own
fulfillment state on eBay's side (lineItemFulfillmentStatus in
raw_snapshot), and this app doesn't write tracking back to eBay. What
this page *does* now track is purely local: dismissed_at, the operator's
own "I'm done looking at this" marker (2026-09-28, real complaint: every
order stayed expanded forever with no way to collapse or clear one out
of the active view). It never touches order or listing status, and it's
a completely separate field from applied_at -- the sync poller's own
idempotency marker -- so dismissing an order here can never affect
whether its stock got decremented.
"""

import uuid
from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_locate import LOCATE_STYLE, PACK_STYLE, pack_card_html
from .database import get_session
from .models import EbayImportedOrder, ShopListing
from .ui import brand_header, page

router = APIRouter(tags=["ebay-sales"])

FILTERS = {
    "action_needed": "Needs action",
    "all": "All orders",
    "completed": "Completed",
}


def _ship_to(snapshot: dict) -> dict:
    """Buyer name/address/email out of eBay's own order JSON -- every
    field guarded, since this is a live third-party payload shape, not
    something this app controls."""
    steps = snapshot.get("fulfillmentStartInstructions") or []
    ship_to = (steps[0].get("shippingStep") or {}).get("shipTo") if steps else {}
    ship_to = ship_to or {}
    address = ship_to.get("contactAddress") or {}
    buyer = snapshot.get("buyer") or {}
    street = ", ".join(v for v in (address.get("addressLine1"), address.get("addressLine2")) if v)
    locality = ", ".join(
        v for v in (address.get("city"), address.get("stateOrProvince"), address.get("postalCode")) if v
    )
    return {
        "name": ship_to.get("fullName") or buyer.get("username") or "Unknown buyer",
        "street": street,
        "locality": locality,
        "country": address.get("countryCode") or "",
    }


def _line_items_by_sku(snapshot: dict) -> dict[str, dict]:
    return {li["sku"]: li for li in snapshot.get("lineItems", []) if li.get("sku")}


def _price_html(line_item: dict | None) -> str:
    if not line_item:
        return "—"
    cost = line_item.get("lineItemCost") or {}
    value, currency = cost.get("value"), cost.get("currency", "")
    return f"${value} {currency}" if value else "—"


def _order_total_html(snapshot: dict) -> str:
    total = (snapshot.get("pricingSummary") or {}).get("total") or {}
    value, currency = total.get("value"), total.get("currency", "")
    return f"${value} {currency}" if value else "—"


def _order_card_html(session: Session, ebay_order_id: str, rows: list[EbayImportedOrder]) -> str:
    snapshot = rows[0].raw_snapshot or {}
    ship_to = _ship_to(snapshot)
    line_items = _line_items_by_sku(snapshot)
    completed = all(row.dismissed_at is not None for row in rows)

    cards = "".join(
        pack_card_html(
            session,
            session.get(ShopListing, row.shop_listing_id).inventory_item_id if row.shop_listing_id else None,
            title=(line_items.get(row.sku) or {}).get("title", row.sku),
            qty=row.quantity,
            price_html=_price_html(line_items.get(row.sku)),
            back_url="/ebay-sales",
            empty_note="Couldn't match this sku to a specific held card -- check "
                       "/ebay-listings for what this SKU maps to.",
        )
        for row in rows
    )

    actions = (
        "<div class='muted'>Completed</div>" if completed else
        f"<form method='post' action='/ebay-sales/{escape(ebay_order_id)}/complete'>"
        "<button class='btn small'>Mark complete</button></form>"
        f"<form method='post' action='/ebay-sales/{escape(ebay_order_id)}/discard' "
        "onsubmit=\"return confirm('Discard this order from the active view?');\">"
        "<button class='btn small' style='background:none;color:var(--text-dim);"
        "border:1px solid var(--panel-border);box-shadow:none'>Discard</button></form>"
    )

    # Collapsed by default once completed -- an order still needing
    # action opens straight up, since that's the one a person actually
    # came here to work on.
    return (
        f"<details class='panel order-block'{'' if completed else ' open'}>"
        "<summary class='order-head'>"
        f"<div><strong>Order {escape(ebay_order_id)}</strong>"
        f"<span class='muted'> · {rows[0].observed_at:%Y-%m-%d %H:%M}</span></div>"
        f"<div class='detail-price'>{_order_total_html(snapshot)}</div>"
        "</summary>"
        f"<div class='muted ship-to'>{escape(ship_to['name'])} &mdash; {escape(ship_to['street'])}, "
        f"{escape(ship_to['locality'])}, {escape(ship_to['country'])}</div>"
        f"<div class='pack-grid'>{cards}</div>"
        f"<div class='order-block-actions'>{actions}</div>"
        "</details>"
    )


@router.get("/ebay-sales", response_class=HTMLResponse)
def ebay_sales_page(
    status: str = "action_needed",
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    status = status if status in FILTERS else "action_needed"
    rows = session.scalars(
        select(EbayImportedOrder)
        .where(EbayImportedOrder.applied_at.isnot(None))
        .order_by(EbayImportedOrder.observed_at.desc())
    ).all()

    by_order: dict[str, list[EbayImportedOrder]] = {}
    for row in rows:
        by_order.setdefault(row.ebay_order_id, []).append(row)

    def _completed(group: list[EbayImportedOrder]) -> bool:
        return all(r.dismissed_at is not None for r in group)

    if status == "action_needed":
        shown = {oid: g for oid, g in by_order.items() if not _completed(g)}
    elif status == "completed":
        shown = {oid: g for oid, g in by_order.items() if _completed(g)}
    else:
        shown = by_order

    orders_html = "".join(
        _order_card_html(session, ebay_order_id, group)
        for ebay_order_id, group in sorted(shown.items(), key=lambda kv: kv[1][0].observed_at, reverse=True)
    ) or "<div class='panel'><p class='muted'>Nothing here.</p></div>"

    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' href='/ebay-sales?status={key}'>{escape(label)}</a>"
        for key, label in FILTERS.items()
    )

    body = (
        brand_header("eBay sales")
        + f"<p class='subtitle'>{len(shown)} order(s), {sum(len(g) for g in shown.values())} card(s).</p>"
        + f"<div class='filter-tabs'>{filter_bar}</div>"
        + orders_html
        + _STYLE + PACK_STYLE + LOCATE_STYLE
    )
    return HTMLResponse(page("EzBay — eBay sales", body))


@router.post("/ebay-sales/{ebay_order_id}/complete")
def complete_order(
    ebay_order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    return _dismiss(session, ebay_order_id, user, action="ebay_sale.complete")


@router.post("/ebay-sales/{ebay_order_id}/discard")
def discard_order(
    ebay_order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    return _dismiss(session, ebay_order_id, user, action="ebay_sale.discard")


def _dismiss(session: Session, ebay_order_id: str, user: str, *, action: str) -> RedirectResponse:
    rows = session.scalars(
        select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id)
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.dismissed_at = now
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action=action,
                resource_type="ebay_imported_order", resource_id=ebay_order_id, outcome="success",
                correlation_id=str(uuid.uuid4()), details={"line_count": len(rows)})
    return RedirectResponse("/ebay-sales", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.order-block{margin-bottom:14px}
.order-block[open]{padding-bottom:16px}
.order-head{display:flex;justify-content:space-between;align-items:center;cursor:pointer;list-style:none}
.order-head::-webkit-details-marker{display:none}
.order-head::before{content:'▸';margin-right:8px;color:var(--text-dim);font-size:12px}
details[open]>.order-head::before{content:'▾'}
.ship-to{margin:10px 0;font-size:13.5px}
.order-block-actions{display:flex;gap:8px;margin-top:12px}
.btn.small{padding:6px 12px;font-size:13px}
@media (max-width: 680px) {
  .order-head{flex-wrap:wrap;gap:6px 12px}
  .order-block-actions{flex-wrap:wrap}
}
</style>"""
