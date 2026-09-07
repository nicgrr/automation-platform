"""The business dashboard -- rebuilt light-themed and chart-driven per an
explicit reference (a Salesforce executive dashboard): organized widget
cards instead of a stat grid, real donut/gauge/bar charts instead of plain
numbers. Deliberately its own self-contained HTML shell rather than
ui.py's shared dark `page()`/`STYLE` -- every other page in this app keeps
the dark "trading desk" look; only this one page's palette changed, by
explicit choice, not a global theme switch.
"""

from datetime import UTC, datetime
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .charts import CHART_STYLE, bar_list, donut_chart, donut_legend, gauge_chart
from .search import AUTOCOMPLETE_SCRIPT
from .database import get_session
from .inventory_review import _aud, _latest_prices
from .models import (
    AuditEvent, CapturedCard, CardCaptureStatus, InventoryItem, Marketplace,
    PendingListing, ListingBuildStatus, PotentialPurchase, PotentialPurchaseStatus,
    PricingStatus, Sale, TcgCard,
)

router = APIRouter(tags=["dashboard"])

OPEN_POTENTIAL_STATUSES = {
    PotentialPurchaseStatus.WATCHING, PotentialPurchaseStatus.CONTACT_SELLER,
    PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER,
}


def _review_queue_size(settings) -> int:
    """How many crops the scanner set aside. A plain directory count -- the
    queue is files on disk, not a table."""
    directory = Path(settings.scan_media_dir) / "needs_review"
    return len(list(directory.glob("*.jpg"))) if directory.exists() else 0


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    settings = request.app.state.settings

    # -- pipeline counts, unchanged from before --
    pending_review_count = len(session.scalars(select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW)).all())
    pending_price_count = len(session.scalars(select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW)).all())
    pending_listing_count = len(session.scalars(select(PendingListing).where(PendingListing.status == ListingBuildStatus.DRAFT)).all())
    review_count = _review_queue_size(settings)
    catalog_by_game = session.execute(select(TcgCard.game, func.count()).group_by(TcgCard.game)).all()

    # -- inventory value + pricing-coverage gauge --
    items = session.scalars(select(InventoryItem)).all()
    prices = _latest_prices(session, {i.card_id for i in items})
    market_value = 0.0
    priced_count = 0
    game_values: dict[str, float] = {}
    tcg_games = {row[0]: row[1] for row in session.execute(select(TcgCard.catalog_item_id, TcgCard.game)).all()}
    for i in items:
        price = prices.get((i.card_id, i.variant))
        value = _aud(price, i.quantity) or 0.0
        if price is not None:
            priced_count += 1
        market_value += value
        game = tcg_games.get(i.card_id, "unknown")
        game_values[game] = game_values.get(game, 0.0) + value
    priced_pct = (priced_count / len(items) * 100) if items else 0.0
    scanned_holdings, scanned_copies = len(items), sum(i.quantity for i in items)

    # -- inventory ageing --
    now = datetime.now(UTC)
    ageing = {"0-30d": 0, "31-60d": 0, "61-90d": 0, "90+d": 0}
    for i in items:
        added = i.added_at if i.added_at.tzinfo else i.added_at.replace(tzinfo=UTC)
        age = (now - added).days
        bucket = "0-30d" if age <= 30 else "31-60d" if age <= 60 else "61-90d" if age <= 90 else "90+d"
        ageing[bucket] += 1

    # -- sales by channel + recent sales --
    marketplaces = {m.id: m.name for m in session.scalars(select(Marketplace)).all()}
    all_sales = session.scalars(select(Sale).order_by(desc(Sale.sold_at))).all()
    channel_totals: dict[str, float] = {}
    for s in all_sales:
        name = marketplaces.get(s.marketplace_id, "Unrecorded")
        channel_totals[name] = channel_totals.get(name, 0.0) + float(s.gross_amount)
    recent_sales = all_sales[:6]

    # -- top potential buys, ranked by expected margin --
    open_potentials = [
        p for p in session.scalars(select(PotentialPurchase)).all()
        if p.status in OPEN_POTENTIAL_STATUSES and p.market_value
    ]
    open_potentials.sort(key=lambda p: float(p.market_value - (p.asking_price or p.market_value)), reverse=True)
    top_potentials = open_potentials[:5]

    events = session.scalars(select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(6)).all()

    # ---- widgets ----
    gauge_widget = (
        "<div class='widget'><div class='widget-head'><h3>Inventory Priced</h3><span class='widget-sub'>Coverage</span></div>"
        f"<div class='gauge-wrap'>{gauge_chart(priced_pct, color='#0891b2', label='priced')}"
        f"<div class='gauge-caption'>{priced_count:,} of {scanned_holdings:,} holdings have a known market price</div></div></div>"
    )

    sales_rows = "".join(
        f"<tr><td>{escape(marketplaces.get(s.marketplace_id, '—'))}</td>"
        f"<td>${s.gross_amount:,.2f}</td><td>{s.sold_at:%Y-%m-%d}</td></tr>"
        for s in recent_sales
    ) or "<tr><td colspan=3 class='chart-empty'>No sales recorded yet.</td></tr>"
    sales_widget = (
        "<div class='widget'><div class='widget-head'><h3>Recent Sales</h3><a href='/sales' class='widget-link'>View all</a></div>"
        f"<table class='widget-table'><thead><tr><th>Channel</th><th>Amount</th><th>Date</th></tr></thead><tbody>{sales_rows}</tbody></table></div>"
    )

    buy_rows = "".join(
        f"<tr><td>{escape(p.description)}</td><td>${p.market_value:,.2f}</td>"
        f"<td>{f'${p.asking_price:,.2f}' if p.asking_price else '—'}</td></tr>"
        for p in top_potentials
    ) or "<tr><td colspan=3 class='chart-empty'>No open potential buys.</td></tr>"
    buys_widget = (
        "<div class='widget'><div class='widget-head'><h3>Top Potential Buys</h3><a href='/potential-stock' class='widget-link'>View all</a></div>"
        f"<table class='widget-table'><thead><tr><th>Item</th><th>Market</th><th>Asking</th></tr></thead><tbody>{buy_rows}</tbody></table></div>"
    )

    market_value_widget = (
        "<div class='widget'><div class='widget-head'><h3>Inventory Value</h3><span class='widget-sub'>By game</span></div>"
        "<div class='value-donut-row'>"
        f"<div class='big-number'>${market_value:,.2f}<div class='big-number-sub'>{scanned_holdings:,} holdings &middot; {scanned_copies:,} copies</div></div>"
        + (
            f"{donut_chart(list(game_values.items()))}{donut_legend(list(game_values.items()))}"
            if game_values else "<p class='chart-empty'>No priced inventory yet.</p>"
        )
        + "</div></div>"
    )

    channel_items = sorted(channel_totals.items(), key=lambda kv: -kv[1])
    channel_widget = (
        "<div class='widget'><div class='widget-head'><h3>Sales by Channel</h3></div>"
        + (f"<div class='value-donut-row'>{donut_chart(channel_items)}{donut_legend(channel_items)}</div>" if channel_items else "<p class='chart-empty'>No sales recorded yet.</p>")
        + "</div>"
    )

    ageing_widget = (
        "<div class='widget'><div class='widget-head'><h3>Inventory Ageing</h3><a href='/analytics' class='widget-link'>Full analytics</a></div>"
        f"<div class='bar-list'>{bar_list(list(ageing.items()), color='#7c3aed', money=False)}</div></div>"
    )

    catalog_summary = ", ".join(f"{count:,} {game}" for game, count in catalog_by_game) or "none yet"
    events_html = "".join(f"<li>{escape(str(e.occurred_at))} — {escape(e.action)} — {escape(e.outcome)}</li>" for e in events) or "<li>No events</li>"
    ops_widget = (
        "<div class='widget'><div class='widget-head'><h3>Operations</h3></div>"
        "<div class='ops-grid'>"
        f"<div><div class='ops-label'>Bulk scan review</div><div class='ops-value'>{review_count}</div></div>"
        f"<div><div class='ops-label'>Capture review</div><div class='ops-value'>{pending_review_count}</div></div>"
        f"<div><div class='ops-label'>Price approval</div><div class='ops-value'>{pending_price_count}</div></div>"
        f"<div><div class='ops-label'>Listings drafted</div><div class='ops-value'>{pending_listing_count}</div></div>"
        "</div>"
        f"<p class='ops-catalog'>Catalogue: {escape(catalog_summary)}</p>"
        f"<ul class='ops-events'>{events_html}</ul>"
        "</div>"
    )

    grid = "".join([gauge_widget, sales_widget, buys_widget, market_value_widget, channel_widget, ageing_widget, ops_widget])

    body = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EzBay — Dashboard</title>
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#0891b2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="EzBay">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<style>{_STYLE}{CHART_STYLE}</style>
</head><body>
<header class="topbar">
  <div class="brand"><span class="brand-mark">Ez</span>Bay</div>
  <form class="topsearch" method="get" action="/search">
    <div class="autocomplete-wrap">
      <input id="global-search-input" name="q" placeholder="Search cards, sets, characters…" autocomplete="off">
      <div id="global-search-results" class="autocomplete-dropdown" hidden></div>
    </div>
  </form>
  <nav class="topnav">
    <a href="/analytics">Analytics</a>
    <a href="/inventory">Inventory</a>
    <a href="/feed">Scan feed</a>
    <span class="status-pill">Platform OK</span>
  </nav>
</header>
<main class="dash-main">
  <div class="dash-title">
    <h1>Sales Executive Dashboard</h1>
    <p>A live view of inventory, sales, and purchasing &middot; as of {now:%Y-%m-%d %H:%M} UTC</p>
  </div>
  <div class="widget-grid">{grid}</div>

  <div class="quicklinks">
    <div class="quicklink-group">
      <h4>Card capture &amp; listings</h4>
      <a href="/cards/capture">Capture new card</a>
      <a href="/cards/capture/bulk">Bulk upload</a>
      <a href="/cards/review">Review queue ({pending_review_count})</a>
      <a href="/cards/pricing">Price review ({pending_price_count})</a>
      <a href="/listings/review">Listing review ({pending_listing_count})</a>
    </div>
    <div class="quicklink-group">
      <h4>Bulk scan &amp; catalogue</h4>
      <a href="/inventory">Inventory ({scanned_holdings} distinct)</a>
      <a href="/feed">Scan feed</a>
      <a href="/scan-ingest">Status &amp; logs</a>
      <a href="/review">Review queue ({review_count})</a>
      <a href="/foil-review">Foil review</a>
      <a href="/search">Search catalogue</a>
    </div>
    <div class="quicklink-group">
      <h4>Buying &amp; selling</h4>
      <a href="/buying-calculator">Buying calculator</a>
      <a href="/purchase-lots">Purchase lots</a>
      <a href="/potential-stock">Potential stock</a>
      <a href="/sales">Sales</a>
      <a href="/whatnot">Whatnot shows</a>
      <a href="/marketplaces">Marketplaces</a>
    </div>
    <div class="quicklink-group">
      <h4>Business</h4>
      <a href="/customers">Customers</a>
      <a href="/suppliers">Suppliers</a>
      <a href="/goals">Goals</a>
      <a href="/release-calendar">Release calendar</a>
      <a href="/sealed-products">Sealed products</a>
      <a href="/collectibles">Collectibles</a>
      <a href="/export">Export data</a>
    </div>
  </div>
</main>
{AUTOCOMPLETE_SCRIPT}
</body></html>"""
    return HTMLResponse(body)


_STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#f4f5f8;color:#12141c;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:#0891b2;text-decoration:none}
a:hover{text-decoration:underline}

.topbar{display:flex;align-items:center;gap:20px;background:#fff;border-bottom:1px solid #e5e7ee;
  padding:12px 24px;position:sticky;top:0;z-index:10}
.brand{font-size:19px;font-weight:800;letter-spacing:-.02em}
.brand-mark{background:linear-gradient(120deg,#0891b2,#7c3aed);-webkit-background-clip:text;background-clip:text;color:transparent}
.topsearch{flex:1;max-width:420px}
.autocomplete-wrap{position:relative;width:100%}
.autocomplete-wrap input{width:100%;padding:9px 14px;border-radius:999px;border:1px solid #e5e7ee;background:#f4f5f8;font-size:13.5px}
.autocomplete-wrap input:focus{outline:none;border-color:#0891b2;background:#fff}

.autocomplete-dropdown{position:absolute;top:calc(100% + 8px);left:0;right:0;z-index:30;
  background:#fff;border:1px solid #e5e7ee;border-radius:12px;box-shadow:0 12px 28px rgba(16,24,40,.12);overflow:hidden}
.ac-item{display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;font-size:13.5px}
.ac-item.active,.ac-item:hover{background:#f4f5f8}
.ac-name{font-weight:650;flex:0 1 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#12141c}
.ac-detail{color:#6b7280;font-size:12px;flex:1}
.ac-owned{font-size:11px;color:#059669;background:#ecfdf5;padding:2px 8px;border-radius:999px}
.topnav{display:flex;align-items:center;gap:18px;font-size:13.5px;color:#4b5563;margin-left:auto}
.status-pill{background:#ecfdf5;color:#059669;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:650}

.dash-main{max-width:1240px;margin:0 auto;padding:28px 24px 60px}
.dash-title h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}
.dash-title p{margin:0 0 24px;color:#6b7280;font-size:13.5px}

.widget-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
@media (max-width:980px){.widget-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:640px){.widget-grid{grid-template-columns:1fr}}

.widget{background:#fff;border:1px solid #e5e7ee;border-radius:14px;padding:18px 20px;
  box-shadow:0 1px 2px rgba(16,24,40,.04)}
.widget-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:14px}
.widget-head h3{margin:0;font-size:14px;font-weight:700}
.widget-sub{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.06em}
.widget-link{font-size:12px;font-weight:600}

.widget-table{width:100%;border-collapse:collapse;font-size:13px}
.widget-table th{text-align:left;color:#9ca3af;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
  font-weight:600;padding:0 0 8px;border-bottom:1px solid #eef0f5}
.widget-table td{padding:8px 0;border-bottom:1px solid #f4f5f8}
.widget-table tr:last-child td{border-bottom:none}

.gauge-wrap{display:flex;flex-direction:column;align-items:center;gap:8px}
.gauge-caption{font-size:12px;color:#6b7280;text-align:center}

.value-donut-row{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.big-number{font-size:26px;font-weight:800;letter-spacing:-.02em}
.big-number-sub{font-size:12px;font-weight:500;color:#6b7280;margin-top:4px}

.ops-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:14px}
.ops-label{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em}
.ops-value{font-size:20px;font-weight:700}
.ops-catalog{font-size:12px;color:#6b7280;margin:0 0 10px}
.ops-events{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px;
  font-size:11.5px;color:#6b7280;max-height:110px;overflow-y:auto}

.quicklinks{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-top:26px;
  padding-top:22px;border-top:1px solid #e5e7ee}
@media (max-width:780px){.quicklinks{grid-template-columns:repeat(2,1fr)}}
.quicklink-group h4{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#9ca3af;margin:0 0 10px}
.quicklink-group{display:flex;flex-direction:column;gap:7px}
.quicklink-group a{font-size:13px;color:#374151}
.quicklink-group a:hover{color:#0891b2}
"""
