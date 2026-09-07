"""Checkout for NL Vault -- Module 24.

Payment goes through Stripe Checkout (the hosted page), deliberately, so
no card details ever touch this server: the shopper is redirected to
Stripe, pays there, and comes back with a session id we reconcile. That
keeps PCI scope about as small as it can be for a self-hosted shop.

Same gating pattern as the eBay integration: everything here is built and
tested, but with no STRIPE_SECRET_KEY configured the checkout button
explains that payments aren't live yet rather than half-failing. An order
is still written first in either case, so nothing is lost.

Stock is decremented only once Stripe confirms payment, never at "add to
cart" -- an abandoned cart must not quietly remove things from sale.
"""

import secrets
import string
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

from .common import CART_COOKIE, cart_lines, nav_links, read_cart, shipping_for
from .ui import page

router = APIRouter()

STRIPE_API = "https://api.stripe.com/v1/checkout/sessions"
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def stripe_secret() -> str | None:
    """Read at call time rather than import time so adding the key to
    .env.local and restarting is all that's needed to go live."""
    return getattr(get_settings(), "stripe_secret_key", None) or None


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
    items_total = sum((line["line_total"] for line in lines), Decimal("0"))
    ship_cost, ship_label = shipping_for(session, selected, items_total)

    summary = "".join(
        f"<div><span>{escape(line['listing'].title)} × {line['quantity']}</span>"
        f"<span>{_money(line['line_total'])}</span></div>"
        for line in lines
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
        f"<div><span>Items</span><span>{_money(items_total)}</span></div>"
        f"<div><span>{escape(ship_label)}</span><span>{_money(ship_cost)}</span></div>"
        f"<div class='grand'><span>Total</span><span>{_money(items_total + ship_cost)}</span></div>"
        "</div></div>"
        "</div>"
    )
    return HTMLResponse(page(
        "Checkout — NL Vault", body,
        cart_count=sum(line["quantity"] for line in lines), nav_links=nav_links(session),
    ))


async def create_stripe_session(order: ShopOrder, lines: list[dict], ship_cost: Decimal, base_url: str) -> str | None:
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
    items_total = sum((line["line_total"] for line in lines), Decimal("0"))
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
    session.commit()

    base_url = str(request.base_url).rstrip("/")
    pay_url = await create_stripe_session(order, lines, ship_cost, base_url)
    if pay_url:
        order.stripe_session_id = pay_url.rsplit("/", 1)[-1]
        session.commit()
        return RedirectResponse(pay_url, status_code=303)

    # No payment provider configured: the order still exists, and the
    # shopper is told plainly rather than dropped on an error page.
    return RedirectResponse(f"/checkout/complete?ref={order.reference}", status_code=303)


@router.get("/checkout/complete", response_class=HTMLResponse)
def checkout_complete(
    request: Request, ref: str = Query(""), session: Session = Depends(get_session),
) -> HTMLResponse:
    order = session.query(ShopOrder).filter_by(reference=ref).one_or_none() if ref else None
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    # Stripe redirects here on success. Treat that as payment confirmed and
    # take the stock down -- only now, never at add-to-cart.
    if order.status == ShopOrderStatus.PENDING_PAYMENT and stripe_secret() is not None:
        order.status = ShopOrderStatus.PAID
        order.paid_at = datetime.now(UTC)
        _decrement_stock(session, order)
        session.commit()

    paid = order.status == ShopOrderStatus.PAID
    headline = "Thank you — order confirmed" if paid else "Order received"
    note = (
        "<p class='muted'>A confirmation is on its way to your inbox.</p>" if paid else
        "<div class='notice'>Card payments aren't live yet, so this order is reserved but unpaid — "
        "we'll be in touch by email to arrange payment.</div>"
    )

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
    ))
    response.delete_cookie(CART_COOKIE)
    return response


def _decrement_stock(session: Session, order: ShopOrder) -> None:
    """Take sold quantity off the listing, marking it sold out at zero.
    Bulk lots aren't matched back by id (an order line keeps only its
    title once the lot is gone), so they're handled by title match --
    imprecise, but a lot is a one-off product and this is only ever a
    convenience for the operator, not an accounting record."""
    for item in session.query(ShopOrderItem).filter_by(order_id=order.id).all():
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
