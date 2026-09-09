"""Website sale -> eBay sync (Phase 4 of the two-way stock sync -- see
.claude/plans/idempotent-churning-bumblebee.md). Pure risk-reduction: a
website sale ending or reducing the matching eBay listing can only ever
prevent an oversell, never cause one, so unlike ebay_publish.py's
listing-creation path this needs no Approval gate and runs fully
automatically.

The one hard requirement this module exists to satisfy: a slow or
failing eBay call must never affect the checkout flow that triggered it.
Every caller (storefront/checkout.py) schedules sync_ebay_after_sale as a
FastAPI BackgroundTask -- it runs after the HTTP response has already
been sent, opens its own DB session (the order's own commit has already
happened and returned by the time this runs), and swallows every
failure into EbayListingLink.last_error rather than raising. A failed
push is self-healed by scripts/import_ebay_orders.py's own reconciliation
sweep (Phase 5), not by retrying here.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from .adapters.ebay import EbayApiError, EbayNotConfigured
from .adapters.ebay_write import EbayWriteAdapter
from .config import get_settings
from .database import SessionLocal
from .ebay_oauth import OAuthError, configured, ebay_services, valid_user_access_token
from .models import EbayListingLink, EbayListingLinkStatus

logger = logging.getLogger(__name__)


async def sync_ebay_after_sale(shop_listing_id: str, new_quantity: int) -> None:
    settings = get_settings()
    if not configured(settings):
        return  # no eBay connection at all -- nothing to sync, not an error

    with SessionLocal() as session:
        link = session.scalar(
            select(EbayListingLink).where(
                EbayListingLink.shop_listing_id == shop_listing_id,
                EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
            )
        )
        if link is None:
            return  # not listed on eBay -- nothing to do, not an error

        try:
            cipher, oauth = ebay_services(settings)
            token = await valid_user_access_token(session, cipher, oauth)
            adapter = EbayWriteAdapter(token, environment=settings.ebay_env)
            if new_quantity == 0:
                await adapter.withdraw_offer(link.ebay_offer_id)
                link.status = EbayListingLinkStatus.ENDED
            else:
                await adapter.set_inventory_quantity(link.sku, new_quantity)
            link.last_pushed_quantity = new_quantity
            link.last_error = None
        except (EbayApiError, EbayNotConfigured, OAuthError) as exc:
            # The expected eBay-integration failure modes -- a 4xx/5xx
            # from eBay, a missing/expired token, a revoked connection.
            # Recorded, not raised: this must never propagate out of a
            # background task, and Phase 5's reconciliation sweep is what
            # retries it, not this function.
            logger.warning("eBay sync failed for shop_listing_id=%s: %s", shop_listing_id, exc)
            link.last_error = str(exc)
        finally:
            link.last_synced_at = datetime.now(UTC)
            session.commit()
