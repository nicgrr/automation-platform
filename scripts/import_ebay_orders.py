"""Poll eBay for orders and reflect a sale back onto the matching
ShopListing -- Phase 5 of the two-way storefront <-> eBay stock sync (see
.claude/plans/idempotent-churning-bumblebee.md).

Polling, not a webhook: this app has no public HTTPS surface today
(Tailscale-only), and eBay's own docs recommend pairing their
Notification API with polling rather than relying on it alone -- a
missed delivery here has no automatic backstop, and a missed eBay *sale*
oversells real stock, which is worse than a missed price update. Run
this every 5-10 minutes via a systemd timer (ebay-order-import.timer),
the same shape as scan-ingest's own sheet-check.timer.

Idempotency: eBay's getOrders returns a rolling window, not "new since
last call" cleanly, so every order line seen is upserted into
EbayImportedOrder keyed by ebay_order_id before anything is applied --
applied_at stays None until the matching ShopListing has actually been
decremented, so a crash between "seen" and "applied" gets retried
next run rather than silently skipped.

Also does a light reconciliation sweep each run: any ACTIVE
EbayListingLink whose last_pushed_quantity disagrees with its listing's
current quantity gets pushed again -- this is what self-heals a Phase 4
website-sale push that failed (see ebay_sync.py), rather than a separate
retry mechanism.

    .venv/bin/python -m scripts.import_ebay_orders --dry-run
    .venv/bin/python -m scripts.import_ebay_orders
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_write import EbayWriteAdapter
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_oauth import configured_for_writes, ebay_services, valid_user_access_token
from automation_control.models import BulkLot, EbayCredential, EbayImportedOrder, EbayListingLink, EbayListingLinkStatus, ShopListing
from automation_control.stock import decrement_listing_quantity

# How far back to look on the very first run (no last_order_sync_at yet).
# Generous on purpose -- missing an old order is worse than one extra,
# cheap query.
DEFAULT_LOOKBACK_DAYS = 3


def _format_since(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _line_items(order: dict) -> list[tuple[str, int]]:
    """(sku, quantity) for every real line item on an eBay order -- skips
    anything with no sku, which can't be matched back to a ShopListing."""
    items = []
    for line in order.get("lineItems", []):
        sku = line.get("sku")
        quantity = line.get("quantity")
        if sku and quantity:
            items.append((sku, int(quantity)))
    return items


async def _reconcile(session, adapter) -> tuple[int, int]:
    """Push a listing's real quantity to eBay again wherever the two have
    drifted -- self-heals a Phase 4 push that failed and was recorded in
    EbayListingLink.last_error rather than retried on the spot."""
    healed = failed = 0
    links = session.scalars(select(EbayListingLink).where(EbayListingLink.status == EbayListingLinkStatus.ACTIVE)).all()
    for link in links:
        listing = session.get(ShopListing, link.shop_listing_id)
        if listing is None or link.last_pushed_quantity == listing.quantity:
            continue
        try:
            if listing.quantity == 0:
                await adapter.withdraw_offer(link.ebay_offer_id)
                link.status = EbayListingLinkStatus.ENDED
            else:
                await adapter.set_inventory_quantity(link.sku, listing.quantity)
            link.last_pushed_quantity = listing.quantity
            link.last_error = None
            healed += 1
        except EbayApiError as exc:
            link.last_error = str(exc)
            failed += 1
        link.last_synced_at = datetime.now(UTC)
    return healed, failed


async def run(args) -> int:
    settings = get_settings()
    if not configured_for_writes(settings):
        raise SystemExit(
            "eBay is not configured for writes -- see ebay_oauth.configured_for_writes "
            "(OAuth connection + the ebay_*_policy_id / ebay_merchant_location_key settings)."
        )

    cipher, oauth = ebay_services(settings)
    applied = seen = failed = 0

    with SessionLocal() as session:
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayWriteAdapter(token, environment=settings.ebay_env)

        credential = session.get(EbayCredential, 1)
        since = credential.last_order_sync_at if credential and credential.last_order_sync_at else datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        print(f"polling eBay {settings.ebay_env} orders modified since {since.isoformat()}...\n")

        try:
            payload = await adapter.get_orders(since=_format_since(since))
        except EbayApiError as exc:
            raise SystemExit(f"could not fetch eBay orders: {exc}") from exc

        for order in payload.get("orders", []):
            ebay_order_id = order.get("orderId")
            if not ebay_order_id:
                continue
            for sku, quantity in _line_items(order):
                seen += 1
                existing = session.scalar(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id, EbayImportedOrder.sku == sku))
                if existing and existing.applied_at is not None:
                    continue  # already applied on a previous run -- the idempotency guarantee

                record = existing or EbayImportedOrder(
                    ebay_order_id=ebay_order_id, sku=sku, quantity=quantity,
                    marketplace_id=settings.ebay_marketplace_id, raw_snapshot=order,
                )
                if existing is None:
                    session.add(record)

                link = session.scalar(select(EbayListingLink).where(EbayListingLink.sku == sku))
                target = session.get(ShopListing, link.shop_listing_id) if link else None
                if target is None and link is None:
                    # A listing published through ebay_publish.py's API flow
                    # always has an EbayListingLink. One uploaded by hand via
                    # eBay's own Bulk Listing Tool (export_ebay_bulk_listing.py
                    # + convert_to_ebay_template.py) never does -- but its
                    # CustomLabel/SKU *is* the ShopListing or BulkLot id
                    # directly (see export_ebay_bulk_listing.py's `sku` column),
                    # so that id resolves the sale with no link needed at all.
                    target = session.get(ShopListing, sku) or session.get(BulkLot, sku)

                if target is None:
                    print(f"  no local listing for sku={sku} (order {ebay_order_id}) -- nothing to apply")
                    # Marked applied anyway: there's nothing this sku will
                    # ever resolve to without a code/data change, and
                    # leaving applied_at unset would re-log this same line
                    # on every future run forever.
                    if not args.dry_run:
                        record.applied_at = datetime.now(UTC)
                        session.commit()
                    continue

                print(f"  {'would apply' if args.dry_run else 'applying'}: order {ebay_order_id} sold {quantity}x sku={sku} ({target.title})")
                if args.dry_run:
                    continue

                if isinstance(target, ShopListing):
                    record.shop_listing_id = target.id
                new_quantity = decrement_listing_quantity(session, target, quantity)
                if link is not None and new_quantity is not None:
                    link.last_pushed_quantity = new_quantity
                    if new_quantity == 0:
                        link.status = EbayListingLinkStatus.ENDED
                record.applied_at = datetime.now(UTC)
                applied += 1
                session.commit()

        healed = recon_failed = 0
        if not args.dry_run:
            healed, recon_failed = await _reconcile(session, adapter)
            failed += recon_failed
            session.commit()

            # credential should always exist here -- valid_user_access_token
            # above only succeeds with a real connected account, and that
            # row is only ever created by ebay_oauth.store_user_tokens from
            # a completed OAuth exchange. Guarded rather than assumed: a
            # bare EbayCredential(id=1) would violate its own NOT NULL
            # token columns, and skipping the cursor update is a much
            # smaller problem than crashing the whole run over it.
            if credential is not None:
                credential.last_order_sync_at = datetime.now(UTC)
                session.commit()

    print(f"\n{seen} order line(s) seen, {applied} applied, {healed} listing(s) reconciled, {failed} failed.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
