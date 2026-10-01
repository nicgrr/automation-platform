#!/usr/bin/env python
"""Scan allowed eBay item locations and update the Sonny Angel market watch.

The Browse API call uses eBay's official itemLocationCountry filter. Depop
and Facebook Marketplace do not provide a generally available search API for
this use case; they belong behind the same normalizer if approved access is
ever available. This command never scrapes them or stores a personal login.
"""

import argparse
import asyncio
from decimal import Decimal

import httpx

from automation_control.adapters.ebay import EbaySandboxReadAdapter, normalize_market
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import configured, ebay_services
from automation_control.market_watch_service import allowed_country_codes, send_alerts, upsert_listing


async def exchange_rates(currencies: set[str]) -> dict[str, Decimal]:
    rates = {"AUD": Decimal("1")}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for currency in currencies - {"AUD"}:
            response = await client.get("https://api.frankfurter.dev/v1/latest", params={"from": currency, "to": "AUD"})
            response.raise_for_status()
            rates[currency] = Decimal(str(response.json()["rates"]["AUD"]))
    return rates


async def run(limit: int, dry_run: bool, send: bool) -> int:
    settings = get_settings()
    if not configured(settings):
        raise SystemExit("eBay OAuth is not configured; add the existing developer credentials to .env.local")
    _, oauth = ebay_services(settings)
    token = (await oauth.application_token())["access_token"]
    adapter = EbaySandboxReadAdapter(token, environment=settings.ebay_env)
    found = []
    countries = allowed_country_codes(settings.market_allowed_countries)
    for country in countries:
        # Browse API has no supported EBAY_JP marketplace. EBAY_AU remains
        # the buyer-facing site while itemLocationCountry selects AU or JP
        # sellers precisely.
        payload = await adapter.search_market(
            "Sonny Angel", limit=limit, marketplace="EBAY_AU",
            item_location_country=country,
        )
        found.extend(normalize_market(payload))
    currencies = {row.get("currency") for row in found if row.get("currency")}
    rates = await exchange_rates(currencies) if settings.ebay_env == "production" else {c: Decimal("1") for c in currencies}
    created = updated = alerted = 0
    with SessionLocal() as session:
        for row in found:
            if not row.get("ebay_item_id") or not row.get("price") or not row.get("url"):
                continue
            if (row.get("marketplace") or "").upper() not in countries:
                # Defence in depth: the API filter is authoritative, but an
                # incomplete/malformed result must not leak another country
                # into a deliberately narrow watch.
                continue
            listing, is_new = upsert_listing(
                session, source="ebay", external_id=row["ebay_item_id"], title=row["title"] or "Untitled",
                url=row["url"], image_url=row.get("image_url"), seller=row.get("seller"),
                condition=row.get("condition"), country=row.get("marketplace"), price=row["price"],
                shipping=row.get("shipping", 0), currency=row["currency"], exchange_rate=rates[row["currency"]],
                # GST/customs vary by seller, platform collection, and shipment.
                # Default to zero rather than pretending an estimate is an invoice.
                tax_rate=Decimal("0"), raw=row.get("raw"),
            )
            created += int(is_new); updated += int(not is_new)
            threshold = settings.market_alert_max_landed_aud
            if send and is_new and (threshold is None or float(listing.landed_aud) <= threshold):
                alerted += len(await send_alerts(session, listing, settings))
        if not dry_run:
            session.commit()
        else:
            session.rollback()
    print(f"countries={','.join(countries)} found={len(found)} created={created} updated={updated} alerts={alerted} dry_run={dry_run}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-alerts", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(run(min(max(args.limit, 1), 200), args.dry_run, args.send_alerts))


if __name__ == "__main__":
    raise SystemExit(main())
