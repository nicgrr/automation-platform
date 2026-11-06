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
    EbayListing, EbayListingTraffic, InventoryItem, InventoryStatus, MarketListing, Marketplace,
    PotentialPurchaseStatus, PurchaseLot, PurchaseLotStatus, Sale, SaleStatus,
)
from .ui import brand_header, page

router = APIRouter(tags=["analytics"])

DEAD_STOCK_DAYS = 90
# "Viewed but not selling" only means something once a listing has had a
# real look; a couple of views is noise.
VIEWED_NOT_SELLING_MIN_VIEWS = 10
EBAY_PANEL_ROWS = 10
OPEN_POTENTIAL_STATUSES = {
    PotentialPurchaseStatus.WATCHING, PotentialPurchaseStatus.CONTACT_SELLER,
    PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER,
}
TIED_UP_STATUSES = {InventoryStatus.AVAILABLE, InventoryStatus.LISTED, InventoryStatus.RESERVED}


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    now = datetime.now(UTC)
    items = session.scalars(select(InventoryItem)).all()
    prices = _latest_prices(session, {i.catalog_item_id for i in items})

    market_value = 0.0
    cost_basis_total = 0.0
    cash_tied_up = 0.0
    ageing = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    dead_stock = []
    for i in items:
        price = prices.get((i.catalog_item_id, i.variant))
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
    all_sales = session.scalars(select(Sale).where(Sale.status == SaleStatus.COMPLETED)).all()

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

    purchase_candidates = session.scalars(select(MarketListing).where(
        MarketListing.candidate_status.is_not(None)
    )).all()
    candidate_landed_cost = sum(
        float(candidate.landed_aud or 0)
        for candidate in purchase_candidates
        if candidate.candidate_status in OPEN_POTENTIAL_STATUSES
    )
    open_offers = session.scalar(select(func.count()).select_from(PurchaseLot).where(PurchaseLot.status == PurchaseLotStatus.OFFER_SENT)) or 0
    open_offers += sum(
        1 for candidate in purchase_candidates
        if candidate.candidate_status in (PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER)
    )

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
        + stat("Candidate landed cost", f"${candidate_landed_cost:,.2f}")
        + stat("Open offers", str(open_offers))
        + "</div>"
    )

    ageing_html = "".join(
        f"<div class='age-bucket{' dead' if bucket == '90+' and count else ''}'><div class='age-count'>{count}</div><div class='age-label'>{bucket} days</div></div>"
        for bucket, count in ageing.items()
    )
    def item_label(item: InventoryItem) -> str:
        name = item.catalog_item.name if item.catalog_item else item.catalog_item_id
        return escape(f"{name} ({item.variant.value}, x{item.quantity})")

    dead_stock_html = (
        "".join(f"<li>{item_label(i)}</li>" for i in dead_stock[:15])
        if dead_stock else "<li class='muted'>None flagged.</li>"
    )

    body = (
        brand_header("Business analytics")
        + top_cards
        + "<div class='panel'><h2>Inventory ageing</h2><div class='age-row'>" + ageing_html + "</div></div>"
        + f"<div class='panel'><h2>Dead stock (90+ days, still available)</h2><ul class='events'>{dead_stock_html}</ul></div>"
        + _ebay_performance_html(session)
        + (f"<div class='panel'><h2>Best sales channel</h2><p>{escape(best_channel)} — "
           f"${channel_totals[best_channel]:,.2f}</p></div>" if best_channel else "")
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Analytics", body))


def _rate(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "—"


def _ebay_performance_html(session: Session) -> str:
    """Panel for the latest eBay traffic window (see ebay_traffic.py)."""
    rows = session.execute(
        select(EbayListingTraffic, EbayListing.title)
        .join(EbayListing, EbayListing.ebay_listing_id == EbayListingTraffic.ebay_listing_id)
        .where(EbayListing.listing_status == "Active")
    ).all()
    if not rows:
        return ("<div class='panel'><h2>eBay performance</h2><p class='muted'>No eBay traffic yet. "
                "Reconnect eBay at <a href='/auth/ebay/start'>/auth/ebay/start</a> to allow analytics; "
                "listing views and impressions then refresh daily.</p></div>")

    traffic = [row[0] for row in rows]
    titles = {row[0].ebay_listing_id: row[1] for row in rows}
    impressions = sum(t.impressions for t in traffic)
    views = sum(t.views for t in traffic)
    sales = sum(t.transactions for t in traffic)
    first = traffic[0]

    def listing_list(items, detail) -> str:
        if not items:
            return "<li class='muted'>None.</li>"
        return "".join(
            f"<li><a href='https://www.ebay.com.au/itm/{escape(t.ebay_listing_id)}' target='_blank' rel='noopener'>"
            f"{escape(titles[t.ebay_listing_id])}</a> <span class='muted'>— {detail(t)}</span></li>"
            for t in items[:EBAY_PANEL_ROWS]
        )

    not_selling = sorted((t for t in traffic if t.transactions == 0 and t.views >= VIEWED_NOT_SELLING_MIN_VIEWS),
                         key=lambda t: -t.views)
    unseen = sorted(traffic, key=lambda t: (t.impressions, t.views))
    best = sorted((t for t in traffic if t.transactions), key=lambda t: (-t.transactions, -t.views))
    stats = "".join(
        f"<div class='stat-card'><div class='label'>{label}</div><div class='value'>{value}</div></div>"
        for label, value in (
            ("Impressions", f"{impressions:,}"),
            ("Listing views", f"{views:,}"),
            ("Views per impression", _rate(views, impressions)),
            ("Items sold", f"{sales:,}"),
            ("Sales per view", _rate(sales, views)),
        )
    )
    return (
        "<div class='panel'><h2>eBay performance</h2>"
        f"<p class='muted'>{first.period_start:%d %b} – {first.period_end:%d %b %Y} · {len(traffic)} active listings · "
        f"updated {first.fetched_at:%d %b}</p>"
        f"<div class='stat-grid'>{stats}</div>"
        f"<h3>Viewed but not selling</h3><p class='muted'>{VIEWED_NOT_SELLING_MIN_VIEWS}+ views, no sales — check price and photos.</p>"
        f"<ul class='events'>{listing_list(not_selling, lambda t: f'{t.views} views, {t.impressions:,} impressions')}</ul>"
        "<h3>Rarely seen</h3><p class='muted'>Fewest impressions — check title, category and item specifics.</p>"
        f"<ul class='events'>{listing_list(unseen, lambda t: f'{t.impressions:,} impressions, {t.views} views')}</ul>"
        "<h3>Best sellers</h3>"
        f"<ul class='events'>{listing_list(best, lambda t: f'{t.transactions} sold from {t.views} views')}</ul>"
        "</div>"
    )


_STYLE = """<style>
.age-row{display:flex;gap:14px}
.age-bucket{flex:1;text-align:center;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:10px;padding:14px}
.age-bucket.dead{border-color:var(--danger)}
.age-count{font-size:22px;font-weight:700}
.age-label{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
.muted{color:var(--text-dim)}
</style>"""
