"""The business dashboard -- rebuilt light-themed and chart-driven per an
explicit reference (a Salesforce executive dashboard): organized widget
cards instead of a stat grid, real donut/gauge/bar charts instead of plain
numbers. Originally its own fully self-contained HTML shell, back when it
was the one light-themed exception to the rest of the dark app; now that
the whole app shares one light theme (2026-09-07), it uses ui.py's shared
`page()` shell like every other route, keeping only its own widget-grid
markup and styling as page-specific `_STYLE`.
"""

from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .charts import CHART_STYLE, bar_list, donut_chart, donut_legend, gauge_chart
from .database import get_session
from .inventory_review import _aud, _latest_ebay_prices, _latest_prices
from .models import (
    AuditEvent, CollectibleProduct, InventoryItem, InventoryStatus, Marketplace,
    MarketListing, PotentialPurchaseStatus, RetailerProgram, Sale, TcgCard,
)
from .nav_counts import queue_counts
from .ui import ADMIN_NAV, page

router = APIRouter(tags=["dashboard"])

OPEN_POTENTIAL_STATUSES = {
    PotentialPurchaseStatus.WATCHING, PotentialPurchaseStatus.CONTACT_SELLER,
    PotentialPurchaseStatus.OFFER_SENT, PotentialPurchaseStatus.COUNTER_OFFER,
}


def _jobs_html() -> str:
    """The background jobs, above everything else on the page.

    Placed above the widget grid rather than in it, because the failure
    this exists for looked exactly like healthy numbers: the price backfill
    died on 8 September and every figure on this page carried on rendering
    a value, just an increasingly stale one. A widget among widgets would
    have been read the same way.

    Healthy collapses to a single line. Only trouble gets room.
    """
    from .job_health import all_statuses

    statuses = all_statuses()
    hurt = [s for s in statuses if s.needs_attention]
    if not hurt:
        when = max((s.last_run for s in statuses if s.last_run), default=None)
        ran = f" &middot; last ran {when:%a %H:%M}" if when else ""
        return (f"<div class='jobs jobs-ok'><span class='jobs-dot'></span>"
                f"All {len(statuses)} background jobs healthy{ran}</div>")

    rows = "".join(
        f"<li><strong>{escape(s.job.label)}</strong> &mdash; {escape(s.detail)}"
        f"<span class='jobs-what'>{escape(s.job.what)}</span>"
        + (f"<span class='jobs-next'>next run {s.next_run:%a %H:%M}</span>" if s.next_run else "")
        + "</li>"
        for s in hurt
    )
    phrase = "job needs" if len(hurt) == 1 else "jobs need"
    return (f"<div class='jobs jobs-bad'><p class='jobs-head'>{len(hurt)} background {phrase} "
            f"attention</p><ul>{rows}</ul>"
            "<p class='jobs-foot'>Logs are in <code>scan_ingest_data/logs/</code>.</p></div>")


def _recovery_html() -> str:
    """Show whether usable recovery evidence is recent, not merely scheduled."""
    from .recovery_health import recovery_statuses

    statuses = recovery_statuses()
    hurt = [status for status in statuses if status.needs_attention]
    if not hurt:
        detail = " &middot; ".join(
            f"{escape(status.label.lower())} {escape(status.detail)}" for status in statuses
        )
        return (
            "<div class='jobs jobs-ok'><span class='jobs-dot'></span>"
            f"Recovery checks healthy &middot; {detail}</div>"
        )
    rows = "".join(
        f"<li><strong>{escape(status.label)}</strong> &mdash; {escape(status.detail)}</li>"
        for status in hurt
    )
    phrase = "check needs" if len(hurt) == 1 else "checks need"
    return (
        f"<div class='jobs jobs-bad'><p class='jobs-head'>{len(hurt)} recovery {phrase} "
        f"attention</p><ul>{rows}</ul>"
        "<p class='jobs-foot'>Backup and restore evidence must both be recent.</p></div>"
    )


def _storefront_readiness_html(settings) -> str:
    from .storefront_readiness import storefront_readiness_issues

    issues = storefront_readiness_issues(settings)
    if not issues:
        return ""
    rows = "".join(f"<li>{escape(issue)}</li>" for issue in issues)
    return (
        "<div class='jobs jobs-bad'><p class='jobs-head'>NL Vault is private but not launch-ready</p>"
        f"<ul>{rows}</ul><p class='jobs-foot'><a href='/settings'>Open Settings</a>. "
        "Checkout and public launch remain blocked until every item is resolved.</p></div>"
    )


def _quicklinks_html(counts: dict[str, int | None], storefront_url: str) -> str:
    """The same ten workflow entrances shown in the shared drawer."""
    columns = []
    for title, links, collapsed in ADMIN_NAV:
        if collapsed:
            continue
        rows = []
        for href, label in links:
            n = counts.get(href)
            suffix = f" <span class='ql-count'>{n}</span>" if n else ""
            rows.append(f"<a href='{href}'>{escape(label)}{suffix}</a>")
        columns.append(
            f"<div class='quicklink-group'><h4>{escape(title)}</h4>{''.join(rows)}</div>"
        )
    columns.append(
        "<div class='quicklink-group'><h4>Storefront</h4>"
        f"<a href='{storefront_url}' target='_blank' rel='noopener'>NL Vault shop &#8599;</a></div>"
    )
    return f"<div class='quicklinks'>{''.join(columns)}</div>"


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    settings = request.app.state.settings
    # the storefront is a separate application on its own port; derive
    # its URL from the host in use so this link works over Tailscale and
    # locally without another setting to keep in sync
    storefront_url = f"{request.url.scheme}://{request.url.hostname}:8444/"

    # -- pipeline counts --
    # From nav_counts.queue_counts so the Operations widget, the
    # quicklinks below and the nav drawer's badges can't disagree about
    # how much work is waiting; it also covers foil and photo audit,
    # which this handler never counted at all.
    counts = queue_counts(session, settings)
    pending_review_count = counts["/cards/review"]
    pending_price_count = counts["/cards/pricing"]
    pending_listing_count = counts["/shop-listings"]
    review_count = counts["/review"]
    catalog_by_game = session.execute(select(TcgCard.game, func.count()).group_by(TcgCard.game)).all()

    # -- inventory value + pricing-coverage gauge --
    # Personal Collection is deliberately excluded: those items are held
    # privately, off the shop, until explicitly promoted (see
    # personal_collection.py) -- a sales-facing coverage metric measuring
    # what's actually for sale shouldn't be diluted by what isn't.
    items = session.scalars(select(InventoryItem).where(InventoryItem.status != InventoryStatus.PERSONAL_COLLECTION)).all()
    # CardPrice.catalog_item_id lives in the generic catalogue id-space
    # (see CardPrice's own docstring and every recent importer --
    # One Piece, Personal Collection, collectibles all price by
    # catalog_item_id), not InventoryItem.card_id, which is a Pokemon-only
    # legacy field left null for anything imported through the newer
    # generic catalog. Keying this lookup off card_id silently missed
    # every non-Pokemon-legacy holding's real, already-fetched price --
    # confirmed: this test showed 100% coverage by catalog_item_id for
    # the same holdings this dashboard reported at 64%.
    prices = _latest_prices(session, {i.catalog_item_id for i in items})
    ebay_prices = _latest_ebay_prices(session, {i.catalog_item_id for i in items})
    market_value = 0.0
    priced_count = 0
    game_values: dict[str, float] = {}
    tcg_games = {row[0]: row[1] for row in session.execute(select(TcgCard.catalog_item_id, TcgCard.game)).all()}
    # Non-TCG holdings (Sonny Angel and similar figures) have no `game` at
    # all -- that's not a data gap, they're a genuinely different product
    # type, but lumping all of them into a bare "unknown" bucket next to
    # "pokemon"/"one_piece" reads as a bug rather than what it is. Grouped
    # by brand instead, same as everywhere else these are shown.
    collectible_brands = {row[0]: row[1] for row in session.execute(select(CollectibleProduct.catalog_item_id, CollectibleProduct.brand)).all()}
    for i in items:
        # falls back to the variant-agnostic eBay figure exactly like
        # /inventory does, so the dashboard's total can't disagree with
        # the page it links to
        price = prices.get((i.catalog_item_id, i.variant)) or ebay_prices.get(i.catalog_item_id)
        value = _aud(price, i.quantity) or 0.0
        if price is not None:
            priced_count += 1
        market_value += value
        game = tcg_games.get(i.catalog_item_id) or collectible_brands.get(i.catalog_item_id) or "unknown"
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

    # -- market-watch listings deliberately promoted into the buying pipeline --
    open_candidates = list(session.scalars(
        select(MarketListing).where(MarketListing.candidate_status.in_(OPEN_POTENTIAL_STATUSES))
        .order_by(MarketListing.candidate_updated_at.desc(), MarketListing.last_seen_at.desc())
        .limit(5)
    ))

    # -- Gameology Guild goal ---------------------------------------------
    # Read from the same service as the full tracker so its spend, fund and
    # recommendation can never drift from the compact home widget.
    guild_program = session.scalar(select(RetailerProgram).where(RetailerProgram.slug == "gameology-guild"))
    guild_state = None
    if guild_program is not None:
        from .retailer_goals_service import program_state
        guild_state = program_state(session, guild_program)

    events = session.scalars(select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(6)).all()

    # ---- widgets ----
    gauge_widget = (
        "<div class='widget'><div class='widget-head'><h3>Inventory Priced</h3><span class='widget-sub'>Coverage</span></div>"
        f"<div class='gauge-wrap'>{gauge_chart(priced_pct, color='#0891b2', label='priced')}"
        f"<div class='gauge-caption'>{priced_count:,} of {scanned_holdings:,} holdings have a known market price</div></div></div>"
    )

    guild_widget = ""
    if guild_state is not None:
        target = next((m.target_amount for m in guild_state["milestones"] if m.target_amount == 5000), None)
        target = target or max((m.target_amount for m in guild_state["milestones"]), default=1)
        pct = min(100, float(guild_state["spend"] / target * 100)) if target else 0
        best = guild_state["recommendation"]
        if best:
            best_line = (
                f"<div class='guild-best'><span>Best current match</span>"
                f"<strong>{escape(best['product'].name)}</strong>"
                f"<small>{f'${best['gameology_price']:,.2f}' if best['gameology_price'] is not None else 'price needed'}</small></div>"
            )
        else:
            best_line = "<div class='guild-best'><span>Best current match</span><strong>Add prices to rank the queue</strong></div>"
        guild_widget = (
            "<div class='widget guild-widget'><div class='widget-head'><h3>GAMEOLOGY</h3>"
            "<a href='/retailer-goals' class='widget-link'>Open tracker</a></div>"
            f"<div class='big-number'>${guild_state['spend']:,.0f} / ${target:,.0f}</div>"
            f"<div class='dash-progress'><span style='width:{pct:.1f}%'></span></div>"
            f"<div class='guild-fund'>Fund <strong>${guild_state['fund']['available']:,.2f}</strong></div>"
            + best_line + "</div>"
        )

    # -- widgets that only appear once they have something to say --------
    #
    # Sales are recorded by hand on /sales, which has never been used (see
    # HIDDEN_NAV in ui.py). An empty-state message is the right thing for a widget whose
    # data comes and goes; it is the wrong thing for one that has never
    # had any, because it reads as a broken feature rather than an unused
    # one. These three render the moment their first row exists.
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
        f"<tr><td>{escape(candidate.title)}</td><td>${candidate.landed_aud:,.2f}</td>"
        f"<td>{escape(candidate.candidate_status.value.replace('_', ' ').title())}</td></tr>"
        for candidate in open_candidates
    ) or "<tr><td colspan=3 class='chart-empty'>No open purchase candidates.</td></tr>"
    buys_widget = (
        "<div class='widget'><div class='widget-head'><h3>Purchase Candidates</h3><a href='/market-watch?view=candidates' class='widget-link'>View all</a></div>"
        f"<table class='widget-table'><thead><tr><th>Listing</th><th>Landed</th><th>Status</th></tr></thead><tbody>{buy_rows}</tbody></table></div>"
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

    # Cached, not a live eBay call -- this loads on every dashboard visit,
    # and the whole point of /ebay-listings existing is that "what's live"
    # can only be answered by asking eBay, which is comparatively slow and
    # rate-limited. Same local-only read _cached_rows already does for
    # /settings, so the two can never disagree about what's cached.
    from .ebay_listings import _cached_rows

    ebay_active = _cached_rows(session)
    ebay_last_synced = max((r.observed_at for r in ebay_active), default=None)
    if ebay_last_synced:
        ebay_body = (
            f"<div class='ops-grid'><div><div class='ops-label'>Active listings</div>"
            f"<div class='ops-value'>{len(ebay_active)}</div></div></div>"
            f"<p class='ops-catalog'>Checked {ebay_last_synced:%Y-%m-%d %H:%M} UTC</p>"
        )
    else:
        # Never 0 -- 0 is a specific claim about eBay this dashboard has
        # not made yet. This is the exact gap that prompted the page to
        # exist: the modern Sell API this app is built around reported 0
        # while two real listings were live, found only by asking a
        # different eBay API by hand.
        ebay_body = "<p class='chart-empty'>Never checked. <a href='/ebay-listings'>Check eBay</a> to see what's live.</p>"
    ebay_widget = (
        "<div class='widget'><div class='widget-head'><h3>eBay Listings</h3>"
        "<a href='/ebay-listings' class='widget-link'>View</a></div>" + ebay_body + "</div>"
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

    grid = "".join([
        guild_widget,
        gauge_widget,
        sales_widget if recent_sales else "",
        buys_widget if open_candidates else "",
        market_value_widget,
        channel_widget if channel_items else "",
        ageing_widget,
        ebay_widget,
        ops_widget,
    ])

    body = f"""<div class="dash-title">
    <h1>Sales Executive Dashboard</h1>
    <p>A live view of inventory, sales, and purchasing &middot; as of {now:%Y-%m-%d %H:%M} UTC</p>
  </div>
  {_jobs_html()}
  {_recovery_html()}
  {_storefront_readiness_html(settings)}
  <div class="widget-grid">{grid}</div>

  {_quicklinks_html(counts, storefront_url)}"""
    return HTMLResponse(page("EzBay — Dashboard", body, head_extra=f"<style>{_STYLE}{CHART_STYLE}</style>"))


_STYLE = """
.dash-title h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em;font-weight:800}
.dash-title p{margin:0 0 24px;color:var(--text-dim);font-size:13.5px}
.jobs{margin:0 0 20px;border-radius:12px;font-size:13px}
.jobs-ok{padding:9px 14px;background:var(--surface-sunken);border:1px solid var(--panel-border);
  color:var(--text-dim);display:flex;align-items:center;gap:9px}
.jobs-dot{width:7px;height:7px;border-radius:50%;background:var(--success);flex:none}
.jobs-bad{padding:15px 18px;background:var(--warning-bg);border:1px solid var(--warning)}
.jobs-head{margin:0 0 9px;font-weight:700;color:var(--warning)}
.jobs-bad ul{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:9px}
.jobs-bad li{line-height:1.45}
.jobs-what{display:block;color:var(--text-dim);font-size:12.5px}
.jobs-next{display:block;color:var(--text-faint);font-size:11.5px;margin-top:2px}
.jobs-foot{margin:11px 0 0;font-size:12px;color:var(--text-dim)}

.widget-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
@media (max-width:980px){.widget-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:640px){.widget-grid{grid-template-columns:1fr}}

.widget{background:var(--panel);border:1px solid var(--panel-border);border-radius:14px;padding:18px 20px;
  box-shadow:var(--shadow)}
.widget-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:14px}
.widget-head h3{margin:0;font-size:14px;font-weight:700}
.widget-sub{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.06em}
.widget-link{font-size:12px;font-weight:600}

.widget-table{width:100%;border-collapse:collapse;font-size:13px}
.widget-table th{text-align:left;color:var(--text-faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;
  font-weight:600;padding:0 0 8px;border-bottom:1px solid var(--panel-border)}
.widget-table td{padding:8px 0;border-bottom:1px solid var(--panel-border)}
.widget-table tr:last-child td{border-bottom:none}

.gauge-wrap{display:flex;flex-direction:column;align-items:center;gap:8px}
.gauge-caption{font-size:12px;color:var(--text-dim);text-align:center}

.value-donut-row{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.big-number{font-size:26px;font-weight:800;letter-spacing:-.02em}
.big-number-sub{font-size:12px;font-weight:500;color:var(--text-dim);margin-top:4px}

.ops-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:14px}
.ops-label{font-size:11px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em}
.ops-value{font-size:20px;font-weight:700}
.ops-catalog{font-size:12px;color:var(--text-dim);margin:0 0 10px}
.ops-events{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px;
  font-size:11.5px;color:var(--text-dim);max-height:110px;overflow-y:auto}

.quicklinks{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:20px;margin-top:26px;
  padding-top:22px;border-top:1px solid var(--panel-border)}
@media (max-width:780px){.quicklinks{grid-template-columns:repeat(2,1fr)}}
.quicklink-group h4{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin:0 0 10px}
.quicklink-group{display:flex;flex-direction:column;gap:7px}
.quicklink-group a{font-size:13px;color:var(--text)}
.quicklink-group a:hover{color:var(--accent)}
.ql-count{display:inline-block;min-width:18px;padding:0 6px;margin-left:4px;border-radius:999px;
  font-size:11px;font-weight:700;background:var(--warning-bg);color:var(--warning);
  font-variant-numeric:tabular-nums}
.guild-widget{border-color:rgba(var(--accent-rgb),.28);background:linear-gradient(145deg,var(--panel),rgba(var(--accent-rgb),.05))}
.dash-progress{height:9px;border-radius:999px;background:var(--surface-sunken);overflow:hidden;margin:12px 0}
.dash-progress span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.guild-fund{font-size:12px;color:var(--text-dim);margin-bottom:13px}.guild-fund strong{color:var(--text);font-size:16px;margin-left:5px}
.guild-best{display:flex;flex-direction:column;gap:3px;border-top:1px solid var(--panel-border);padding-top:11px}
.guild-best span,.guild-best small{font-size:11px;color:var(--text-dim)}.guild-best strong{font-size:13px}
"""
