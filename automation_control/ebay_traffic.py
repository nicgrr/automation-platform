"""Read-only per-listing traffic from eBay's Sell Analytics API, for the
"eBay performance" panel on /analytics.

One traffic_report call per 200 active listings (the API's listing_ids
cap) over a trailing 30-day window. Only totals are stored: impressions,
views and transactions. Rates are worked out on the page from those totals
rather than taken from eBay's own CLICK_THROUGH_RATE, which counts search
clicks only and so doesn't line up with total views.

Nothing here writes to eBay. The scope is sell.analytics.readonly, which an
older connection doesn't have until the owner reconnects at
/auth/ebay/start -- refresh_traffic() says so instead of failing as 403.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError
from .audit import record_event
from .ebay_oauth import ANALYTICS_SCOPE, OAuthError, ebay_services, valid_user_access_token
from .models import EbayCredential, EbayListing, EbayListingTraffic

API_BASE = {"production": "https://api.ebay.com", "sandbox": "https://api.sandbox.ebay.com"}
METRICS = ("LISTING_IMPRESSION_TOTAL", "LISTING_VIEWS_TOTAL", "TRANSACTION")
WINDOW_DAYS = 30
BATCH_SIZE = 200
# eBay finalises a day's traffic a day or two after it ends; asking for
# yesterday can come back partial or rejected.
LATENCY_DAYS = 2


def report_window(today: date) -> tuple[date, date]:
    end = today - timedelta(days=LATENCY_DAYS)
    return end - timedelta(days=WINDOW_DAYS - 1), end


def parse_traffic_report(payload: dict) -> dict[str, dict[str, int]]:
    """{listing_id: {metric: value}} from a traffic_report response, whose
    metric values are positional against header.metrics."""
    keys = [metric.get("key") for metric in (payload.get("header") or {}).get("metrics") or []]
    out = {}
    for record in payload.get("records") or []:
        dimensions = record.get("dimensionValues") or []
        if not dimensions or dimensions[0].get("value") is None:
            continue
        values = {}
        for key, metric in zip(keys, record.get("metricValues") or []):
            raw = metric.get("value") if metric.get("applicable", True) else None
            values[key] = int(float(raw)) if raw not in (None, "") else 0
        out[str(dimensions[0]["value"])] = values
    return out


async def fetch_traffic(client: httpx.AsyncClient, token: str, listing_ids: list[str], start: date, end: date,
                        *, environment: str, marketplace_id: str) -> dict[str, dict[str, int]]:
    results = {}
    for offset in range(0, len(listing_ids), BATCH_SIZE):
        chunk = listing_ids[offset:offset + BATCH_SIZE]
        report_filter = (f"marketplace_ids:{{{marketplace_id}}},"
                         f"date_range:[{start:%Y%m%d}..{end:%Y%m%d}],"
                         f"listing_ids:{{{'|'.join(chunk)}}}")
        response = await client.get(
            f"{API_BASE[environment]}/sell/analytics/v1/traffic_report",
            params={"dimension": "LISTING", "metric": ",".join(METRICS), "filter": report_filter},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise EbayApiError(f"eBay traffic report failed ({response.status_code}): {response.text[:300]}")
        results.update(parse_traffic_report(response.json()))
    return results


async def refresh_traffic(session: Session, settings, user: str, *, today: date | None = None,
                          client: httpx.AsyncClient | None = None) -> int:
    """Replaces the stored window with fresh numbers for every active listing
    in the eBay listings cache. A listing eBay returns no row for gets
    zeros: it had no recorded traffic in the window."""
    credential = session.get(EbayCredential, 1)
    if credential is None or ANALYTICS_SCOPE not in (credential.scopes or ""):
        raise OAuthError("eBay analytics isn't allowed by the current connection -- reconnect at /auth/ebay/start")
    cipher, oauth = ebay_services(settings)
    token = await valid_user_access_token(session, cipher, oauth)
    listing_ids = list(session.scalars(
        select(EbayListing.ebay_listing_id).where(EbayListing.listing_status == "Active").order_by(EbayListing.ebay_listing_id)
    ))
    start, end = report_window(today or datetime.now(UTC).date())
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        traffic = await fetch_traffic(client, token, listing_ids, start, end,
                                      environment=settings.ebay_env, marketplace_id=settings.ebay_marketplace_id)
    finally:
        if owned:
            await client.aclose()

    fetched_at = datetime.now(UTC)
    session.execute(delete(EbayListingTraffic))
    for listing_id in listing_ids:
        values = traffic.get(listing_id, {})
        session.add(EbayListingTraffic(
            ebay_listing_id=listing_id, period_start=start, period_end=end,
            impressions=values.get("LISTING_IMPRESSION_TOTAL", 0),
            views=values.get("LISTING_VIEWS_TOTAL", 0),
            transactions=values.get("TRANSACTION", 0),
            fetched_at=fetched_at,
        ))
    record_event(session, actor_type="user", actor_id=user, action="ebay.traffic_report.read",
                 resource_type="ebay_listing", resource_id=None, outcome="success",
                 correlation_id=str(uuid.uuid4()),
                 details={"listings": len(listing_ids), "with_traffic": len(traffic),
                          "period_start": start.isoformat(), "period_end": end.isoformat()})
    return len(listing_ids)
