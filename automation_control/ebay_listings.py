"""What's actually live on eBay right now -- Module 27.

Answers a question that turned out to have no page anywhere: two items
were relisted through Seller Hub's Relist button, and nothing in this app
could see them. `/api/ebay/listings` already existed and calls the real
eBay API, but through the Sell Inventory API, which only sees listings
that were themselves *created* through the Inventory API -- a listing
made any other way (by hand, Relist, bulk File Exchange) is permanently
invisible to it. Confirmed on this account directly: that route reported
0 while GetMyeBaySelling (the call Seller Hub itself runs on) found 2.

This page is that second, correct call, adapters/ebay_trading.py's
GetMyeBaySelling wrapper -- so "what's live" here means the same thing it
does on eBay's own Seller Hub page, regardless of how a listing got
there.

Read-only, same as everything else that talks to eBay outside
ebay_publish.py: this can refresh the local mirror of what eBay reports,
never create, edit, or end a listing.

Cached rather than live on every page load -- a background sync isn't
wired up yet (there's no timer for this the way ebay-price-import.timer
exists for prices), so a human clicking "Refresh" is currently the only
thing that ever calls eBay here. The page says how stale that cache is
rather than pretending it's always fresh.
"""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError
from .adapters.ebay_trading import ActiveListing, EbayTradingReadAdapter
from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .ebay_oauth import OAuthError, ebay_services, valid_user_access_token
from .models import CatalogItem, EbayListing, InventoryItem
from .ui import brand_header, page

router = APIRouter()

# Tags a row in the shared EbayListing table as having come from this
# route rather than /api/ebay/listings' Sell API sync -- both write into
# the same table (it's documented as a wholesale mirror of whatever eBay
# reports), and without this a future Sell API listing and a Trading API
# one with the same item id would be indistinguishable in raw_snapshot.
SOURCE = "trading_api"


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _upsert(session: Session, listings: list[ActiveListing]) -> int:
    now = datetime.now(UTC)
    for item in listings:
        price_minor = int(item.price * 100) if item.price is not None else 0
        values = dict(
            title=item.title, currency=item.currency or "AUD", current_price_minor=price_minor,
            quantity=item.quantity, listing_status="Active", marketplace=None,
            raw_snapshot={
                "source": SOURCE, "quantity_sold": item.quantity_sold,
                "listing_type": item.listing_type, "view_item_url": item.view_item_url,
                "start_time": item.start_time.isoformat() if item.start_time else None,
            },
            observed_at=now,
        )
        existing = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == item.ebay_item_id))
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
        else:
            session.add(EbayListing(ebay_listing_id=item.ebay_item_id, **values))
    session.commit()
    return len(listings)


def _cached_rows(session: Session) -> list[EbayListing]:
    rows = session.scalars(select(EbayListing).order_by(EbayListing.observed_at.desc())).all()
    return [r for r in rows if (r.raw_snapshot or {}).get("source") == SOURCE]


def _card_html(row: EbayListing) -> str:
    """One listing as a stacked card rather than a table row -- a wide
    Item/Price/Qty/Sold/Type table forces horizontal scrolling on a phone,
    which is where this page actually gets used."""
    snapshot = row.raw_snapshot or {}
    price = Decimal(row.current_price_minor) / 100
    url = snapshot.get("view_item_url")
    title = f"<a href='{escape(url)}' target='_blank' rel='noopener'>{escape(row.title)}</a>" if url else escape(row.title)
    sold = snapshot.get("quantity_sold") or 0
    qty = row.quantity if row.quantity is not None else "?"
    listing_type = snapshot.get("listing_type") or ""
    return (
        "<div class='ebay-card'>"
        f"<div class='ebay-card-title'>{title}</div>"
        f"<div class='ebay-card-id'>{escape(row.ebay_listing_id)}"
        f"{' &middot; ' + escape(listing_type) if listing_type else ''}</div>"
        "<div class='ebay-card-stats'>"
        f"<span class='ebay-stat'><b>{row.currency} ${price:,.2f}</b></span>"
        f"<span class='ebay-stat'>{qty} left</span>"
        f"<span class='ebay-stat'>{sold} sold</span>"
        "</div></div>"
    )


def _stats(rows: list[EbayListing]) -> dict:
    total_value = Decimal("0")
    total_sold = 0
    total_revenue = Decimal("0")
    for row in rows:
        price = Decimal(row.current_price_minor) / 100
        sold = (row.raw_snapshot or {}).get("quantity_sold") or 0
        total_value += price * (row.quantity or 0)
        total_sold += sold
        total_revenue += price * sold
    return {"count": len(rows), "total_value": total_value, "total_sold": total_sold, "total_revenue": total_revenue}


def _stats_html(rows: list[EbayListing]) -> str:
    if not rows:
        return ""
    s = _stats(rows)
    return (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Active listings</div><div class='value'>{s['count']}</div></div>"
        f"<div class='stat-card'><div class='label'>Listed value</div><div class='value'>${s['total_value']:,.2f}</div></div>"
        f"<div class='stat-card'><div class='label'>Units sold</div><div class='value'>{s['total_sold']}</div></div>"
        f"<div class='stat-card'><div class='label'>Est. revenue</div><div class='value'>${s['total_revenue']:,.2f}"
        "<div class='value-sub'>at current listed price, not sale price</div></div></div>"
        "</div>"
    )


# A listing sitting this long with zero sales is worth a second look
# (reprice or relist) rather than left to quietly age forever.
STALE_AFTER_DAYS = 21
# The shortest word worth matching for the bundle-overlap check below --
# short enough excludes noisy generic suffixes (ex, V, GX) without a
# hand-maintained stopword list doing all the work alone.
_MIN_TOKEN_LEN = 5
_GENERIC_WORDS = {"mega", "shiny", "energy", "promo", "cards", "pokemon"}


def _listing_age_days(row: EbayListing) -> int | None:
    raw = (row.raw_snapshot or {}).get("start_time")
    if not raw:
        return None
    try:
        started = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return (datetime.now(UTC) - started).days


def _distinctive_word(name: str) -> str | None:
    """The one word in a card name most likely to identify it inside an
    eBay title -- e.g. "Kangaskhan" out of "Mega Kangaskhan ex". Picking
    the longest word rather than the first/last sidesteps needing to know
    which side of a name carries the species vs. a rarity suffix."""
    candidates = [w.strip("().,'\"") for w in name.split()]
    candidates = [w for w in candidates if len(w) >= _MIN_TOKEN_LEN and w.lower() not in _GENERIC_WORDS]
    return max(candidates, key=len) if candidates else None


def _owned_card_names(session: Session) -> list[str]:
    """Every catalog item with real stock on hand (quantity > 0). Joined on
    the id CatalogCard and CatalogItem share (confirmed against svp-196:
    same id, both tables) rather than InventoryItem.catalog_item_id, which
    isn't backfilled for cards committed by scan-ingest -- see
    InventoryItem's docstring."""
    rows = session.execute(
        select(CatalogItem.name).join(InventoryItem, InventoryItem.card_id == CatalogItem.id)
        .where(InventoryItem.quantity > 0).distinct()
    ).all()
    return [r[0] for r in rows]


def _bundle_overlaps(session: Session, rows: list[EbayListing]) -> list[tuple[EbayListing, str]]:
    """Listings whose title very likely names a card also sitting in local
    inventory -- e.g. a bundle listing built from cards that are (or look
    like) the same physical copies scan-ingest already has on hand. This is
    a title-text heuristic, not a SKU link, so it's a flag to go check by
    eye, not a guaranteed duplicate."""
    tokens: dict[str, str] = {}
    for name in _owned_card_names(session):
        word = _distinctive_word(name)
        if word:
            tokens.setdefault(word.lower(), name)
    flagged = []
    for row in rows:
        title_lower = row.title.lower()
        match = next((name for token, name in tokens.items() if re.search(rf"\b{re.escape(token)}\b", title_lower)), None)
        if match:
            flagged.append((row, match))
    return flagged


def _recommendations_html(session: Session, rows: list[EbayListing]) -> str:
    items = []
    for row, name in _bundle_overlaps(session, rows):
        items.append(
            f"<li class='rec warn'>{escape(row.title)}<br><span class='rec-detail'>looks like it may include "
            f"<b>{escape(name)}</b>, which is also recorded as in-stock inventory &mdash; "
            "worth checking it isn't the same physical card counted twice.</span></li>"
        )
    for row in rows:
        age = _listing_age_days(row)
        sold = (row.raw_snapshot or {}).get("quantity_sold") or 0
        if not sold and age is not None and age >= STALE_AFTER_DAYS:
            items.append(
                f"<li class='rec info'>{escape(row.title)}<br><span class='rec-detail'>"
                f"listed {age} days with no sales &mdash; consider a reprice or a relist.</span></li>"
            )
    if not items:
        return "<p class='muted'>Nothing flagged right now.</p>"
    return f"<ul class='rec-list'>{''.join(items)}</ul>"


@router.get("/ebay-listings", response_class=HTMLResponse)
def ebay_listings_page(request: Request, refreshed: str = "", error: str = "",
                       user: str = Depends(require_dashboard_user),
                       session: Session = Depends(get_session)) -> HTMLResponse:
    rows = _cached_rows(session)
    last_synced = max((r.observed_at for r in rows), default=None)

    note = ""
    if error:
        note = f"<p class='linkfill-note bad'>{escape(error)}</p>"
    elif refreshed:
        note = f"<p class='linkfill-note ok'>Refreshed from eBay just now.</p>"

    cards = (
        f"<div class='ebay-grid'>{''.join(_card_html(r) for r in rows)}</div>"
        if rows else "<p class='muted'>Nothing cached yet &mdash; click Refresh to check eBay.</p>"
    )

    freshness = (
        f"<p class='subtitle'>{len(rows)} active listing{'s' if len(rows) != 1 else ''} "
        f"&middot; last checked {last_synced:%Y-%m-%d %H:%M} UTC</p>"
        if last_synced else
        "<p class='subtitle'>Never checked. Nothing here is live until you click Refresh.</p>"
    )

    body = (
        brand_header("eBay listings")
        + "<p class='subtitle'>What eBay's own Seller Hub would show under Active -- read via "
          "GetMyeBaySelling, which sees a listing regardless of how it was created (Relist, bulk "
          "upload, or through this app). This is a cache: it only updates when you click Refresh.</p>"
        + note + freshness
        + _stats_html(rows)
        + (
            "<div class='panel'><h3>Recommendations</h3>" + _recommendations_html(session, rows) + "</div>"
            if rows else ""
        )
        + f"<div class='panel'>{cards}"
        + "<form method='post' action='/ebay-listings/refresh'><button>Refresh from eBay</button></form>"
        + "</div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — eBay listings", body))


@router.post("/ebay-listings/refresh")
async def ebay_listings_refresh(request: Request, user: str = Depends(require_dashboard_user),
                                session: Session = Depends(get_session)) -> RedirectResponse:
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayTradingReadAdapter(token, settings.ebay_client_id, environment=settings.ebay_env,
                                         marketplace_id=settings.ebay_marketplace_id)
        listings = await adapter.get_active_listings()
    except (OAuthError, EbayApiError) as exc:
        record_event(session, actor_type="user", actor_id=user, action="ebay.trading_listings.read",
                     resource_type="ebay_listing", resource_id=None, outcome="failed",
                     correlation_id=_correlation_id(), details={"error": str(exc)})
        from urllib.parse import quote
        return RedirectResponse(f"/ebay-listings?error={quote(str(exc))}", status_code=303)

    count = _upsert(session, listings)
    record_event(session, actor_type="user", actor_id=user, action="ebay.trading_listings.read",
                resource_type="ebay_listing", resource_id=None, outcome="success",
                correlation_id=_correlation_id(), details={"count": count})
    return RedirectResponse("/ebay-listings?refreshed=1", status_code=303)


_STYLE = """<style>
.ebay-grid{display:flex;flex-direction:column;gap:10px}
.ebay-card{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:12px;
  padding:12px 14px;min-width:0}
.ebay-card-title{font-size:14.5px;font-weight:600;line-height:1.35;word-break:break-word}
.ebay-card-title a{color:inherit;text-decoration:none}
.ebay-card-title a:hover{color:var(--accent)}
.ebay-card-id{font-size:11.5px;color:var(--text-dim);margin-top:3px}
.ebay-card-stats{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:13px;color:var(--text-dim)}
.ebay-stat b{color:var(--text);font-variant-numeric:tabular-nums}

.rec-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.rec-list li{border-radius:10px;padding:10px 12px;font-size:13.5px;line-height:1.4}
.rec-list li.warn{background:rgba(212,165,39,.10);border:1px solid rgba(212,165,39,.35)}
.rec-list li.info{background:var(--surface-sunken);border:1px solid var(--panel-border)}
.rec-detail{color:var(--text-dim);font-size:12.5px}

@media (max-width:560px){
  .ebay-card-stats{gap:10px}
}
</style>"""
