"""The business command centre -- Modules 13 (inventory analytics) and 16
(business dashboard). Answers, in one page, the questions the whole
platform exists to answer: what's it worth, what's tied up in it, what
sold, what's ageing.

Market value reuses inventory_review's own latest-price/AUD-conversion
logic (see that module for why: pokemontcg's snapshot vs.
pokemonpricetracker's live data, AUD conversion at display time) rather
than duplicating it -- this project already shares underscore-prefixed
helpers across modules (scan_feed.py reuses scan_ingest_status's parsing
the same way).

Cost-basis figures are honest about being incomplete: `allocated_cost_basis`
is a new field on InventoryItem with no historical backfill, so "total
inventory cost" only reflects what's been recorded since. Shown as a
partial figure with a note, not silently treated as zero-means-free.
"""

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .inventory_review import _aud, _latest_prices
from .models import (
    Goal, InventoryItem, InventoryStatus, Marketplace, PotentialPurchase,
    PotentialPurchaseStatus, PurchaseLot, PurchaseLotStatus, Sale,
)
from .ui import brand_header, page, pill

router = APIRouter(tags=["analytics"])

DEAD_STOCK_DAYS = 90
OPEN_POTENTIAL_STATUSES = {
    PotentialPurchaseStatus.WATCHING, PotentialPurchaseStatus.CONTACT_SELLER,
    PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER,
}
TIED_UP_STATUSES = {InventoryStatus.AVAILABLE, InventoryStatus.LISTED, InventoryStatus.RESERVED}


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    now = datetime.now(UTC)
    items = session.scalars(select(InventoryItem)).all()
    prices = _latest_prices(session, {i.card_id for i in items})

    market_value = 0.0
    cost_basis_total = 0.0
    cash_tied_up = 0.0
    ageing = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    dead_stock = []
    for i in items:
        price = prices.get((i.card_id, i.variant))
        value = _aud(price, i.quantity) or 0.0
        market_value += value
        basis = float(i.allocated_cost_basis or 0) * i.quantity
        cost_basis_total += basis
        if i.status in TIED_UP_STATUSES:
            cash_tied_up += basis

        added = i.added_at if i.added_at.tzinfo else i.added_at.replace(tzinfo=UTC)
        age_days = (now - added).days
        bucket = "0-30" if age_days <= 30 else "31-60" if age_days <= 60 else "61-90" if age_days <= 90 else "90+"
        ageing[bucket] += 1
        if age_days > DEAD_STOCK_DAYS and i.status == InventoryStatus.AVAILABLE:
            dead_stock.append(i)

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    all_sales = session.scalars(select(Sale)).all()

    def net_profit(s: Sale) -> float:
        return float(s.gross_amount + s.shipping_revenue - s.fees_amount - s.shipping_cost - s.packaging_cost - s.tax_amount)

    def naive(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    today_sales = sum(float(s.gross_amount) for s in all_sales if naive(s.sold_at) >= today_start)
    month_sales = [s for s in all_sales if naive(s.sold_at) >= month_start]
    monthly_revenue = sum(float(s.gross_amount) for s in month_sales)
    monthly_profit = sum(net_profit(s) for s in month_sales)
    realized_profit = sum(net_profit(s) for s in all_sales)

    channel_totals: dict[str, float] = {}
    marketplaces = {m.id: m.name for m in session.scalars(select(Marketplace)).all()}
    for s in all_sales:
        name = marketplaces.get(s.marketplace_id, "Unrecorded")
        channel_totals[name] = channel_totals.get(name, 0.0) + float(s.gross_amount)
    best_channel = max(channel_totals, key=channel_totals.get) if channel_totals else None

    potential_value = sum(
        float(p.market_value or 0) for p in session.scalars(select(PotentialPurchase)).all()
        if p.status in OPEN_POTENTIAL_STATUSES
    )
    open_offers = session.scalar(select(func.count()).select_from(PurchaseLot).where(PurchaseLot.status == PurchaseLotStatus.OFFER_SENT)) or 0
    open_offers += sum(
        1 for p in session.scalars(select(PotentialPurchase)).all()
        if p.status in (PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER)
    )

    goals = session.scalars(select(Goal).where(Goal.achieved_at.is_(None)).order_by(Goal.created_at)).all()

    def stat(label: str, value: str, sub: str = "") -> str:
        sub_html = f" <span class='value-sub'>{sub}</span>" if sub else ""
        return f"<div class='stat-card'><div class='label'>{label}</div><div class='value'>{value}{sub_html}</div></div>"

    top_cards = (
        "<div class='stat-grid'>"
        + stat("Inventory market value", f"${market_value:,.2f}")
        + stat("Inventory cost basis", f"${cost_basis_total:,.2f}", "partial -- new field")
        + stat("Cash tied up", f"${cash_tied_up:,.2f}")
        + stat("Today's sales", f"${today_sales:,.2f}")
        + stat("Monthly revenue", f"${monthly_revenue:,.2f}")
        + stat("Monthly profit", f"${monthly_profit:,.2f}")
        + stat("Realized profit (all-time)", f"${realized_profit:,.2f}")
        + stat("Potential stock value", f"${potential_value:,.2f}")
        + stat("Open offers", str(open_offers))
        + "</div>"
    )

    ageing_html = "".join(
        f"<div class='age-bucket{' dead' if bucket == '90+' and count else ''}'><div class='age-count'>{count}</div><div class='age-label'>{bucket} days</div></div>"
        for bucket, count in ageing.items()
    )
    def item_label(item: InventoryItem) -> str:
        name = item.card.name if item.card else item.card_id
        return escape(f"{name} ({item.variant.value}, x{item.quantity})")

    dead_stock_html = (
        "".join(f"<li>{item_label(i)}</li>" for i in dead_stock[:15])
        if dead_stock else "<li class='muted'>None flagged.</li>"
    )

    goals_html = "".join(
        f"<li>{g.label} — {pill(f'{min(100, float(g.current_value / g.target_value * 100)) if g.target_value else 0:.0f}%', 'neutral')}</li>"
        for g in goals[:6]
    ) or "<li class='muted'>No open goals.</li>"

    body = (
        brand_header("Business analytics")
        + "<p class='subtitle'>The command centre -- Modules 13 &amp; 16.</p>"
        + top_cards
        + "<div class='panel'><h2>Inventory ageing</h2><div class='age-row'>" + ageing_html + "</div></div>"
        + f"<div class='panel'><h2>Dead stock (90+ days, still available)</h2><ul class='events'>{dead_stock_html}</ul></div>"
        + f"<div class='panel'><h2>Best sales channel</h2><p>{escape(best_channel) + ' — $' + format(channel_totals[best_channel], ',.2f') if best_channel else 'No sales recorded yet.'}</p></div>"
        + f"<div class='panel'><h2>Open goals</h2><ul class='events'>{goals_html}</ul></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Analytics", body))


_STYLE = """<style>
.age-row{display:flex;gap:14px}
.age-bucket{flex:1;text-align:center;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:10px;padding:14px}
.age-bucket.dead{border-color:var(--danger)}
.age-count{font-size:22px;font-weight:700}
.age-label{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
.muted{color:var(--text-dim)}
</style>"""
