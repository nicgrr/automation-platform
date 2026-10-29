"""End one exact eBay fixed-price item after a matching confirmation.

Usage:
    .venv/bin/python -m scripts.end_ebay_listing 267796015368 \
        --confirm-item-id 267796015368
"""

import argparse
import asyncio

from automation_control.adapters.ebay_trading_write import EbayTradingListingAdapter
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import ebay_services, valid_user_access_token


async def _run(item_id: str, confirmation: str) -> int:
    if item_id != confirmation:
        raise SystemExit("confirmation must exactly match the eBay item id")
    if not item_id.isdigit():
        raise SystemExit("eBay item id must contain digits only")

    settings = get_settings()
    cipher, oauth = ebay_services(settings)
    with SessionLocal() as session:
        token = await valid_user_access_token(session, cipher, oauth)
        session.commit()  # persist a refreshed token before the external write

    adapter = EbayTradingListingAdapter(
        token,
        settings.ebay_client_id,
        environment=settings.ebay_env,
        marketplace_id=settings.ebay_marketplace_id,
    )
    try:
        await adapter.end_item(item_id, reason="NotAvailable")
    finally:
        await adapter.client.aclose()
    print(f"ended eBay item {item_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_id")
    parser.add_argument("--confirm-item-id", required=True)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.item_id.strip(), args.confirm_item_id.strip()))


if __name__ == "__main__":
    raise SystemExit(main())
