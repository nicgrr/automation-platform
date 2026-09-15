"""Daily refresh of the eBay active-listings cache, for systemd.

Calls the same `refresh_from_ebay` the "Refresh from eBay" button on
/ebay-listings calls -- one implementation, not a second copy of the
GetMyeBaySelling logic, so the cache can't drift depending on which one
last ran it.

Without this, the dashboard's eBay Listings widget and /settings' "Active
eBay listings" row are only ever as fresh as the last time a human
happened to click Refresh -- which, before this existed, was never,
because nothing prompted the click.

    .venv/bin/python -m scripts.refresh_ebay_listings
"""

import asyncio

from automation_control.adapters.ebay import EbayApiError
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_listings import refresh_from_ebay
from automation_control.ebay_oauth import OAuthError


async def _run() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        try:
            count = await refresh_from_ebay(session, settings, user="cron")
        except (OAuthError, EbayApiError) as exc:
            print(f"eBay listings refresh failed: {exc}")
            return 1
        session.commit()
    print(f"refreshed {count} active listing(s) from eBay")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
