"""Daily refresh of per-listing eBay traffic for /analytics, for systemd.

Read-only: calls eBay's Sell Analytics traffic report through
`automation_control.ebay_traffic.refresh_traffic`.

    .venv/bin/python -m scripts.refresh_ebay_traffic
"""

import asyncio

from automation_control.adapters.ebay import EbayApiError
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import OAuthError
from automation_control.ebay_traffic import refresh_traffic


async def _run() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        try:
            count = await refresh_traffic(session, settings, user="cron")
        except (OAuthError, EbayApiError) as exc:
            print(f"eBay traffic refresh failed: {exc}")
            return 1
        session.commit()
    print(f"refreshed traffic for {count} active listing(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
