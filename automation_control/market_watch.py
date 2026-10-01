"""Cross-market collectible discovery for Australia and Japan.

Automated collection is limited to approved APIs. Facebook Marketplace and
Depop finds can be entered from their public listing links without EzBay
storing an account session or scraping either service.
"""

import hashlib
import imaplib
import re
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from statistics import median
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .market_alert_import import (
    import_alert_email,
    import_unseen_alerts,
    imap_configured,
    mark_imported_alerts_seen,
)
from .market_scanner import live_public_search
from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CollectibleProduct,
    MarketListing,
    MarketListingMatch,
    MarketWatchTerm,
)
from .market_watch_service import (
    COUNTRY_LABELS,
    MARKET_SOURCE_DOMAINS,
    MARKET_SOURCE_FIXED_COUNTRIES,
    allowed_country_codes,
    associate_listing,
    classify_item_kind,
    exchange_rate_to_aud,
    listing_has_price,
    source_label,
    upsert_listing,
    valid_market_listing_url,
    watch_matches_title,
)
from .ui import brand_header, page, pill
from . import vocabulary

router = APIRouter(tags=["market-watch"])

KINDS = ["individual", "blind_box", "full_case", "bundle", "unknown"]
KIND_LABELS = {
    "individual": "Individuals", "blind_box": "Blind boxes",
    "full_case": "Full boxes / cases", "bundle": "Bundles", "unknown": "Needs review",
}
UNMATCHED_SET = "Unmatched / needs review"
AUTOMATED_SOURCES = {"ebay", "public_web"}
MANUAL_SOURCE_DOMAINS = MARKET_SOURCE_DOMAINS


def listing_set(item: MarketListing) -> str:
    """Canonical set named by a listing, never a speculative fuzzy guess."""
    found = vocabulary.match(item.title, vocabulary.SONNY_ANGEL)
    if found is None:
        return UNMATCHED_SET
    return found.series if found.kind == vocabulary.CHARACTER and found.series else found.name


def grouped_market(listings: list[MarketListing], watch_names: dict[str, str] | None = None):
    groups = defaultdict(lambda: defaultdict(list))
    for item in listings:
        group_name = (watch_names or {}).get(item.id) or listing_set(item)
        groups[group_name][classify_item_kind(item.title)].append(item)
    return groups


def _money(value) -> str:
    return f"A${value:,.2f}"


def _listing_card(item: MarketListing) -> str:
    kind = classify_item_kind(item.title)
    art = (f"<img src='{escape(item.image_url)}' alt='' loading='lazy'>" if item.image_url
           else "<span class='mw-no-photo'>No photo</span>")
    price = (
        f"<strong>{_money(item.landed_aud)} landed</strong>"
        f"<small>{escape(item.currency)} {item.price:,.2f} + {item.shipping:,.2f} shipping"
        f" · {escape(item.country or 'location unknown')}</small>"
        if listing_has_price(item) else
        f"<strong>Price not supplied</strong><small>{escape(item.country or 'location unknown')}"
        " · confirm on marketplace</small>"
    )
    confidence = (item.raw or {}).get("location_confidence")
    location_note = "<small class='location-note'>Region inferred from public search; confirm before buying.</small>" if confidence == "search_region" else ""
    return (
        "<article class='mw-card'>" + art + "<div class='mw-copy'>"
        f"<div>{pill(escape(source_label(item.source)), 'info')} {pill(KIND_LABELS[kind], 'neutral')}</div>"
        f"<h3>{escape(item.title)}</h3>{price}{location_note}"
        f"<a class='btn' href='{escape(item.url)}' target='_blank' rel='noopener'>Open listing</a>"
        "</div></article>"
    )


def _live_result_card(item: dict) -> str:
    price = (
        f"<strong>{escape(item['currency'])} {item['price']:,.2f}</strong>"
        if item.get("price") is not None else
        "<strong>Price not detected</strong>"
    )
    description = escape((item.get("description") or "")[:300])
    return (
        "<article class='mw-card'><span class='mw-no-photo'>Live result</span><div class='mw-copy'>"
        f"<div>{pill(escape(source_label(item['source'])), 'info')} "
        f"{pill(escape(item['country']), 'neutral')}</div>"
        f"<h3>{escape(item['title'])}</h3>{price}<small>{description}</small>"
        f"<a class='btn' href='{escape(item['url'])}' target='_blank' rel='noopener'>Open source page</a>"
        "</div></article>"
    )


@router.get("/market-watch/live-search", response_class=HTMLResponse)
async def market_watch_live_search(
    request: Request,
    query: str = Query(..., min_length=2, max_length=256),
    country: str = Query("AU"),
    user: str = Depends(require_dashboard_user),
):
    """Display Brave results transiently; never save them to EzBay."""
    settings = request.app.state.settings
    country = country.upper()
    try:
        rows = await live_public_search(settings, query, country, limit=20)
        cards = "".join(_live_result_card(row) for row in rows)
        content = cards or "<div class='panel'>No allow-listed public-web results were found.</div>"
        error = ""
    except (ValueError, httpx.HTTPError) as exc:
        content = ""
        error = f"<div class='panel bad-note'>{escape(str(exc))}</div>"

    allowed = allowed_country_codes(settings.market_allowed_countries)
    options = "".join(
        f"<option value='{code}'{' selected' if code == country else ''}>"
        f"{escape(COUNTRY_LABELS.get(code, code))}</option>" for code in allowed
    )
    body = brand_header("Live public-web search") + (
        "<section class='panel'><p class='muted'>Results are displayed for this request only and are not saved, "
        "included in alerts, or added to price history. Confirm the seller location and price on the source page.</p>"
        "<form class='filters' method='get' action='/market-watch/live-search'>"
        f"<label>Collectible<input name='query' value='{escape(query)}' required maxlength='256'></label>"
        f"<label>Search region<select name='country'>{options}</select></label>"
        "<button type='submit'>Search public web</button></form>"
        "<a class='btn ghost' href='/market-watch'>Back to Market Watch</a></section>"
        f"{error}<div class='mw-grid'>{content}</div>{_STYLE}"
    )
    return HTMLResponse(page(f"EzBay — Live search: {query}", body))


def _watch_card(watch: MarketWatchTerm) -> str:
    countries = " & ".join(COUNTRY_LABELS.get(code, code) for code in (watch.countries or []))
    sources = " + ".join(source_label(source) for source in (watch.sources or []))
    last_scan = watch.last_scanned_at.strftime("%d %b %H:%M") if watch.last_scanned_at else "Not scanned yet"
    summary = watch.last_scan_summary or {}
    scan_errors = summary.get("errors") or []
    status = pill("Active", "ok") if watch.active else pill("Paused", "neutral")
    issue = f"<small class='watch-error'>{escape('; '.join(scan_errors))}</small>" if scan_errors else ""
    selected_countries = set(watch.countries or [])
    selected_sources = set(watch.sources or [])
    ceiling = f"{watch.max_landed_aud:.2f}" if watch.max_landed_aud is not None else ""
    edit = (
        "<details class='watch-edit'><summary>Edit</summary>"
        f"<form method='post' action='/market-watch/watches/{escape(watch.id)}'>"
        f"<label>Name<input name='name' maxlength='128' value='{escape(watch.name)}' required></label>"
        f"<label>Search terms<input name='query' maxlength='256' value='{escape(watch.query)}' required></label>"
        f"<label>Category<input name='category' maxlength='64' value='{escape(watch.category or '')}'></label>"
        "<div class='check-row'><span>Locations</span>"
        f"<label><input type='checkbox' name='countries' value='AU'{' checked' if 'AU' in selected_countries else ''}> AU</label>"
        f"<label><input type='checkbox' name='countries' value='JP'{' checked' if 'JP' in selected_countries else ''}> JP</label></div>"
        "<div class='check-row'><span>Sources</span>"
        f"<label><input type='checkbox' name='sources' value='ebay'{' checked' if 'ebay' in selected_sources else ''}> eBay</label>"
        f"<label><input type='checkbox' name='sources' value='public_web'{' checked' if 'public_web' in selected_sources else ''}> Public web</label></div>"
        f"<label>Max landed AUD<input name='max_landed_aud' type='number' min='0.01' step='0.01' value='{ceiling}'></label>"
        "<button class='ghost' type='submit'>Save changes</button></form></details>"
    )
    return (
        "<article class='watch-card'>"
        f"<div class='watch-title'><strong>{escape(watch.name)}</strong>{status}</div>"
        f"<code>{escape(watch.query)}</code><small>{escape(countries or 'No locations')} · {escape(sources or 'No sources')}</small>"
        f"<small>{escape(last_scan)} · {int(summary.get('found') or 0)} found</small>{issue}"
        "<div class='watch-actions'>"
        f"<form method='post' action='/market-watch/refresh'><input type='hidden' name='watch_id' value='{escape(watch.id)}'>"
        f"<button class='ghost' type='submit'{' disabled' if not watch.active else ''}>Scan</button></form>"
        f"<form method='post' action='/market-watch/watches/{escape(watch.id)}/toggle'>"
        f"<button class='ghost' type='submit'>{'Pause' if watch.active else 'Resume'}</button></form>"
        f"</div>{edit}</article>"
    )


@router.get("/market-watch", response_class=HTMLResponse)
def market_watch(request: Request, source: str = Query(""), kind: str = Query(""), set_name: str = Query(""),
                 country: str = Query(""), watch_id: str = Query(""),
                 notice: str = Query(""), error: str = Query(""),
                 user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    allowed = allowed_country_codes(settings.market_allowed_countries)
    country = country.upper()
    if country not in allowed:
        country = ""
    stmt = select(MarketListing).where(
        MarketListing.active.is_(True), MarketListing.country.in_(allowed),
    )
    if watch_id:
        stmt = stmt.join(
            MarketListingMatch,
            MarketListingMatch.market_listing_id == MarketListing.id,
        ).where(MarketListingMatch.watch_term_id == watch_id)
    if source:
        stmt = stmt.where(MarketListing.source == source)
    if kind:
        stmt = stmt.where(MarketListing.item_kind == kind)
    if country:
        stmt = stmt.where(MarketListing.country == country)
    listings = session.scalars(stmt.order_by(MarketListing.last_seen_at.desc()).limit(300)).unique().all()
    sources = session.execute(
        select(MarketListing.source, func.count())
        .where(MarketListing.active.is_(True), MarketListing.country.in_(allowed))
        .group_by(MarketListing.source)
    ).all()
    catalogue_rows = session.scalar(select(func.count()).select_from(CollectibleProduct)) or 0
    watches = session.scalars(select(MarketWatchTerm).order_by(MarketWatchTerm.active.desc(), MarketWatchTerm.name)).all()
    matches = session.execute(
        select(MarketListingMatch.market_listing_id, MarketWatchTerm.name)
        .join(MarketWatchTerm, MarketWatchTerm.id == MarketListingMatch.watch_term_id)
        .where(MarketListingMatch.market_listing_id.in_([listing.id for listing in listings]))
        .order_by(MarketWatchTerm.name)
    ).all() if listings else []
    watch_names: dict[str, str] = {}
    for listing_id, name in matches:
        watch_names.setdefault(listing_id, name)

    groups = grouped_market(listings, watch_names)
    all_set_names = sorted(groups, key=lambda name: (name == UNMATCHED_SET, name.lower()))
    shown_set_names = [name for name in all_set_names if not set_name or name == set_name]
    set_sections = []
    for name in shown_set_names:
        buckets = groups[name]
        price_cells = []
        set_count = 0
        for category in KINDS:
            rows = buckets.get(category, [])
            set_count += len(rows)
            values = [row.landed_aud for row in rows if listing_has_price(row)]
            if values:
                price_cells.append(
                    f"<div class='mw-price'><span>{KIND_LABELS[category]}</span>"
                    f"<strong>{_money(median(values))}</strong>"
                    f"<small>median · {len(values)} listing{'s' if len(values) != 1 else ''}<br>"
                    f"range {_money(min(values))}–{_money(max(values))}</small></div>"
                )
        listing_cards = "".join(_listing_card(item) for category in KINDS for item in buckets.get(category, []))
        set_sections.append(
            f"<details class='mw-set'{' open' if len(shown_set_names) == 1 else ''}>"
            f"<summary><span><strong>{escape(name)}</strong><small>{set_count} listings</small></span>"
            "<span class='mw-chevron'>⌄</span></summary>"
            f"<div class='mw-prices'>{''.join(price_cells)}</div>"
            f"<div class='mw-grid'>{listing_cards}</div></details>"
        )
    source_options = "".join(
        f"<option value='{escape(s)}'{' selected' if s == source else ''}>{escape(source_label(s))} ({n})</option>"
        for s, n in sources
    )
    kind_options = "".join(f"<option value='{k}'{' selected' if k == kind else ''}>{KIND_LABELS[k]}</option>" for k in KINDS)
    set_options = "".join(f"<option value='{escape(name)}'{' selected' if name == set_name else ''}>{escape(name)}</option>" for name in all_set_names)
    country_options = "".join(
        f"<option value='{code}'{' selected' if code == country else ''}>{escape(COUNTRY_LABELS.get(code, code))}</option>"
        for code in allowed
    )
    manual_country_options = "".join(
        f"<option value='{code}'>{escape(COUNTRY_LABELS.get(code, code))}</option>" for code in allowed
    )
    watch_options = "".join(
        f"<option value='{escape(watch.id)}'{' selected' if watch.id == watch_id else ''}>"
        f"{escape(watch.name)}</option>" for watch in watches
    )
    allowed_label = " & ".join(COUNTRY_LABELS.get(code, code) for code in allowed)
    notices = (f"<div class='panel ok-note'>{escape(notice)}</div>" if notice else "") + (
        f"<div class='panel bad-note'>{escape(error)}</div>" if error else ""
    )
    manual_source_options = "".join(
        f"<option value='{key}'>{escape(source_label(key))}</option>" for key in MANUAL_SOURCE_DOMAINS
    )
    manual_form = (
        "<details class='panel manual-add'><summary>Add a marketplace find</summary>"
        "<p class='muted'>Paste a marketplace link without giving EzBay your login. This also supports Gumtree and Japanese proxy-market links.</p>"
        "<form method='post' action='/market-watch/listings'>"
        f"<label>Marketplace<select name='source'>{manual_source_options}</select></label>"
        "<label>Listing URL<input name='url' type='url' required placeholder='https://…'></label>"
        "<label>Title<input name='title' required placeholder='Collectible listing title'></label>"
        f"<label>Seller location<select name='country'>{manual_country_options}</select></label>"
        "<div class='manual-money'><label>Price<input name='price' type='number' min='0.01' step='0.01' required></label>"
        "<label>Shipping<input name='shipping' type='number' min='0' step='0.01' value='0' required></label>"
        "<label>Currency<select name='currency'><option value='AUD'>AUD</option><option value='JPY'>JPY</option></select></label></div>"
        "<label>Seller (optional)<input name='seller'></label><label>Condition (optional)<input name='condition'></label>"
        "<label>Image URL (optional)<input name='image_url' type='url'></label>"
        "<button type='submit'>Add to market watch</button></form></details>"
    )
    live_search_panel = (
        "<section class='panel live-search-panel'><h2>Live public-web search</h2>"
        "<p class='muted'>Search approved Australia or Japan marketplace domains using Brave. "
        "Results are temporary and are never saved to EzBay.</p>"
        "<form class='filters' method='get' action='/market-watch/live-search'>"
        "<label>Collectible<input name='query' required maxlength='256' "
        "placeholder='Dragon Ball Carddass'></label>"
        f"<label>Search region<select name='country'>{manual_country_options}</select></label>"
        f"<button type='submit'{' disabled' if not settings.brave_search_api_key else ''}>Search now</button>"
        "</form>"
        + ("" if settings.brave_search_api_key else "<p class='muted'>Brave Search is not configured.</p>")
        + "</section>"
    )
    watch_cards = "".join(_watch_card(watch) for watch in watches) or "<p class='muted'>No watches yet. Add your first one below.</p>"
    watch_panel = (
        "<section class='panel watch-panel'><div class='watch-head'><div><h2>Collectible watches</h2>"
        "<p class='muted'>eBay uses exact AU/JP item locations. Public-web discovery runs only when a licensed Brave key is configured.</p></div>"
        "<form method='post' action='/market-watch/refresh'><button type='submit'>Scan active watches</button></form></div>"
        f"<div class='watch-grid'>{watch_cards}</div>"
        "<details class='watch-add'><summary>Add a watch</summary><form method='post' action='/market-watch/watches'>"
        "<label>Name<input name='name' maxlength='128' required placeholder='Dragon Ball Carddass'></label>"
        "<label>Search terms<input name='query' maxlength='256' required placeholder='Dragon Ball Carddass'></label>"
        "<label>Category (optional)<input name='category' maxlength='64' placeholder='Cards'></label>"
        "<div class='check-row'><span>Locations</span><label><input type='checkbox' name='countries' value='AU' checked> Australia</label>"
        "<label><input type='checkbox' name='countries' value='JP' checked> Japan</label></div>"
        "<div class='check-row'><span>Sources</span><label><input type='checkbox' name='sources' value='ebay' checked> eBay</label>"
        "<label><input type='checkbox' name='sources' value='public_web'> Public web</label></div>"
        "<label>Maximum landed price AUD (optional)<input name='max_landed_aud' type='number' min='0.01' step='0.01'></label>"
        "<button type='submit'>Create watch</button></form></details></section>"
    )
    inbox_panel = (
        "<section class='panel inbox-panel'><h2>Alert inbox</h2>"
        "<p class='muted'>Upload an exported .eml from Gumtree, Buyee, ZenMarket, FROM JAPAN, Depop or Facebook. EzBay reads supported links only and never logs into the marketplace.</p>"
        "<form method='post' action='/market-watch/alerts/import' enctype='multipart/form-data'>"
        "<input name='email_file' type='file' accept='.eml,message/rfc822' required>"
        f"<label>Fallback location<select name='country'>{manual_country_options}</select></label>"
        "<button type='submit'>Import email</button></form>"
        + ("<form method='post' action='/market-watch/alerts/fetch'><button class='ghost' type='submit'>Check alert mailbox</button></form>" if imap_configured(settings) else "<p class='muted'>Optional IMAP mailbox: not configured.</p>")
        + "</section>"
    )
    body = brand_header("Collectible market watch") + notices + watch_panel + (
        "<div class='mw-stats'>"
        f"<div><strong>{len(listings)}</strong><span>live matches shown</span></div>"
        f"<div><strong>{escape(allowed_label)}</strong><span>seller locations only</span></div>"
        f"<div><strong>{catalogue_rows}</strong><span>collectible catalogue rows</span></div>"
        f"<div><strong>{'Ready' if settings.telegram_bot_token and settings.telegram_chat_id else 'Needs setup'}</strong><span>Telegram</span></div>"
        f"<div><strong>{'Ready' if settings.discord_webhook_url else 'Needs setup'}</strong><span>Discord</span></div></div>"
        "<form class='panel filters' method='get'><label>Marketplace<select name='source'><option value=''>All</option>"
        + source_options + "</select></label><label>Seller location<select name='country'><option value=''>Australia &amp; Japan</option>"
        + country_options + "</select></label><label>Watch<select name='watch_id'><option value=''>All watches</option>"
        + watch_options + "</select></label><label>Group<select name='set_name'><option value=''>All groups</option>"
        + set_options + "</select></label><label>Item type<select name='kind'><option value=''>All types</option>"
        + kind_options + "</select></label><button>Filter</button></form>"
        + ("<div class='mw-sets'>" + "".join(set_sections) + "</div>" if set_sections else "<div class='panel'>No scanned listings match these filters.</div>")
        + inbox_panel
        + live_search_panel
        + manual_form
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Collectible market watch", body))


@router.post("/market-watch/refresh")
def refresh_market_watch(background_tasks: BackgroundTasks, watch_id: str = Form(""),
                         user: str = Depends(require_dashboard_user)):
    from scripts.scan_collectible_market import run

    ids = [watch_id] if watch_id else None
    background_tasks.add_task(run, 20, False, False, ids)
    label = "Selected watch" if watch_id else "Active watches"
    return RedirectResponse(
        "/market-watch?" + urlencode({"notice": f"{label} scan started for Australia and Japan"}),
        status_code=303,
    )


@router.post("/market-watch/watches")
def add_market_watch(
    request: Request,
    name: str = Form(...),
    query: str = Form(...),
    category: str = Form(""),
    countries: list[str] = Form(default=[]),
    sources: list[str] = Form(default=[]),
    max_landed_aud: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    name, query, category = name.strip(), query.strip(), category.strip()
    allowed = set(allowed_country_codes(request.app.state.settings.market_allowed_countries))
    selected_countries = list(dict.fromkeys(code.upper() for code in countries if code.upper() in allowed))
    selected_sources = list(dict.fromkeys(source for source in sources if source in AUTOMATED_SOURCES))
    try:
        if not name or len(name) > 128:
            raise ValueError("Watch name is required and must be 128 characters or fewer.")
        if not query or len(query) > 256:
            raise ValueError("Search terms are required and must be 256 characters or fewer.")
        if not selected_countries:
            raise ValueError("Choose Australia, Japan, or both.")
        if not selected_sources:
            raise ValueError("Choose at least one discovery source.")
        existing = session.scalar(
            select(MarketWatchTerm).where(func.lower(MarketWatchTerm.query) == query.lower()),
        )
        if existing:
            raise ValueError("A watch with those search terms already exists.")
        ceiling = Decimal(max_landed_aud) if max_landed_aud.strip() else None
        if ceiling is not None and ceiling <= 0:
            raise ValueError("Maximum landed price must be positive.")
    except (InvalidOperation, ValueError) as exc:
        return RedirectResponse("/market-watch?" + urlencode({"error": str(exc)}), status_code=303)

    watch = MarketWatchTerm(
        name=name,
        query=query,
        category=category or None,
        countries=selected_countries,
        sources=selected_sources,
        max_landed_aud=ceiling,
        active=True,
    )
    session.add(watch)
    session.flush()
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.term_created",
        resource_type="market_watch_term", resource_id=watch.id, outcome="success",
        correlation_id=watch.id,
        details={"query": query, "countries": selected_countries, "sources": selected_sources},
    )
    session.commit()
    return RedirectResponse(
        "/market-watch?" + urlencode({"notice": f"Watch added: {name}"}), status_code=303,
    )


@router.post("/market-watch/watches/{watch_id}/toggle")
def toggle_market_watch(watch_id: str, user: str = Depends(require_dashboard_user),
                        session: Session = Depends(get_session)) -> RedirectResponse:
    watch = session.get(MarketWatchTerm, watch_id)
    if watch is None:
        return RedirectResponse("/market-watch?error=Watch+not+found", status_code=303)
    watch.active = not watch.active
    watch.updated_at = datetime.now(UTC)
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.term_toggled",
        resource_type="market_watch_term", resource_id=watch.id, outcome="success",
        correlation_id=watch.id, details={"active": watch.active},
    )
    session.commit()
    return RedirectResponse(
        "/market-watch?" + urlencode({"notice": f"{watch.name} {'resumed' if watch.active else 'paused'}"}),
        status_code=303,
    )


@router.post("/market-watch/watches/{watch_id}")
def update_market_watch(
    watch_id: str,
    request: Request,
    name: str = Form(...),
    query: str = Form(...),
    category: str = Form(""),
    countries: list[str] = Form(default=[]),
    sources: list[str] = Form(default=[]),
    max_landed_aud: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    watch = session.get(MarketWatchTerm, watch_id)
    if watch is None:
        return RedirectResponse("/market-watch?error=Watch+not+found", status_code=303)
    name, query, category = name.strip(), query.strip(), category.strip()
    allowed = set(allowed_country_codes(request.app.state.settings.market_allowed_countries))
    selected_countries = list(dict.fromkeys(code.upper() for code in countries if code.upper() in allowed))
    selected_sources = list(dict.fromkeys(source for source in sources if source in AUTOMATED_SOURCES))
    try:
        if not name or len(name) > 128 or not query or len(query) > 256:
            raise ValueError("Name and search terms are required.")
        if not selected_countries or not selected_sources:
            raise ValueError("Choose at least one location and one discovery source.")
        duplicate = session.scalar(select(MarketWatchTerm).where(
            func.lower(MarketWatchTerm.query) == query.lower(),
            MarketWatchTerm.id != watch_id,
        ))
        if duplicate:
            raise ValueError("A watch with those search terms already exists.")
        ceiling = Decimal(max_landed_aud) if max_landed_aud.strip() else None
        if ceiling is not None and ceiling <= 0:
            raise ValueError("Maximum landed price must be positive.")
    except (InvalidOperation, ValueError) as exc:
        return RedirectResponse("/market-watch?" + urlencode({"error": str(exc)}), status_code=303)

    watch.name = name
    watch.query = query
    watch.category = category or None
    watch.countries = selected_countries
    watch.sources = selected_sources
    watch.max_landed_aud = ceiling
    watch.updated_at = datetime.now(UTC)
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.term_updated",
        resource_type="market_watch_term", resource_id=watch.id, outcome="success",
        correlation_id=watch.id,
        details={"query": query, "countries": selected_countries, "sources": selected_sources},
    )
    session.commit()
    return RedirectResponse(
        "/market-watch?" + urlencode({"notice": f"Watch updated: {name}"}), status_code=303,
    )


@router.post("/market-watch/alerts/import")
async def upload_market_alert(
    email_file: UploadFile = File(...), country: str = Form("AU"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> RedirectResponse:
    country = country.upper()
    if country not in {"AU", "JP"}:
        country = "AU"
    try:
        result = await import_alert_email(session, await email_file.read(), default_country=country)
    except (ValueError, httpx.HTTPError) as exc:
        session.rollback()
        return RedirectResponse("/market-watch?" + urlencode({"error": str(exc)}), status_code=303)
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.alert_email_imported",
        resource_type="market_alert_import", resource_id=None, outcome="success",
        correlation_id=hashlib.sha256((email_file.filename or "email").encode()).hexdigest()[:36],
        details={"status": result.status, "imported": result.imported, "updated": result.updated},
    )
    session.commit()
    return RedirectResponse("/market-watch?" + urlencode({"notice": result.detail}), status_code=303)


@router.post("/market-watch/alerts/fetch")
async def fetch_market_alerts(request: Request, user: str = Depends(require_dashboard_user),
                              session: Session = Depends(get_session)) -> RedirectResponse:
    try:
        result = await import_unseen_alerts(session, request.app.state.settings)
    except (ValueError, OSError, imaplib.IMAP4.error, httpx.HTTPError, RuntimeError) as exc:
        session.rollback()
        return RedirectResponse("/market-watch?" + urlencode({"error": str(exc)}), status_code=303)
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.alert_mailbox_checked",
        resource_type="market_alert_import", resource_id=None, outcome="success",
        correlation_id=hashlib.sha256(str(datetime.now(UTC)).encode()).hexdigest()[:36],
        details={"imported": result.imported, "updated": result.updated},
    )
    session.commit()
    try:
        await mark_imported_alerts_seen(request.app.state.settings, result)
    except (OSError, imaplib.IMAP4.error):
        return RedirectResponse(
            "/market-watch?" + urlencode({
                "notice": result.detail,
                "error": "Listings were saved, but the email could not be marked as read.",
            }),
            status_code=303,
        )
    return RedirectResponse("/market-watch?" + urlencode({"notice": result.detail}), status_code=303)


def _valid_market_url(source: str, url: str) -> bool:
    return valid_market_listing_url(source, url)


def _source_from_url(url: str) -> str | None:
    for source in MANUAL_SOURCE_DOMAINS:
        if _valid_market_url(source, url):
            return source
    return None


def _optional_web_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Image URL must be an http(s) URL.")
    return value


@router.get("/market-watch/share", response_class=HTMLResponse)
def share_to_market_watch(
    title: str = Query(""), text: str = Query(""), url: str = Query(""),
    user: str = Depends(require_dashboard_user),
) -> HTMLResponse:
    if not url:
        found = re.search(r"https?://\S+", text)
        url = found.group(0).rstrip(".,);]") if found else ""
    source = _source_from_url(url) or "facebook"
    if not title:
        title = re.sub(r"https?://\S+", "", text).strip()
    options = "".join(
        f"<option value='{key}'{' selected' if key == source else ''}>{escape(source_label(key))}</option>"
        for key in MANUAL_SOURCE_DOMAINS
    )
    body = (
        brand_header("Share to EzBay")
        + "<div class='panel share-card'><p class='muted'>Confirm the visible listing details. EzBay stores the link; it does not sign into or scrape the marketplace.</p>"
        "<form method='post' action='/market-watch/listings'>"
        f"<label>Marketplace<select name='source'>{options}</select></label>"
        f"<label>Listing URL<input name='url' type='url' value='{escape(url)}' required></label>"
        f"<label>Title<input name='title' value='{escape(title)}' required></label>"
        "<label>Seller location<select name='country'><option value='AU'>Australia</option><option value='JP'>Japan</option></select></label>"
        "<div class='manual-money'><label>Price (optional)<input name='price' type='number' min='0.01' step='0.01'></label>"
        "<label>Shipping<input name='shipping' type='number' min='0' step='0.01' value='0'></label>"
        "<label>Currency<select name='currency'><option value='AUD'>AUD</option><option value='JPY'>JPY</option></select></label></div>"
        "<label>Image URL (optional)<input name='image_url' type='url'></label>"
        "<button type='submit'>Save to market watch</button></form></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Share listing", body))


@router.post("/market-watch/listings")
async def add_manual_market_listing(
    request: Request, source: str = Form(...), url: str = Form(...), title: str = Form(...),
    country: str = Form(...), price: str = Form(""), shipping: str = Form("0"),
    currency: str = Form("AUD"), seller: str = Form(""), condition: str = Form(""),
    image_url: str = Form(""), user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    settings = request.app.state.settings
    allowed = allowed_country_codes(settings.market_allowed_countries)
    source, country, currency = source.strip().lower(), country.strip().upper(), currency.strip().upper()
    title, url = title.strip(), url.strip()
    try:
        if source not in MANUAL_SOURCE_DOMAINS:
            raise ValueError("Choose a supported marketplace.")
        if not _valid_market_url(source, url):
            raise ValueError(f"That URL is not a {source_label(source)} listing link.")
        country = MARKET_SOURCE_FIXED_COUNTRIES.get(source, country)
        if country not in allowed:
            raise ValueError("Seller location must be Australia or Japan.")
        if currency not in {"AUD", "JPY"}:
            raise ValueError("Currency must be AUD or JPY.")
        amount = Decimal(price) if price.strip() else Decimal("0")
        delivery = Decimal(shipping or "0")
        if amount < 0 or delivery < 0:
            raise ValueError("Price and shipping cannot be negative.")
        if not title:
            raise ValueError("Title is required.")
        photo = _optional_web_url(image_url)
        rate = await exchange_rate_to_aud(currency)
    except (InvalidOperation, ValueError, KeyError, httpx.HTTPError) as exc:
        return RedirectResponse("/market-watch?" + urlencode({"error": str(exc)}), status_code=303)

    external_id = hashlib.sha256(url.encode()).hexdigest()
    listing, created = upsert_listing(
        session, source=source, external_id=external_id, title=title, url=url,
        image_url=photo, seller=seller.strip() or None, condition=condition.strip() or None,
        country=country, price=amount, shipping=delivery, currency=currency,
        exchange_rate=rate, raw={"entry_method": "manual", "price_available": amount > 0},
    )
    watches = session.scalars(select(MarketWatchTerm).where(MarketWatchTerm.active.is_(True))).all()
    for watch in watches:
        if country in (watch.countries or []) and watch_matches_title(title, watch):
            associate_listing(session, listing, watch, matched_by="manual")
    record_event(
        session, actor_type="user", actor_id=user, action="market_watch.manual_listing",
        resource_type="market_listing", resource_id=listing.id, outcome="success",
        correlation_id=listing.id, details={"source": source, "country": country, "created": created},
    )
    session.commit()
    verb = "added" if created else "updated"
    return RedirectResponse(
        "/market-watch?" + urlencode({"notice": f"{source_label(source)} listing {verb}"}),
        status_code=303,
    )


_STYLE = """<style>
.mw-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.mw-stats div,.mw-card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:16px}
.mw-stats strong,.mw-stats span,.mw-card small{display:block}.mw-stats strong{font-size:22px}
.mw-stats span,.mw-card small,.mw-set summary small{color:#64748b}.mw-sets{display:flex;flex-direction:column;gap:14px}
.mw-set{background:#fff;padding:0;border-radius:14px}.mw-set summary{display:flex;justify-content:space-between;align-items:center;padding:18px 20px;list-style:none}
.mw-set summary::-webkit-details-marker{display:none}.mw-set summary strong,.mw-set summary small{display:block}.mw-set summary strong{font-size:18px}.mw-chevron{font-size:22px}
.mw-prices{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;padding:0 16px 16px}.mw-price{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px}
.mw-price span,.mw-price strong,.mw-price small{display:block}.mw-price span{font-size:12px;color:#64748b}.mw-price strong{font-size:19px;margin:3px 0}
.mw-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;padding:16px;border-top:1px solid #e2e8f0}.mw-card{padding:0;overflow:hidden}
.mw-card>img,.mw-no-photo{width:100%;height:200px;object-fit:contain;background:#f8fafc;display:flex;align-items:center;justify-content:center}.mw-copy{padding:14px}.mw-copy h3{font-size:15px;min-height:38px}.mw-copy .btn{display:inline-block;margin-top:12px}
.location-note{color:#b45309!important;margin-top:5px}.watch-panel{margin-bottom:16px}.watch-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.watch-head h2{margin:0}
.watch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:14px 0}.watch-card{border:1px solid var(--panel-border);border-radius:12px;padding:13px;background:var(--surface-sunken);min-width:0}
.watch-card code,.watch-card small{display:block;overflow-wrap:anywhere}.watch-card code{margin:8px 0}.watch-title,.watch-actions{display:flex;align-items:center;justify-content:space-between;gap:8px}.watch-actions{justify-content:flex-start;margin-top:10px}.watch-actions form{margin:0}.watch-error{color:var(--danger)!important;margin-top:5px}
.watch-add{margin-top:12px}.watch-add form{max-width:620px;margin-top:12px}.check-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:10px 0}.check-row>span{font-weight:700}.check-row label{display:flex;align-items:center;gap:6px;margin:0}
.inbox-panel{margin-top:18px}.inbox-panel form{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin:10px 0}.inbox-panel form label{margin:0}.share-card{max-width:680px}.share-card form{margin-top:14px}
.warning{border-left:4px solid #f59e0b}.market-actions{display:flex;margin:0 0 14px}.market-actions form{margin:0}.manual-add{margin-top:18px}.manual-add form{max-width:620px;margin-top:14px}
.manual-money{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}.ok-note{border-left:4px solid var(--success)}.bad-note{border-left:4px solid var(--danger)}
@media(max-width:680px){.mw-prices{grid-template-columns:repeat(2,minmax(0,1fr))}.mw-grid{grid-template-columns:1fr}.manual-money{grid-template-columns:1fr}.watch-head{display:block}.watch-head form{margin-top:10px}.watch-grid{grid-template-columns:1fr}.inbox-panel form{align-items:stretch;flex-direction:column}}
</style>"""
