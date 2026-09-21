"""Reads back the real ConditionID eBay saved for one already-live listing
-- the other half of "list a Bulk Master lot by hand through Seller Hub's
normal flow to find the real condition value for category 183455" (see
convert_to_ebay_template.py's --lot-condition-id docstring). Seller Hub's
own UI never shows you the numeric ID, only the human-readable name, so
this asks eBay directly via the Trading API's GetItem call instead of
guessing from what the UI displayed.

    .venv/bin/python -m scripts.check_listed_lot_condition <item_id>

<item_id> is the numeric eBay item number from the listing's URL
(ebay.com/itm/<item_id>) or from Seller Hub's Active listings page.
"""

import asyncio
import sys

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.adapters.ebay_trading import EbayTradingReadAdapter
from automation_control.ebay_oauth import OAuthError, ebay_services, valid_user_access_token


async def run(item_id: str) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        cipher, oauth = ebay_services(settings)
        try:
            token = await valid_user_access_token(session, cipher, oauth)
        except OAuthError as exc:
            print(f"error: {exc}")
            return 1

    adapter = EbayTradingReadAdapter(token, settings.ebay_client_id, environment=settings.ebay_env,
                                     marketplace_id=settings.ebay_marketplace_id)
    condition = await adapter.get_item_condition(item_id)
    print(f"category id:            {condition.category_id}")
    print(f"condition id:           {condition.condition_id}")
    print(f"condition display name: {condition.condition_display_name}")
    if condition.category_id != "183455":
        print("\nWARNING: this item is not in category 183455 (Mixed Card Lots) -- "
              "double check the listing actually landed in the right category before trusting this value.")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    return asyncio.run(run(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
