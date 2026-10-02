"""Rehearse the complete NL Vault order lifecycle without external services.

The drill creates a temporary database and uses signed fake Stripe webhooks. It
never reads production data, calls Stripe, sends eBay updates, or charges money.

    .venv/bin/python -m scripts.storefront_transaction_drill
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from automation_control.api import app as admin_app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session, make_engine
from automation_control.models import (
    CatalogItem, CatalogItemType, InventoryItem, InventoryStatus, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, ShopOrder, ShopOrderStatus,
)
from automation_control import shop_orders
from storefront import checkout
from storefront.app import app as storefront_app


WEBHOOK_SECRET = "whsec_local_transaction_drill"


def _signature(body: bytes) -> str:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _add_listing(session: Session, suffix: str) -> ShopListing:
    catalog = CatalogItem(id=f"drill-catalog-{suffix}", item_type=CatalogItemType.TCG_CARD,
                          name=f"Drill card {suffix}")
    inventory = InventoryItem(id=f"drill-inventory-{suffix}", catalog_item_id=catalog.id,
                              quantity=1, status=InventoryStatus.LISTED)
    listing = ShopListing(id=f"drill-listing-{suffix}", catalog_item_id=catalog.id,
                          inventory_item_id=inventory.id, title=f"Drill card {suffix}",
                          price=Decimal("20.00"), quantity=1, status=ShopListingStatus.ACTIVE)
    session.add_all([catalog, inventory, listing])
    session.commit()
    return listing


def _place_order(client: TestClient, session: Session, listing: ShopListing) -> ShopOrder:
    client.cookies.clear()
    client.post("/cart/add", data={"listing_id": listing.id}, follow_redirects=False)
    response = client.post("/checkout", data={
        "email": "drill@example.test", "name": "Transaction Drill",
        "address": "1 Test Street\nSydney NSW 2000", "country": "Australia", "zone": "au",
    }, follow_redirects=False)
    if response.status_code != 303:
        raise RuntimeError(f"checkout failed: {response.status_code} {response.text}")
    return session.query(ShopOrder).order_by(ShopOrder.created_at.desc()).first()


def _pay(client: TestClient, order: ShopOrder) -> None:
    body = json.dumps({
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": f"cs_drill_{order.id}", "payment_intent": f"pi_drill_{order.id}",
            "client_reference_id": order.reference, "payment_status": "paid",
        }},
    }).encode()
    response = client.post("/webhooks/stripe", content=body,
                           headers={"stripe-signature": _signature(body)})
    if response.status_code != 200:
        raise RuntimeError(f"webhook failed: {response.status_code} {response.text}")


def run() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="ezbay-storefront-drill.") as directory:
        engine = make_engine(f"sqlite+pysqlite:///{Path(directory) / 'drill.db'}")
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            session.add(ShippingRate(zone=ShippingZone.AU, label="Tracked",
                                     price=Decimal("9.95"), is_default=True))
            session.commit()

            storefront_app.dependency_overrides[get_session] = lambda: session
            admin_app.dependency_overrides[get_session] = lambda: session
            admin_app.dependency_overrides[require_dashboard_user] = lambda: "transaction-drill"
            original_secret = checkout.stripe_webhook_secret
            original_checkout_secret = checkout.stripe_secret
            original_checkout_sync = checkout.sync_ebay_after_sale
            original_admin_sync = shop_orders.sync_ebay_after_sale
            checkout.stripe_webhook_secret = lambda: WEBHOOK_SECRET
            checkout.stripe_secret = lambda: None
            checkout.sync_ebay_after_sale = lambda *_args, **_kwargs: None
            shop_orders.sync_ebay_after_sale = lambda *_args, **_kwargs: None
            try:
                shop_client = TestClient(storefront_app)
                admin_client = TestClient(admin_app)

                fulfilled_listing = _add_listing(session, "fulfilled")
                fulfilled = _place_order(shop_client, session, fulfilled_listing)
                assert fulfilled.stock_reserved_at is not None and fulfilled_listing.quantity == 0
                _pay(shop_client, fulfilled)
                session.refresh(fulfilled)
                assert fulfilled.status == ShopOrderStatus.PAID
                admin_client.post(f"/shop-orders/{fulfilled.id}/pack", follow_redirects=False)
                admin_client.post(f"/shop-orders/{fulfilled.id}/ship",
                                  data={"tracking_number": "DRILL123AU"}, follow_redirects=False)
                session.refresh(fulfilled)
                assert fulfilled.status == ShopOrderStatus.SHIPPED

                cancelled_listing = _add_listing(session, "cancelled")
                cancelled = _place_order(shop_client, session, cancelled_listing)
                admin_client.post(f"/shop-orders/{cancelled.id}/cancel", follow_redirects=False)
                session.refresh(cancelled)
                session.refresh(cancelled_listing)
                assert cancelled.status == ShopOrderStatus.CANCELLED
                assert cancelled_listing.quantity == 1

                refunded_listing = _add_listing(session, "refunded")
                refunded = _place_order(shop_client, session, refunded_listing)
                _pay(shop_client, refunded)
                admin_client.post(f"/shop-orders/{refunded.id}/refund", data={
                    "refund_confirmed": "true", "restock": "true", "reason": "Drill return",
                }, follow_redirects=False)
                session.refresh(refunded)
                session.refresh(refunded_listing)
                assert refunded.status == ShopOrderStatus.REFUNDED
                assert refunded_listing.quantity == 1

                return {
                    "status": "passed",
                    "checkout_reservation": "passed",
                    "signed_webhook_payment": "passed",
                    "fulfilment": "passed",
                    "cancellation_restore": "passed",
                    "refund_restock": "passed",
                    "external_calls": "none",
                }
            finally:
                checkout.stripe_webhook_secret = original_secret
                checkout.stripe_secret = original_checkout_secret
                checkout.sync_ebay_after_sale = original_checkout_sync
                shop_orders.sync_ebay_after_sale = original_admin_sync
                storefront_app.dependency_overrides.clear()
                admin_app.dependency_overrides.clear()
        engine.dispose()


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
