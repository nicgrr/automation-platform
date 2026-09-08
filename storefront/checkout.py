"""Checkout for NL Vault -- Module 24.

Payment goes through Stripe Checkout (the hosted page), deliberately, so
no card details ever touch this server: the shopper is redirected to
Stripe, pays there, and comes back with a session id we reconcile. That
keeps PCI scope about as small as it can be for a self-hosted shop.

Same gating pattern as the eBay integration: everything here is built and
tested, but with no STRIPE_SECRET_KEY configured the checkout button
explains that payments aren't live yet rather than half-failing. An order
is still written first in either case, so nothing is lost.

Stock is decremented only once payment is confirmed, never at "add to
cart" -- an abandoned cart must not quietly remove things from sale.

Payment is confirmed two ways, and only Stripe's own word counts as
confirmation for either:

  * /checkout/complete -- the browser lands here on success, but a
    redirect landing is not proof of payment: hitting this URL with a
    known reference is trivial for anyone, and a customer who pays but
    closes the tab before the redirect never lands here at all. So this
    page asks Stripe directly whether the session actually paid
    (fetch_checkout_session) before marking anything -- the redirect is
    only ever a *hint* to go check.
  * /webhooks/stripe -- the authoritative path. Stripe calls this
    server-to-server once the dashboard is told this endpoint's public
    URL, independent of whether the browser ever comes back. Requests are
    verified by HMAC signature (verify_stripe_signature) rather than
    trusted because they arrived on the right path.

Both paths funnel through the same guarded "mark paid" logic, gated on
the order still being PENDING_PAYMENT, so whichever confirms first wins
and the other is a no-op rather than a double charge/decrement.
"""

import hashlib
import hmac
import json
import secrets
import string
import time
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from automation_control.config import get_settings
from automation_control.database import get_session
from automation_control.models import (
    BulkLot, ShippingZone, ShopListing, ShopListingStatus, ShopOrder, ShopOrderItem, ShopOrderStatus,
)

from . import discounts
from .common import CART_COOKIE, cart_lines, nav_links, read_cart, shipping_for
from .ui import page

router = APIRouter()

STRIPE_API = "https://api.stripe.com/v1/checkout/sessions"
STRIPE_COUPONS_API = "https://api.stripe.com/v1/coupons"
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits
WEBHOOK_TOLERANCE_SECONDS = 300  # Stripe's own default replay-protection window


def stripe_secret() -> str | None:
    """Read at call time rather than import time so adding the key to
    .env.local and restarting is all that's needed to go live."""
    return getattr(get_settings(), "stripe_secret_key", None) or None


def stripe_webhook_secret() -> str | None:
    return getattr(get_settings(), "stripe_webhook_secret", None) or None


def _is_stripe_session_paid(payload: dict) -> bool:
    """Stripe's own word for whether a Checkout Session actually paid.
    payment_status is "paid" for the normal card-payment case; it can sit
    at "unpaid" briefly for delayed methods (bank transfers), which this
    correctly refuses to treat as paid rather than guessing."""
    return (payload or {}).get("payment_status") == "paid"


async def fetch_checkout_session(secret: str, session_id: str) -> dict | None:
    """Ask Stripe directly what a Checkout Session's status actually is.
    Returns None on any network or API failure rather than raising --
    a Stripe hiccup here should read as "not confirmed yet", not crash
    the order-confirmation page a paying customer is looking at."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{STRIPE_API}/{session_id}", auth=(secret, ""))
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str,
                            tolerance: int = WEBHOOK_TOLERANCE_SECONDS) -> bool:
    """Stripe's documented webhook signature scheme: HMAC-SHA256 over
    "{timestamp}.{raw body}", compared to the v1 signature in the
    Stripe-Signature header. This is what makes a POST to /webhooks/stripe
    trustworthy -- arriving on the right path proves nothing on its own,
    since the path is public.

    The timestamp check is replay protection: without it, a captured
    request could be resent indefinitely and still verify.
    """
    if not sig_header or not secret:
        return False
    parts: dict[str, str] = {}
    for piece in sig_header.split(","):
        if "=" in piece:
            key, _, value = piece.partition("=")
            parts[key] = value
    timestamp, v1 = parts.get("t"), parts.get("v1")
    if not timestamp or not v1:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > tolerance:
        return False
    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def order_reference() -> str:
    return "NLV-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(8))


def _money(value) -> str:
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


def _field(name: str, label: str, *, required: bool = True, kind: str = "text", value: str = "") -> str:
    star = " required" if required else ""
    return (
        f"<label class='field'>{escape(label)}"
        f"<input name='{name}' type='{kind}' value='{escape(value)}'{star}></label>"
    )


@router.get("/checkout", response_class=HTMLResponse)
def checkout_form(
    request: Request, zone: str = Query("au"), session: Session = Depends(get_session),
) -> HTMLResponse:
    lines = cart_lines(session, read_cart(request))
    if not lines:
        return RedirectResponse("/cart", status_code=303)

    selected = ShippingZone.INTERNATIONAL if zone == "international" else ShippingZone.AU
    raw_items_total = sum((line["line_total"] for line in lines), Decimal("0"))
    groups, _near_misses = discounts.evaluate(session, lines)
    discount_total = discounts.total_discount(groups)
    items_total = raw_items_total - discount_total
    ship_cost, ship_label = shipping_for(session, selected, items_total)

    summary = "".join(
        f"<div><span>{escape(line['listing'].title)} × {line['quantity']}</span>"
        f"<span>{_money(line['line_total'])}</span></div>"
        for line in lines
    )
    for group in groups:
        summary += (
            f"<div><span>Set discount &mdash; {escape(group.label)} "
            f"({group.discount_pct:.0f}%)</span><span>&minus;{_money(group.discount_amount)}</span></div>"
        )

    live = stripe_secret() is not None
    pay_button = (
        "<button class='btn brass' type='submit'>Pay securely with Stripe</button>"
        if live else
        "<button class='btn' type='submit'>Place order (payment not live yet)</button>"
    )
    notice = "" if live else (
        "<div class='notice'>Card payments aren't switched on yet. You can still place the order — "
        "we'll email you to arrange payment.</div>"
    )

    body = (
        "<div class='page-head'><h1>Checkout</h1><p>Everything ships tracked from Australia.</p></div>"
        "<div class='detail'>"
        "<div class='panel'>"
        "<h2>Where's it going?</h2>"
        f"<form method='post' action='/checkout'>"
        f"<input type='hidden' name='zone' value='{selected.value}'>"
        + _field("email", "Email", kind="email")
        + _field("name", "Full name")
        + "<label class='field'>Address<textarea name='address' rows='3' required></textarea></label>"
        + _field("country", "Country", value="Australia")
        + notice
        + pay_button
        + "</form></div>"
        "<div class='panel'><h2>Your order</h2>"
        f"<div class='totals' style='border-top:none;margin-top:0'>{summary}</div>"
        "<div class='totals'>"
        f"<div><span>Items</span><span>{_money(raw_items_total)}</span></div>"
        + (f"<div class='discount-row'><span>Set discount</span><span>&minus;{_money(discount_total)}</span></div>"
           if discount_total > 0 else "")
        + f"<div><span>{escape(ship_label)}</span><span>{_money(ship_cost)}</span></div>"
        f"<div class='grand'><span>Total</span><span>{_money(items_total + ship_cost)}</span></div>"
        "</div></div>"
        "</div>"
    )
    site_url = str(request.base_url).rstrip("/")
    return HTMLResponse(page(
        "Checkout — NL Vault", body,
        cart_count=sum(line["quantity"] for line in lines), nav_links=nav_links(session),
        site_url=site_url, canonical=site_url + request.url.path, robots="noindex,follow",
    ))


async def _create_discount_coupon(secret: str, currency: str, amount: Decimal, client: httpx.AsyncClient) -> str | None:
    """A single one-time Stripe Coupon covering the whole order's set
    discount. Line items keep the listed price -- Stripe Checkout doesn't
    accept a negative unit_amount, and this way the hosted page shows the
    discount as its own line rather than baking it silently into a price."""
    if amount <= 0:
        return None
    response = await client.post(STRIPE_COUPONS_API, data={
        "amount_off": str(int((amount * 100).to_integral_value())),
        "currency": currency.lower(),
        "duration": "once",
        "name": "Bulk set discount",
    }, auth=(secret, ""))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="payment provider rejected the discount")
    return response.json().get("id")


async def create_stripe_session(
    order: ShopOrder, lines: list[dict], ship_cost: Decimal, base_url: str,
    discount_total: Decimal = Decimal("0"),
) -> str | None:
    """Hand the basket to Stripe and get back a URL to send the shopper to.
    Amounts are sent in cents, recomputed from the order we just wrote --
    never from anything the browser supplied."""
    secret = stripe_secret()
    if secret is None:
        return None

    data: list[tuple[str, str]] = [
        ("mode", "payment"),
        ("success_url", f"{base_url}/checkout/complete?ref={order.reference}"),
        ("cancel_url", f"{base_url}/cart"),
        ("client_reference_id", order.reference),
        ("customer_email", order.email),
    ]
    for index, line in enumerate(lines):
        cents = int((Decimal(str(line["listing"].price)) * 100).to_integral_value())
        data += [
            (f"line_items[{index}][price_data][currency]", order.currency.lower()),
            (f"line_items[{index}][price_data][unit_amount]", str(cents)),
            (f"line_items[{index}][price_data][product_data][name]", line["listing"].title[:250]),
            (f"line_items[{index}][quantity]", str(line["quantity"])),
        ]
    if ship_cost > 0:
        index = len(lines)
        data += [
            (f"line_items[{index}][price_data][currency]", order.currency.lower()),
            (f"line_items[{index}][price_data][unit_amount]", str(int((ship_cost * 100).to_integral_value()))),
            (f"line_items[{index}][price_data][product_data][name]", "Shipping"),
            (f"line_items[{index}][quantity]", "1"),
        ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        coupon_id = await _create_discount_coupon(secret, order.currency, discount_total, client)
        if coupon_id:
            data.append(("discounts[0][coupon]", coupon_id))
        response = await client.post(STRIPE_API, data=data, auth=(secret, ""))
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="payment provider rejected the request")
    payload = response.json()
    return payload.get("url")


@router.post("/checkout")
async def checkout_submit(
    request: Request,
    email: str = Form(...), name: str = Form(...), address: str = Form(...),
    country: str = Form("Australia"), zone: str = Form("au"),
    session: Session = Depends(get_session),
):
    lines = cart_lines(session, read_cart(request))
    if not lines:
        return RedirectResponse("/cart", status_code=303)

    selected = ShippingZone.INTERNATIONAL if zone == "international" else ShippingZone.AU
    raw_items_total = sum((line["line_total"] for line in lines), Decimal("0"))
    groups, _near_misses = discounts.evaluate(session, lines)
    discount_total = discounts.total_discount(groups)
    items_total = raw_items_total - discount_total
    ship_cost, _ = shipping_for(session, selected, items_total)

    order = ShopOrder(
        reference=order_reference(), email=email.strip(), ship_to_name=name.strip(),
        ship_to_address=address.strip(), ship_to_country=country.strip() or "Australia",
        zone=selected, items_total=items_total, shipping_total=ship_cost,
        grand_total=items_total + ship_cost, currency="AUD",
        status=ShopOrderStatus.PENDING_PAYMENT,
    )
    session.add(order)
    session.flush()
    for line in lines:
        session.add(ShopOrderItem(
            order_id=order.id,
            listing_id=None if line["is_lot"] else line["listing"].id,
            title=line["listing"].title,
            unit_price=Decimal(str(line["listing"].price)),
            quantity=line["quantity"],
        ))
    # Recorded as its own negative line rather than folded into the listed
    # prices above, so an order still shows exactly what was charged and
    # why -- "10% off 5 Base Set cards" reads directly off the order,
    # rather than requiring the discount to be reverse-engineered later.
    for group in groups:
        session.add(ShopOrderItem(
            order_id=order.id, listing_id=None,
            title=f"Set discount — {group.label} ({group.quantity} items, {group.discount_pct:.0f}% off)",
            unit_price=-group.discount_amount, quantity=1,
        ))
    session.commit()

    base_url = str(request.base_url).rstrip("/")
    pay_url = await create_stripe_session(order, lines, ship_cost, base_url, discount_total)
    if pay_url:
        order.stripe_session_id = pay_url.rsplit("/", 1)[-1]
        session.commit()
        return RedirectResponse(pay_url, status_code=303)

    # No payment provider configured: the order still exists, and the
    # shopper is told plainly rather than dropped on an error page.
    return RedirectResponse(f"/checkout/complete?ref={order.reference}", status_code=303)


def _mark_paid(session: Session, order: ShopOrder) -> None:
    """The one place an order becomes PAID. Guarded on the caller side by
    a PENDING_PAYMENT check, so calling this from both the redirect page
    and the webhook is safe -- whichever gets there first wins, and the
    second call's guard simply doesn't fire rather than double-decrementing
    stock or double-recording a payment time."""
    order.status = ShopOrderStatus.PAID
    order.paid_at = datetime.now(UTC)
    _decrement_stock(session, order)
    session.commit()


@router.get("/checkout/complete", response_class=HTMLResponse)
async def checkout_complete(
    request: Request, ref: str = Query(""), session: Session = Depends(get_session),
) -> HTMLResponse:
    order = session.query(ShopOrder).filter_by(reference=ref).one_or_none() if ref else None
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    # The redirect landing here is a hint, never proof -- proof only comes
    # from asking Stripe. Without this, POSTing straight to this URL with
    # a guessed or observed reference would mark an unpaid order paid.
    still_confirming = False
    secret = stripe_secret()
    if order.status == ShopOrderStatus.PENDING_PAYMENT and secret and order.stripe_session_id:
        payload = await fetch_checkout_session(secret, order.stripe_session_id)
        if payload and _is_stripe_session_paid(payload):
            _mark_paid(session, order)
        else:
            still_confirming = True

    paid = order.status == ShopOrderStatus.PAID
    if paid:
        headline = "Thank you — order confirmed"
        note = "<p class='muted'>We'll be in touch when it's on its way.</p>"
    elif still_confirming:
        headline = "Confirming your payment"
        note = (
            "<div class='notice'>Stripe hasn't confirmed this payment yet — that's usually just a few "
            "seconds' delay. Refresh this page shortly; Stripe's own receipt will also land in your inbox "
            "once it clears.</div>"
        )
    else:
        headline = "Order received"
        note = (
            "<div class='notice'>Card payments aren't live yet, so this order is reserved but unpaid — "
            "we'll be in touch to arrange payment.</div>"
        )

    site_url = str(request.base_url).rstrip("/")
    response = HTMLResponse(page(
        f"{headline} — NL Vault",
        "<div class='panel' style='max-width:560px;margin:0 auto;text-align:center'>"
        f"<h1>{headline}</h1>"
        f"<p class='muted'>Reference <strong>{escape(order.reference)}</strong></p>"
        f"<div class='detail-price'>{_money(order.grand_total)}</div>"
        f"{note}"
        "<p style='margin-top:20px'><a class='btn ghost' href='/'>Back to the vault</a></p>"
        "</div>",
        nav_links=nav_links(session),
        site_url=site_url, canonical=site_url + request.url.path, robots="noindex,follow",
    ))
    response.delete_cookie(CART_COOKIE)
    return response


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, session: Session = Depends(get_session)):
    """The authoritative payment-confirmation path -- Stripe calls this
    directly once its dashboard is told this endpoint's public URL, so a
    sale is recorded even if the buyer's browser never makes it back.

    Unconfigured (no STRIPE_WEBHOOK_SECRET yet) is a 503, not a silent
    no-op: better to see the endpoint plainly isn't live than have it look
    wired up while doing nothing.
    """
    secret = stripe_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="webhook is not configured")

    body = await request.body()
    if not verify_stripe_signature(body, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid payload")

    # Any event type not handled below is acknowledged, not rejected --
    # Stripe retries on anything but 2xx, and there's no reason to make it
    # retry an event this endpoint was never going to act on.
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        payload = (event.get("data") or {}).get("object") or {}
        reference = payload.get("client_reference_id")
        if reference and _is_stripe_session_paid(payload):
            order = session.query(ShopOrder).filter_by(reference=reference).one_or_none()
            if order is not None and order.status == ShopOrderStatus.PENDING_PAYMENT:
                _mark_paid(session, order)

    return {"received": True}


def _decrement_stock(session: Session, order: ShopOrder) -> None:
    """Take sold quantity off the listing, marking it sold out at zero.
    Bulk lots aren't matched back by id (an order line keeps only its
    title once the lot is gone), so they're handled by title match --
    imprecise, but a lot is a one-off product and this is only ever a
    convenience for the operator, not an accounting record."""
    for item in session.query(ShopOrderItem).filter_by(order_id=order.id).all():
        if item.unit_price < 0:
            continue  # a set-discount line, not a real stock line
        if item.listing_id:
            listing = session.get(ShopListing, item.listing_id)
            if listing is not None:
                listing.quantity = max(0, listing.quantity - item.quantity)
                if listing.quantity == 0:
                    listing.status = ShopListingStatus.SOLD_OUT
        else:
            lot = session.query(BulkLot).filter_by(title=item.title).one_or_none()
            if lot is not None:
                lot.quantity = max(0, lot.quantity - item.quantity)
                if lot.quantity == 0:
                    lot.status = ShopListingStatus.SOLD_OUT
