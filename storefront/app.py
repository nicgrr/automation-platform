"""NL Vault storefront -- the public shop.

Runs as its own process (see scripts/start-storefront.sh) against the same
database as the admin app, but with a deliberately narrow surface: it
reads catalog_items/shop_listings and writes only shop_orders. There is no
route here that can touch inventory, scanning, pricing feeds or eBay
credentials, by construction rather than by permission check.

The cart lives in a signed cookie rather than a table -- an abandoned cart
shouldn't leave rows behind, and there are no accounts to attach one to.
"""

import json
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from automation_control.database import get_session
from automation_control.models import (
    BulkLot, CardSet, CatalogItem, ShippingRate, ShippingZone, ShopListing, ShopListingStatus,
)

from . import departments
from .brand import logo_mark
from .common import (
    CART_COOKIE, MAX_PER_LINE, _art, _line_art, _money, cart_count, cart_lines,
    nav_links, read_cart, resolve_saleable, shell, shipping_for, write_cart,
)

router = APIRouter()

# --- browsing ---------------------------------------------------------------

def _active_listings(session: Session, query: str = "", limit: int = 60,
                     department: departments.Department | None = None):
    stmt = (
        select(ShopListing, CatalogItem)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id, isouter=True)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0)
    )
    if query:
        stmt = stmt.where(ShopListing.title.ilike(f"%{query}%"))
    if department is not None:
        # an inner-join condition expressed as a filter: a listing with no
        # catalogue row has no department and drops out, rather than being
        # silently filed under the wrong one
        stmt = stmt.where(departments.listing_filter(department))
    return session.execute(stmt.order_by(ShopListing.price.desc()).limit(limit)).all()


def _card(listing: ShopListing, item: CatalogItem | None) -> str:
    low_stock = "<span class='tag low'>Last one</span>" if listing.quantity == 1 else ""
    return (
        f"<a class='card' href='/item/{escape(listing.id)}'>"
        f"<div class='card-art'>{_art(item, listing)}</div>"
        "<div class='card-body'>"
        f"<div class='card-title'>{escape(listing.title)}</div>"
        f"<div class='card-sub'>{escape(listing.condition or 'Ungraded')}</div>"
        f"<div class='card-price'>{_money(listing.price)} {low_stock}</div>"
        "</div></a>"
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    stocked = departments.in_stock(session)
    intro = (
        "<div class='page-head'><h1>The vault is open</h1>"
        "<p>Hand-picked trading cards and collectibles, shipped from Australia.</p></div>"
    )

    if not stocked:
        return shell(session, "NL Vault", intro + (
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>Nothing listed yet</h2>"
            "<p class='muted'>Stock is added from the vault as it's catalogued. Check back shortly.</p>"
            "</div>"
        ), request=request)

    cards = []
    for department, count in stocked:
        span = departments.price_range(session, department)
        range_note = f"{_money(span[0])} – {_money(span[1])}" if span else ""
        cards.append(
            f"<a class='dept-card' href='/shop/{escape(department.slug)}'>"
            f"<h2>{escape(department.name)}</h2>"
            f"<p>{escape(department.blurb)}</p>"
            f"<div class='dept-meta'><span>{count} item(s)</span><span>{range_note}</span></div>"
            "</a>"
        )

    rows = _active_listings(session, limit=8)
    latest = (
        "<h2 class='section-head'>Fresh in the vault</h2>"
        f"<div class='grid'>{''.join(_card(l, i) for l, i in rows)}</div>"
    ) if rows else ""

    body = (
        intro
        + f"<div class='dept-grid'>{''.join(cards)}</div>"
        + latest
        + "<p style='margin-top:26px'><a class='btn ghost' href='/browse'>Browse everything</a></p>"
    )
    return shell(session, "NL Vault", body, request=request)


@router.get("/shop/{slug}", response_class=HTMLResponse)
def department_page(slug: str, request: Request, q: str = Query(""),
                    session: Session = Depends(get_session)) -> HTMLResponse:
    department = departments.find(slug)
    if department is None:
        raise HTTPException(status_code=404, detail="department not found")

    query = q.strip()
    rows = _active_listings(session, query, department=department)

    # Bulk lots live in their own table, so TCG gets a pointer to them
    # rather than trying to merge two differently-shaped products into one
    # grid -- a lot has no condition and no single catalogue item.
    bulk_note = ""
    if department.includes_bulk:
        lots = session.scalar(
            select(func.count()).select_from(BulkLot)
            .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
        ) or 0
        if lots:
            bulk_note = (
                "<div class='notice'>"
                f"<strong>{lots} bulk lot(s)</strong> of sorted commons are listed separately in "
                "<a href='/bulk'>Bulk Master</a> — the cheap way to fill a set.</div>"
            )

    if rows:
        content = f"<div class='grid'>{''.join(_card(l, i) for l, i in rows)}</div>"
        sub = f"{len(rows)} item(s)"
    else:
        content = (
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>Nothing here yet</h2>"
            f"<p class='muted'>{escape(department.blurb)}</p></div>"
        )
        sub = "Nothing listed yet"

    body = (
        f"<div class='page-head'><h1>{escape(department.name)}</h1>"
        f"<p>{escape(department.blurb)} {sub}.</p></div>"
        f"{bulk_note}{content}"
    )
    return shell(session, f"{department.name} — NL Vault", body, request=request, query=query)


@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = Query(""), dept: str = Query(""),
           session: Session = Depends(get_session)) -> HTMLResponse:
    query = q.strip()
    department = departments.find(dept)
    rows = _active_listings(session, query, department=department)
    scope = department.name if department else "the vault"
    heading = f"Results for “{escape(query)}”" if query else f"Everything in {scope}"
    if rows:
        content = f"<div class='grid'>{''.join(_card(l, i) for l, i in rows)}</div>"
        sub = f"{len(rows)} item(s)"
    else:
        content = (
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>Nothing matches</h2>"
            "<p class='muted'>Try a different search, or browse everything.</p>"
            "</div>"
        )
        sub = "No matches"
    body = f"<div class='page-head'><h1>{heading}</h1><p>{sub}</p></div>{content}"
    return shell(session, "Browse — NL Vault", body, request=request, query=query)


@router.get("/item/{listing_id}", response_class=HTMLResponse)
def item_detail(listing_id: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    listing = session.get(ShopListing, listing_id)
    if listing is None or listing.status != ShopListingStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="item not found")
    item = session.get(CatalogItem, listing.catalog_item_id)

    sold_out = listing.quantity <= 0
    buy = (
        "<button class='btn' disabled>Sold out</button>" if sold_out else
        f"<form method='post' action='/cart/add'>"
        f"<input type='hidden' name='listing_id' value='{escape(listing.id)}'>"
        "<button class='btn brass' type='submit'>Add to cart</button></form>"
    )
    description = f"<p class='muted' style='margin-top:14px'>{escape(listing.description)}</p>" if listing.description else ""

    body = (
        "<div class='detail'>"
        f"<div class='detail-art'>{_art(item, listing)}</div>"
        "<div>"
        f"<h1>{escape(listing.title)}</h1>"
        f"<div class='detail-price'>{_money(listing.price)} <span class='muted' style='font-size:15px'>{escape(listing.currency)}</span></div>"
        f"{description}"
        "<ul class='spec-list'>"
        f"<li><span>Condition</span><span>{escape(listing.condition or 'Ungraded')}</span></li>"
        f"<li><span>Available</span><span>{listing.quantity}</span></li>"
        "<li><span>Ships from</span><span>Australia</span></li>"
        "</ul>"
        f"<div style='margin-top:22px'>{buy}</div>"
        "<div class='notice'>Tracked postage Australia-wide. International shipping available at checkout.</div>"
        "</div></div>"
    )
    return shell(session, f"{listing.title} — NL Vault", body, request=request)


# --- cart -------------------------------------------------------------------

@router.post("/cart/add")
def cart_add(request: Request, listing_id: str = Form(...), session: Session = Depends(get_session)):
    saleable = resolve_saleable(session, listing_id)
    if saleable is None:
        raise HTTPException(status_code=404, detail="item not available")
    cart = read_cart(request)
    cart[listing_id] = min(cart.get(listing_id, 0) + 1, saleable.quantity, MAX_PER_LINE)
    response = RedirectResponse("/cart", status_code=303)
    write_cart(response, cart)
    return response


@router.post("/cart/remove")
def cart_remove(request: Request, listing_id: str = Form(...)):
    cart = read_cart(request)
    cart.pop(listing_id, None)
    response = RedirectResponse("/cart", status_code=303)
    write_cart(response, cart)
    return response


@router.get("/cart", response_class=HTMLResponse)
def cart_view(request: Request, zone: str = Query("au"), session: Session = Depends(get_session)) -> HTMLResponse:
    lines = cart_lines(session, read_cart(request))
    if not lines:
        body = (
            "<div class='page-head'><h1>Your cart</h1></div>"
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>Nothing in here yet</h2>"
            "<p class='muted'>Everything you add will show up here.</p>"
            "<p style='margin-top:18px'><a class='btn ghost' href='/browse'>Browse the vault</a></p>"
            "</div>"
        )
        return shell(session, "Cart — NL Vault", body, request=request)

    selected_zone = ShippingZone.INTERNATIONAL if zone == "international" else ShippingZone.AU
    items_total = sum((line["line_total"] for line in lines), Decimal("0"))
    ship_cost, ship_label = shipping_for(session, selected_zone, items_total)

    rows = "".join(
        "<div class='line'>"
        f"<div class='line-art'>{_line_art(line)}</div>"
        "<div class='line-main'>"
        f"<div class='line-title'>{escape(line['listing'].title)}</div>"
        f"<div class='muted'>{_money(line['listing'].price)} × {line['quantity']}</div>"
        "</div>"
        f"<div style='font-weight:600'>{_money(line['line_total'])}</div>"
        f"<form method='post' action='/cart/remove'>"
        f"<input type='hidden' name='listing_id' value='{escape(line['key'])}'>"
        "<button class='btn ghost' style='padding:7px 12px;font-size:13px'>Remove</button></form>"
        "</div>"
        for line in lines
    )

    au_selected = " selected" if selected_zone == ShippingZone.AU else ""
    intl_selected = " selected" if selected_zone == ShippingZone.INTERNATIONAL else ""

    body = (
        "<div class='page-head'><h1>Your cart</h1></div>"
        f"<div class='panel'>{rows}"
        "<form method='get' action='/cart' style='margin-top:18px;max-width:320px'>"
        "<label class='field'>Ship to"
        f"<select name='zone' onchange='this.form.submit()'>"
        f"<option value='au'{au_selected}>Australia</option>"
        f"<option value='international'{intl_selected}>International</option>"
        "</select></label></form>"
        "<div class='totals'>"
        f"<div><span>Items</span><span>{_money(items_total)}</span></div>"
        f"<div><span>{escape(ship_label)}</span><span>{_money(ship_cost)}</span></div>"
        f"<div class='grand'><span>Total</span><span>{_money(items_total + ship_cost)}</span></div>"
        "</div>"
        f"<div style='margin-top:20px'><a class='btn brass' href='/checkout?zone={selected_zone.value}'>Checkout</a></div>"
        "</div>"
    )
    return shell(session, "Cart — NL Vault", body, request=request)


# --- Bulk Master ------------------------------------------------------------
# Most of a scanned collection is commons worth under a dollar or two --
# unsellable one at a time once postage is $9.95, but a real product sorted
# by set and sold by the hundred. This is that section.

BULK_SORTS = {
    "cards_desc": ("Most cards", BulkLot.card_count.desc()),
    "price_asc": ("Cheapest first", BulkLot.price.asc()),
    "price_desc": ("Dearest first", BulkLot.price.desc()),
    "value_desc": ("Best value", BulkLot.market_value.desc()),
}


@router.get("/bulk", response_class=HTMLResponse)
def bulk_master(
    request: Request,
    q: str = Query(""),
    sort: str = Query("cards_desc"),
    min_cards: int = Query(0),
    max_price: float = Query(0),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    query = q.strip()
    sort_key = sort if sort in BULK_SORTS else "cards_desc"

    stmt = select(BulkLot).where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
    if query:
        stmt = stmt.where(BulkLot.title.ilike(f"%{query}%"))
    if min_cards > 0:
        stmt = stmt.where(BulkLot.card_count >= min_cards)
    if max_price > 0:
        stmt = stmt.where(BulkLot.price <= Decimal(str(max_price)))
    lots = session.scalars(stmt.order_by(BULK_SORTS[sort_key][1]).limit(120)).all()

    sort_options = "".join(
        f"<option value='{key}'{' selected' if key == sort_key else ''}>{escape(label)}</option>"
        for key, (label, _) in BULK_SORTS.items()
    )
    filters = (
        "<form class='panel filters' method='get' action='/bulk'>"
        f"<label class='field'>Search<input name='q' value='{escape(query)}' placeholder='Set name…'></label>"
        f"<label class='field'>Sort<select name='sort'>{sort_options}</select></label>"
        f"<label class='field'>Min cards<input name='min_cards' type='number' min='0' step='1' value='{min_cards or ''}'></label>"
        f"<label class='field'>Max price<input name='max_price' type='number' min='0' step='1' value='{max_price or ''}'></label>"
        "<button class='btn' type='submit'>Apply</button>"
        "<a class='btn ghost' href='/bulk'>Reset</a>"
        "</form>"
    )

    if lots:
        cards = "".join(
            f"<a class='card' href='/bulk/{escape(lot.id)}'>"
            "<div class='card-art bulk-art'>"
            + (f"<img src='{escape(lot.image_url)}' alt='' loading='lazy'>" if lot.image_url
               else f"<span class='bulk-count'>{lot.card_count}</span>")
            + "</div>"
            "<div class='card-body'>"
            f"<div class='card-title'>{escape(lot.title)}</div>"
            f"<div class='card-sub'>{lot.card_count} cards · market {_money(lot.market_value)}</div>"
            f"<div class='card-price'>{_money(lot.price)}</div>"
            "</div></a>"
            for lot in lots
        )
        content = f"<div class='grid'>{cards}</div>"
        count_note = f"{len(lots)} lot(s)"
    else:
        content = (
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>No lots match those filters</h2>"
            "<p class='muted'>Try widening the price or card count.</p></div>"
        )
        count_note = "No matches"

    body = (
        "<div class='page-head'><h1>Bulk Master</h1>"
        f"<p>Sorted, sleeved lots by set — the sensible way to buy commons. {count_note}.</p></div>"
        f"{filters}{content}"
    )
    return shell(session, "Bulk Master — NL Vault", body, request=request, query=query)


@router.get("/bulk/{lot_id}", response_class=HTMLResponse)
def bulk_detail(lot_id: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    lot = session.get(BulkLot, lot_id)
    if lot is None or lot.status != ShopListingStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="lot not found")

    saving = ""
    if lot.market_value and Decimal(str(lot.market_value)) > Decimal(str(lot.price)):
        pct = (Decimal("1") - Decimal(str(lot.price)) / Decimal(str(lot.market_value))) * 100
        saving = f"<span class='tag'>{pct:.0f}% under market</span>"

    art = (
        f"<img src='{escape(lot.image_url)}' alt='' style='max-height:220px;margin:0 auto'>"
        if lot.image_url else f"<div class='bulk-count big'>{lot.card_count}</div>"
    )
    body = (
        "<div class='detail'>"
        f"<div class='detail-art' style='display:grid;place-items:center;min-height:240px'>{art}</div>"
        "<div>"
        f"<h1>{escape(lot.title)}</h1>"
        f"<div class='detail-price'>{_money(lot.price)} {saving}</div>"
        f"<p class='muted'>{escape(lot.description or '')}</p>"
        "<ul class='spec-list'>"
        f"<li><span>Cards in lot</span><span>{lot.card_count}</span></li>"
        f"<li><span>Combined market value</span><span>{_money(lot.market_value)}</span></li>"
        "<li><span>Condition</span><span>Played to Near Mint, sorted by set</span></li>"
        "<li><span>Ships from</span><span>Australia</span></li>"
        "</ul>"
        "<form method='post' action='/cart/add' style='margin-top:22px'>"
        f"<input type='hidden' name='listing_id' value='B:{escape(lot.id)}'>"
        "<button class='btn brass' type='submit'>Add lot to cart</button></form>"
        "<div class='notice'>Bulk lots ship in a single tracked parcel — combine several and you still pay postage once.</div>"
        "</div></div>"
    )
    return shell(session, f"{lot.title} — NL Vault", body, request=request)


@router.get("/shipping", response_class=HTMLResponse)
def shipping_info(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    rates = session.scalars(select(ShippingRate).order_by(ShippingRate.zone)).all()
    rows = "".join(
        f"<li><span>{escape(rate.label)}</span><span>{_money(rate.price)}"
        + (f" — free over {_money(rate.free_over)}" if rate.free_over is not None else "")
        + "</span></li>"
        for rate in rates
    ) or "<li><span>Rates</span><span>Calculated at checkout</span></li>"
    body = (
        "<div class='page-head'><h1>Shipping &amp; returns</h1>"
        "<p>Everything ships from Australia, packed to survive the post.</p></div>"
        f"<div class='panel'><h2>Rates</h2><ul class='spec-list'>{rows}</ul>"
        "<div class='notice'>International orders are welcome — shipping is charged at the international "
        "rate above, and any customs duties are the buyer's responsibility.</div>"
        "<h2 style='margin-top:26px'>Returns</h2>"
        "<p class='muted'>If something arrives damaged or isn't as described, contact us within 7 days "
        "of delivery and we'll make it right.</p></div>"
    )
    return shell(session, "Shipping — NL Vault", body, request=request)


def create_app() -> FastAPI:
    app = FastAPI(title="NL Vault", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    from .checkout import router as checkout_router
    app.include_router(checkout_router)
    return app


app = create_app()
