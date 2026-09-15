"""Pull what's actually live on eBay right now and print it -- the same
call the "Refresh from eBay" button on /ebay-listings makes (Trading API's
GetMyeBaySelling, which sees a listing regardless of how it got there:
Relist, bulk File Exchange, or this app's own Inventory API calls). Also
upserts into the shared EbayListing table, so /ebay-listings reflects the
same fresh data after this runs.

    .venv/bin/python -m scripts.pull_ebay_listings
"""

import asyncio

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading import EbayTradingReadAdapter
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_listings import _upsert
from automation_control.ebay_oauth import OAuthError, ebay_services, valid_user_access_token


async def run() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        cipher, oauth = ebay_services(settings)
        try:
            token = await valid_user_access_token(session, cipher, oauth)
            adapter = EbayTradingReadAdapter(
                token, settings.ebay_client_id, environment=settings.ebay_env,
                marketplace_id=settings.ebay_marketplace_id,
            )
            listings = await adapter.get_active_listings()
        except (OAuthError, EbayApiError) as exc:
            print(f"error: {exc}")
            return 1

        _upsert(session, listings)

    print(f"{len(listings)} active listing(s) on eBay:\n")
    for item in sorted(listings, key=lambda l: l.title):
        price = f"{item.currency or 'AUD'} ${item.price:,.2f}" if item.price is not None else "?"
        print(f"  {item.title}  [{item.ebay_item_id}]  {price}  qty {item.quantity}  sold {item.quantity_sold}")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
