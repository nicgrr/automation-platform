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
from .models import EbayListing
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


def _row_html(row: EbayListing) -> str:
    snapshot = row.raw_snapshot or {}
    price = Decimal(row.current_price_minor) / 100
    url = snapshot.get("view_item_url")
    title = f"<a href='{escape(url)}' target='_blank' rel='noopener'>{escape(row.title)}</a>" if url else escape(row.title)
    sold = snapshot.get("quantity_sold") or 0
    return (
        "<tr>"
        f"<td>{title}<div class='muted'>{escape(row.ebay_listing_id)}</div></td>"
        f"<td class='num'>{row.currency} ${price:,.2f}</td>"
        f"<td class='num'>{row.quantity if row.quantity is not None else '?'}</td>"
        f"<td class='num'>{sold}</td>"
        f"<td>{escape(snapshot.get('listing_type') or '')}</td>"
        "</tr>"
    )


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

    if rows:
        body_rows = "".join(_row_html(r) for r in rows)
        table = (
            "<div class='table-wrap'><table><thead><tr>"
            "<th>Item</th><th>Price</th><th>Qty left</th><th>Sold</th><th>Type</th>"
            "</tr></thead><tbody>" + body_rows + "</tbody></table></div>"
        )
    else:
        table = "<p class='muted'>Nothing cached yet &mdash; click Refresh to check eBay.</p>"

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
        + f"<div class='panel'>{table}"
        + "<form method='post' action='/ebay-listings/refresh'><button>Refresh from eBay</button></form>"
        + "</div>"
        + "<style>.table-wrap table{width:100%;border-collapse:collapse}"
          ".table-wrap th,.table-wrap td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--panel-border)}"
          ".table-wrap td.num,.table-wrap th.num{text-align:right;font-variant-numeric:tabular-nums}"
          ".table-wrap .muted{font-size:12px;color:var(--text-dim)}</style>"
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
