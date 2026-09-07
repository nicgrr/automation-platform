"""Shared storefront helpers.

Extracted from app.py so app.py and checkout.py can both use them without
importing each other -- checkout needs the cart and shipping maths, and
app.py registers checkout's routes, which is a cycle if the helpers live
in either one.
"""

import json
from decimal import Decimal
from html import escape

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.models import (
    BulkLot, CardSet, CatalogItem, ShippingRate, ShippingZone, ShopListing, ShopListingStatus,
)

from .ui import page


CART_COOKIE = "nlv_cart"
MAX_PER_LINE = 20


# --- cart plumbing ----------------------------------------------------------

def read_cart(request: Request) -> dict[str, int]:
    """{listing_id: quantity}. Malformed or tampered cookies are treated as
    an empty cart rather than an error -- a shopper should never see a
    stack trace because a cookie got mangled."""
    raw = request.cookies.get(CART_COOKIE)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cart: dict[str, int] = {}
    for key, value in data.items():
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if isinstance(key, str) and 0 < quantity <= MAX_PER_LINE:
            cart[key] = quantity
    return cart


def write_cart(response, cart: dict[str, int]) -> None:
    if cart:
        response.set_cookie(
            CART_COOKIE, json.dumps(cart), max_age=14 * 24 * 3600,
            httponly=True, samesite="lax",
        )
    else:
        response.delete_cookie(CART_COOKIE)


def cart_count(request: Request) -> int:
    return sum(read_cart(request).values())


def _money(value: Decimal | float | None) -> str:
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


def _art(item: CatalogItem | None, listing: ShopListing) -> str:
    url = listing.image_url or (item.image_url if item else None)
    if not url:
        return "<span class='noart'>no image</span>"
    return f"<img src='{escape(url)}' alt='{escape(listing.title)}' loading='lazy'>"


def _line_art(line: dict) -> str:
    if line["is_lot"]:
        lot = line["listing"]
        if lot.image_url:
            return f"<img src='{escape(lot.image_url)}' alt=''>"
        return f"<span class='bulk-count small'>{lot.card_count}</span>"
    return _art(line["item"], line["listing"])


def nav_links(session: Session) -> str:
    """Drawer categories built from what's actually in stock, so the shop
    never advertises a section that would land on an empty page."""
    sets = session.execute(
        select(CardSet.name, BulkLot.id, BulkLot.card_count)
        .join(BulkLot, BulkLot.set_id == CardSet.id)
        .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
        .order_by(BulkLot.card_count.desc()).limit(8)
    ).all()
    if not sets:
        return ""
    rows = "".join(
        f"<a href='/bulk/{escape(lot_id)}'>{escape(name)}<span class='n'>{count}</span></a>"
        for name, lot_id, count in sets
    )
    return f"<div class='navgroup'><h4>Bulk by set</h4>{rows}</div>"


def shell(session: Session, title: str, body: str, *, request: Request, query: str = "") -> HTMLResponse:
    return HTMLResponse(page(
        title, body, cart_count=cart_count(request), query=query, nav_links=nav_links(session),
    ))


def resolve_saleable(session: Session, key: str):
    """Cart keys carry their own type: a bare id is a single listing, a
    'B:' prefix is a bulk lot. One cart holds both, and an unknown or
    withdrawn key simply resolves to None so the line drops out quietly."""
    if key.startswith("B:"):
        lot = session.get(BulkLot, key[2:])
        if lot is None or lot.status != ShopListingStatus.ACTIVE or lot.quantity <= 0:
            return None
        return lot
    listing = session.get(ShopListing, key)
    if listing is None or listing.status != ShopListingStatus.ACTIVE or listing.quantity <= 0:
        return None
    return listing

def cart_lines(session: Session, cart: dict[str, int]):
    """Resolve a cookie cart against live listings, dropping anything that
    has since been withdrawn or sold, and clamping to what's actually in
    stock -- the cookie is a wish, the database is the truth."""
    lines = []
    for key, wanted in cart.items():
        saleable = resolve_saleable(session, key)
        if saleable is None:
            continue
        quantity = min(wanted, saleable.quantity)
        is_lot = isinstance(saleable, BulkLot)
        item = None if is_lot else session.get(CatalogItem, saleable.catalog_item_id)
        lines.append({
            "key": key, "listing": saleable, "item": item, "is_lot": is_lot,
            "quantity": quantity, "line_total": Decimal(str(saleable.price)) * quantity,
        })
    return lines

def shipping_for(session: Session, zone: ShippingZone, items_total: Decimal) -> tuple[Decimal, str]:
    rate = session.scalar(select(ShippingRate).where(ShippingRate.zone == zone).limit(1))
    if rate is None:
        return Decimal("0"), "Shipping calculated at checkout"
    if rate.free_over is not None and items_total >= Decimal(str(rate.free_over)):
        return Decimal("0"), f"{rate.label} — free over {_money(rate.free_over)}"
    return Decimal(str(rate.price)), rate.label
