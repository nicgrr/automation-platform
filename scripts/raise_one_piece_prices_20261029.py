"""One-off: raise every active One Piece single by $3 (requested 2026-10-29).

Covers the whole One Piece singles range, live on eBay or not: the
ShopListing price goes up $3, and for the ones live on eBay the live price
goes up $3 too. The One Piece
bulk lot is a separate eBay-only listing and is deliberately left alone.

The eBay side is a getOffer -> change pricingSummary only -> updateOffer
round trip, so title, description, photos and quantity stay exactly as
they are live (updateOffer is a full replace). Each listing gets an audit
event, and a listing that already has one is skipped, so a re-run after a
partial failure never adds $3 twice.

Dry-run is the default::

    set -a; . ./.env.local; set +a
    .venv/bin/python -m scripts.raise_one_piece_prices_20261029
    .venv/bin/python -m scripts.raise_one_piece_prices_20261029 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading_write import EbayTradingListingAdapter
from automation_control.adapters.ebay_write import EbayWriteAdapter
from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import ebay_services, valid_user_access_token
from automation_control.models import (
    AuditEvent,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)

RAISE = Decimal("3.00")
ACTION = "shop_listing.one_piece_raise_20261029"

# Fields updateOffer accepts; getOffer also returns read-only ones (offerId,
# sku, status, listing, ...) that must not be sent back.
UPDATE_OFFER_FIELDS = {
    "availableQuantity", "categoryId", "charity", "extendedProducerResponsibility",
    "hideBuyerDetails", "includeCatalogProductDetails", "listingDescription",
    "listingDuration", "listingPolicies", "listingStartDate", "lotSize",
    "merchantLocationKey", "pricingSummary", "quantityLimitPerBuyer", "regulatory",
    "secondaryCategoryId", "storeCategoryNames", "tax",
}


def _already_raised(session, listing_id: str) -> bool:
    return session.scalar(
        select(AuditEvent.id).where(AuditEvent.action == ACTION, AuditEvent.resource_id == listing_id)
    ) is not None


def _targets(session):
    rows = session.execute(
        select(ShopListing, EbayListingLink)
        .join(TcgCard, TcgCard.catalog_item_id == ShopListing.catalog_item_id)
        .join(
            EbayListingLink,
            (EbayListingLink.shop_listing_id == ShopListing.id)
            & (EbayListingLink.status == EbayListingLinkStatus.ACTIVE),
            isouter=True,
        )
        .where(TcgCard.game == "one_piece", ShopListing.status == ShopListingStatus.ACTIVE)
        .order_by(ShopListing.price.desc())
    ).all()
    return [(listing, link) for listing, link in rows if not _already_raised(session, listing.id)]


async def _raise_live_price(
    adapter: EbayWriteAdapter, trading: EbayTradingListingAdapter,
    link: EbayListingLink, live_price: Decimal | None, expected: Decimal,
) -> str:
    if live_price != expected:
        raise RuntimeError(f"live price {live_price} for {link.ebay_listing_id} differs from EzBay {expected}; not touching it")
    offer = await adapter.get_offer(link.ebay_offer_id)
    if (offer.get("listing") or {}).get("listingId") != link.ebay_listing_id:
        # The link's offer belongs to an earlier, since-replaced listing (Loki
        # OP17-119 Alt Art: the live item came from a CSV upload). Updating
        # that offer wouldn't touch the live item, so revise the item itself.
        await trading.revise_price(link.ebay_listing_id, price=expected + RAISE)
        return "trading"
    price = offer["pricingSummary"]["price"]
    # expected + RAISE: an earlier run's updateOffer saved the offer but
    # failed to push it to the live item (see the 25604 retry below).
    if Decimal(str(price["value"])) not in (expected, expected + RAISE):
        raise RuntimeError(f"offer price {price['value']} for {link.ebay_listing_id} differs from EzBay {expected}; not touching it")
    payload = {key: value for key, value in offer.items() if key in UPDATE_OFFER_FIELDS}
    payload["pricingSummary"]["price"] = {"value": str(expected + RAISE), "currency": price.get("currency", "AUD")}
    try:
        await adapter.update_offer(link.ebay_offer_id, payload)
    except EbayApiError as exc:
        # Same transient 25604 "Availability not found" ebay_publish.py
        # retries: eBay stores the offer, then fails to publish it to the
        # live item. A second identical updateOffer goes through.
        if "25604" not in str(exc):
            raise
        await asyncio.sleep(3)
        await adapter.update_offer(link.ebay_offer_id, payload)
    return "inventory"


async def _run(*, apply: bool) -> int:
    with SessionLocal() as session:
        targets = _targets(session)
        live = [(listing, link) for listing, link in targets if link and link.ebay_offer_id]
        print(f"{len(targets)} active One Piece singles to raise by ${RAISE} ({len(live)} live on eBay)")
        for listing, link in targets:
            where = f"eBay {link.ebay_listing_id}" if link and link.ebay_offer_id else "EzBay only"
            print(f"  ${listing.price} -> ${listing.price + RAISE}  [{where}]  {listing.title}")
        if not apply:
            print("dry-run: nothing written; pass --apply to raise prices")
            return 0

        settings = get_settings()
        cipher, oauth = ebay_services(settings)
        token = await valid_user_access_token(session, cipher, oauth)
        session.commit()
        adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
        trading = EbayTradingListingAdapter(
            token, settings.ebay_client_id,
            environment=settings.ebay_env, marketplace_id=settings.ebay_marketplace_id,
        )
        try:
            for listing, link in targets:
                old = listing.price
                details = {"old_price": str(old), "new_price": str(old + RAISE)}
                if link and link.ebay_offer_id:
                    remote = session.scalar(
                        select(EbayListing).where(EbayListing.ebay_listing_id == link.ebay_listing_id)
                    )
                    live_price = Decimal(remote.current_price_minor) / 100 if remote else None
                    details["ebay_api"] = await _raise_live_price(adapter, trading, link, live_price, old)
                    details["ebay_listing_id"] = link.ebay_listing_id
                    if remote:
                        remote.current_price_minor = int((old + RAISE) * 100)
                        remote.observed_at = datetime.now(UTC)
                listing.price = old + RAISE
                record_event(
                    session,
                    actor_type="user", actor_id="nic", action=ACTION,
                    resource_type="shop_listing", resource_id=listing.id, outcome="success",
                    correlation_id=str(uuid.uuid4()), details=details,
                )
                print(f"raised {listing.title}: ${old} -> ${listing.price}")
        finally:
            await adapter.aclose()
            await trading.client.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
