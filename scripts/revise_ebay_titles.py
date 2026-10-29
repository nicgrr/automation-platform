"""Preview or apply richer titles to EzBay's active direct eBay listings.

The Inventory API's create-or-replace operation is a full replacement, so
this command always fetches the remote inventory item first and changes only
``product.title`` before writing it back. Bulk/File Exchange listings are
deliberately out of scope because they are not owned by an EbayListingLink
SKU and cannot be revised safely through this path.

    .venv/bin/python -m scripts.revise_ebay_titles
    .venv/bin/python -m scripts.revise_ebay_titles --apply
    .venv/bin/python -m scripts.revise_ebay_titles --apply --ebay-listing-id 123
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from automation_control.adapters.ebay import EbayApiError, EbayNotConfigured
from automation_control.adapters.ebay_write import EbayWriteAdapter
from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import OAuthError, ebay_services, valid_user_access_token
from automation_control.ebay_titles import build_single_card_ebay_title
from automation_control.models import (
    CardSet, CatalogItem, EbayListingLink, EbayListingLinkStatus, InventoryItem,
    ShopListing, TcgCard,
)


async def _run(*, apply: bool, ebay_listing_ids: set[str]) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        statement = select(EbayListingLink).where(
            EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
        ).order_by(EbayListingLink.ebay_listing_id)
        links = list(session.scalars(statement))
        if ebay_listing_ids:
            links = [link for link in links if link.ebay_listing_id in ebay_listing_ids]

        proposals: list[tuple[EbayListingLink, str]] = []
        for link in links:
            listing = session.get(ShopListing, link.shop_listing_id)
            if listing is None:
                print(f"skip {link.ebay_listing_id}: local ShopListing is missing")
                continue
            item = session.get(CatalogItem, listing.catalog_item_id)
            card = session.get(TcgCard, listing.catalog_item_id)
            card_set = session.get(CardSet, card.set_id) if card and card.set_id else None
            inventory = (
                session.get(InventoryItem, listing.inventory_item_id)
                if listing.inventory_item_id else None
            )
            if item is None or card is None or card_set is None:
                print(f"skip {link.ebay_listing_id}: structured card/set metadata is incomplete")
                continue
            title = build_single_card_ebay_title(item, card, card_set, inventory)
            proposals.append((link, title))
            print(f"{link.ebay_listing_id}: {listing.title!r} -> {title!r}")

        if not apply:
            print(f"\nwould revise {len(proposals)} title(s); nothing written (use --apply).")
            return 0
        if not proposals:
            print("no matching titles to revise")
            return 0

        adapter = None
        revised = failed = 0
        try:
            cipher, oauth = ebay_services(settings)
            token = await valid_user_access_token(session, cipher, oauth)
            adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
            for link, title in proposals:
                try:
                    remote = await adapter.get_inventory_item(link.sku)
                    old_title = str((remote.get("product") or {}).get("title") or "")
                    if old_title == title:
                        print(f"unchanged {link.ebay_listing_id}: already uses the detailed title")
                        continue
                    remote.setdefault("product", {})["title"] = title
                    await adapter.upsert_inventory_item(link.sku, remote)
                    link.last_synced_at = datetime.now(UTC)
                    link.last_error = None
                    session.commit()
                    record_event(
                        session,
                        actor_type="user",
                        actor_id="codex",
                        action="ebay.revise_listing_title",
                        resource_type="shop_listing",
                        resource_id=link.shop_listing_id,
                        outcome="success",
                        correlation_id=str(uuid.uuid4()),
                        details={
                            "ebay_listing_id": link.ebay_listing_id,
                            "old_title": old_title,
                            "new_title": title,
                        },
                    )
                    revised += 1
                    print(f"revised {link.ebay_listing_id}")
                except (EbayApiError, EbayNotConfigured, OAuthError) as exc:
                    link.last_error = str(exc)
                    link.last_synced_at = datetime.now(UTC)
                    session.commit()
                    failed += 1
                    print(f"failed {link.ebay_listing_id}: {exc}")
        finally:
            if adapter is not None:
                await adapter.aclose()

        print(f"\nrevised {revised}; failed {failed}.")
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the proposed titles to eBay")
    parser.add_argument(
        "--ebay-listing-id", action="append", default=[],
        help="limit to one eBay item ID (repeatable)",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(apply=args.apply, ebay_listing_ids=set(args.ebay_listing_id)))


if __name__ == "__main__":
    raise SystemExit(main())
