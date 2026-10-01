"""Generic AU/JP collectible discovery across approved search interfaces."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .adapters.ebay import EbaySandboxReadAdapter, normalize_market
from .ebay_oauth import configured, ebay_services
from .market_watch_service import (
    allowed_country_codes,
    associate_listing,
    exchange_rate_to_aud,
    MARKET_SOURCE_DOMAINS,
    MARKET_SOURCE_FIXED_COUNTRIES,
    market_source_for_url,
    send_alerts,
    upsert_listing,
)
from .models import MarketWatchTerm


PRICE_PATTERNS = (
    ("JPY", re.compile(r"(?:JPY\s*|¥\s*)(\d[\d,]*(?:\.\d{1,2})?)", re.I)),
    ("AUD", re.compile(r"(?:AUD\s*|AU\s*\$\s*|A\$\s*|\$\s*)(\d[\d,]*(?:\.\d{1,2})?)", re.I)),
)


@dataclass
class ScanResult:
    watches: int = 0
    found: int = 0
    created: int = 0
    updated: int = 0
    alerts: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def public_domains(configured_domains: str | None) -> tuple[str, ...]:
    parts = tuple(dict.fromkeys(
        part.strip().lower() for part in (configured_domains or "").split(",") if part.strip()
    ))
    return tuple(domain for domain in parts if "." in domain and "/" not in domain)


def _source_for_url(url: str, allowed_domains: tuple[str, ...]) -> tuple[str, str | None] | None:
    host = (urlparse(url).hostname or "").lower()
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        return None
    known_host = any(
        host == domain or host.endswith("." + domain)
        for domain in MARKET_SOURCE_DOMAINS.values()
    )
    return market_source_for_url(url) if known_host else ("public_web", None)


def _price(text: str) -> tuple[Decimal, str] | None:
    for currency, pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return Decimal(match.group(1).replace(",", "")), currency
    return None


async def _brave_results(settings, query: str, country: str, *, limit: int) -> list[dict]:
    domains = public_domains(settings.market_public_search_domains)
    if not domains:
        return []
    site_query = " OR ".join(f"site:{domain}" for domain in domains)
    params = {
        "q": f'"{query}" ({site_query})',
        "count": min(max(limit, 1), 20),
        "country": country.lower(),
        "search_lang": "ja" if country == "JP" else "en",
        "ui_lang": "ja-JP" if country == "JP" else "en-AU",
        "safesearch": "moderate",
    }
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": settings.brave_search_api_key},
            params=params,
        )
        response.raise_for_status()
        return (response.json().get("web") or {}).get("results") or []


async def live_public_search(settings, query: str, country: str, *, limit: int = 20) -> list[dict]:
    """Return allow-listed Brave results for immediate display without persistence.

    The standard prepaid Brave Search plan permits transient use but prohibits
    retaining search results.  Keep this path deliberately separate from the
    scheduled scanner and database upserts.
    """
    country = country.upper()
    allowed = set(allowed_country_codes(settings.market_allowed_countries))
    if country not in allowed:
        raise ValueError("Country is not enabled for market discovery")
    if not settings.brave_search_api_key:
        raise ValueError("Brave Search is not configured")

    domains = public_domains(settings.market_public_search_domains)
    rows = await _brave_results(settings, query.strip(), country, limit=limit)
    results: list[dict] = []
    for row in rows:
        url = row.get("url")
        if not url:
            continue
        source_info = _source_for_url(url, domains)
        if not source_info:
            # A live search may legitimately return a marketplace category or
            # search page. Domain-level identification is safe for display,
            # while the persistent scanner continues requiring listing URLs.
            host = (urlparse(url).hostname or "").lower()
            for candidate, domain in MARKET_SOURCE_DOMAINS.items():
                if host == domain or host.endswith("." + domain):
                    source_info = (candidate, MARKET_SOURCE_FIXED_COUNTRIES.get(candidate))
                    break
        if not source_info:
            continue
        source, fixed_country = source_info
        title = row.get("title") or "Public web result"
        description = row.get("description") or ""
        price_info = _price(f"{title} {description}")
        results.append({
            "source": source,
            "country": fixed_country or country,
            "title": title,
            "url": url,
            "description": description,
            "price": price_info[0] if price_info else None,
            "currency": price_info[1] if price_info else None,
        })
    return results


async def scan_market_watches(session, settings, *, watch_ids: list[str] | None = None,
                              limit: int = 20, send: bool = False) -> ScanResult:
    """Scan active watch terms and update normalized listings atomically.

    Public-web results are persisted only when the deployment explicitly
    confirms its Brave plan includes storage rights. eBay remains available
    independently and uses the exact item-location filter.
    """
    stmt = select(MarketWatchTerm).where(MarketWatchTerm.active.is_(True))
    if watch_ids:
        stmt = stmt.where(MarketWatchTerm.id.in_(watch_ids))
    watches = session.scalars(stmt.order_by(MarketWatchTerm.name)).all()
    result = ScanResult(watches=len(watches))
    if not watches:
        return result

    allowed = set(allowed_country_codes(settings.market_allowed_countries))
    ebay_adapter = None
    ebay_error = None
    if any("ebay" in (watch.sources or []) for watch in watches):
        if not configured(settings):
            ebay_error = "eBay application credentials are not configured"
        else:
            try:
                _, oauth = ebay_services(settings)
                token = (await oauth.application_token())["access_token"]
                ebay_adapter = EbaySandboxReadAdapter(token, environment=settings.ebay_env)
            except Exception as exc:  # normalized into a per-watch scan error below
                ebay_error = f"eBay authentication failed: {type(exc).__name__}"

    rates: dict[str, Decimal] = {"AUD": Decimal("1")}
    domains = public_domains(settings.market_public_search_domains)
    for watch in watches:
        watch_result = {"found": 0, "created": 0, "updated": 0, "errors": []}
        countries = [code for code in (watch.countries or []) if code in allowed]
        if not countries:
            countries = sorted(allowed)
        sources = set(watch.sources or ["ebay"])

        for country in countries:
            if "ebay" in sources:
                if ebay_error:
                    watch_result["errors"].append(ebay_error)
                elif ebay_adapter is not None:
                    try:
                        payload = await ebay_adapter.search_market(
                            watch.query,
                            limit=limit,
                            marketplace="EBAY_AU",
                            item_location_country=country,
                        )
                        rows = normalize_market(payload)
                    except Exception as exc:
                        watch_result["errors"].append(f"eBay {country}: {type(exc).__name__}")
                    else:
                        for row in rows:
                            if (row.get("marketplace") or "").upper() != country:
                                result.skipped += 1
                                continue
                            if not row.get("ebay_item_id") or not row.get("price") or not row.get("url"):
                                result.skipped += 1
                                continue
                            currency = (row.get("currency") or "AUD").upper()
                            if currency not in rates:
                                rates[currency] = await exchange_rate_to_aud(currency)
                            listing, created = upsert_listing(
                                session,
                                source="ebay",
                                external_id=row["ebay_item_id"],
                                title=row.get("title") or "Untitled listing",
                                url=row["url"],
                                image_url=row.get("image_url"),
                                seller=row.get("seller"),
                                condition=row.get("condition"),
                                country=country,
                                price=row["price"],
                                shipping=row.get("shipping", 0),
                                currency=currency,
                                exchange_rate=rates[currency],
                                raw={**(row.get("raw") or {}), "price_available": True, "location_confidence": "exact"},
                            )
                            associate_listing(session, listing, watch, matched_by="ebay_query")
                            result.found += 1
                            result.created += int(created)
                            result.updated += int(not created)
                            watch_result["found"] += 1
                            watch_result["created"] += int(created)
                            watch_result["updated"] += int(not created)
                            threshold = watch.max_landed_aud or settings.market_alert_max_landed_aud
                            if send and created and (threshold is None or listing.landed_aud <= Decimal(str(threshold))):
                                result.alerts += len(await send_alerts(session, listing, settings))

            if "public_web" in sources:
                if not settings.brave_search_api_key:
                    watch_result["errors"].append("Brave Search is not configured")
                elif not settings.brave_search_storage_permitted:
                    watch_result["errors"].append("Brave result storage is not enabled for this plan")
                else:
                    try:
                        web_rows = await _brave_results(settings, watch.query, country, limit=limit)
                    except Exception as exc:
                        watch_result["errors"].append(f"Public web {country}: {type(exc).__name__}")
                    else:
                        for row in web_rows:
                            url = row.get("url")
                            title = row.get("title") or "Public web result"
                            if not url:
                                result.skipped += 1
                                continue
                            source_info = _source_for_url(url, domains)
                            if not source_info:
                                result.skipped += 1
                                continue
                            source, fixed_country = source_info
                            listing_country = fixed_country or country
                            price_info = _price(f"{title} {row.get('description') or ''}")
                            amount = price_info[0] if price_info else Decimal("0")
                            currency = price_info[1] if price_info else ("JPY" if listing_country == "JP" else "AUD")
                            if currency not in rates:
                                rates[currency] = await exchange_rate_to_aud(currency)
                            listing, created = upsert_listing(
                                session,
                                source=source,
                                external_id=hashlib.sha256(url.encode()).hexdigest(),
                                title=title,
                                url=url,
                                country=listing_country,
                                price=amount,
                                shipping=Decimal("0"),
                                currency=currency,
                                exchange_rate=rates[currency],
                                raw={
                                    "entry_method": "brave_search",
                                    "description": row.get("description"),
                                    "price_available": price_info is not None,
                                    "location_confidence": "domain" if fixed_country else "search_region",
                                },
                            )
                            associate_listing(session, listing, watch, matched_by="public_web_query")
                            result.found += 1
                            result.created += int(created)
                            result.updated += int(not created)
                            watch_result["found"] += 1
                            watch_result["created"] += int(created)
                            watch_result["updated"] += int(not created)

        watch.last_scanned_at = datetime.now(UTC)
        watch.updated_at = datetime.now(UTC)
        # Keep errors stable and short; Settings explains missing credentials.
        watch_result["errors"] = list(dict.fromkeys(watch_result["errors"]))
        watch.last_scan_summary = watch_result
        result.errors.extend(f"{watch.name}: {error}" for error in watch_result["errors"])

    session.flush()
    return result
