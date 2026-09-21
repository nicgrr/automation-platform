"""Ask eBay's own Sell Metadata API which condition IDs are actually valid
for the Bulk Master lot category (183455, CCG Mixed Card Lots) -- an
alternative to the "list one lot manually and see what sticks" approach
that's been blocking 14 of 24 lot listings since early this session
(bulk upload failed with "condition id 4000 invalid for category 183455",
and 4000/Ungraded is the only ID confirmed so far, for singles/183454).

    .venv/bin/python -m scripts.check_lot_condition_policy
"""

import asyncio

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import OAuthError, ebay_services, valid_user_access_token

LOT_CATEGORY_ID = "183455"


async def run() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        cipher, oauth = ebay_services(settings)
        try:
            token = await valid_user_access_token(session, cipher, oauth)
        except OAuthError as exc:
            print(f"error: {exc}")
            return 1

    base_url = "https://api.ebay.com" if settings.ebay_env == "production" else "https://api.sandbox.ebay.com"
    marketplace_id = settings.ebay_marketplace_id or "EBAY_AU"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{base_url}/sell/metadata/v1/marketplace/{marketplace_id}/item_condition_policies",
            params={"filter": f"categoryIds:{{{LOT_CATEGORY_ID}}}"},
            headers=headers,
        )
    print(f"status: {resp.status_code}")
    print(resp.text)
    return 0 if resp.status_code == 200 else 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
