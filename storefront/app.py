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
    BulkLot, CardSet, CatalogItem, CatalogItemType, SealedProduct, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, TcgCard,
)

from . import departments, discounts
from .assets import GAME_LOGOS
from .photos import router as photo_router
from .brand import logo_lockup, logo_mark
from .common import (
    CART_COOKIE, MAX_PER_LINE, _art, _line_art, _money, cart_count, cart_lines,
    nav_links, read_cart, resolve_saleable, shell, shipping_for, social_icons_html, write_cart,
)

router = APIRouter()

# --- browsing ---------------------------------------------------------------

def _active_listings(session: Session, query: str = "", limit: int = 60,
                     department: departments.Department | None = None, segment: str | None = None):
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
        if segment:
            extra = departments.segment_filter(department, segment)
            if extra is not None:
                stmt = stmt.where(extra)
    return session.execute(stmt.order_by(ShopListing.price.desc()).limit(limit)).all()


def _set_name(session: Session, item: CatalogItem | None) -> str | None:
    """The set a card or sealed product belongs to, for display -- never
    baked into the stored title, which stays what it was at listing time
    so an old order still reads correctly if a set gets renamed."""
    if item is None:
        return None
    set_id = None
    if item.item_type in (CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD):
        tcg = session.get(TcgCard, item.id)
        set_id = tcg.set_id if tcg else None
    elif item.item_type == CatalogItemType.SEALED_PRODUCT:
        sealed = session.get(SealedProduct, item.id)
        set_id = sealed.set_id if sealed else None
    if not set_id:
        return None
    card_set = session.get(CardSet, set_id)
    return card_set.name if card_set else None


def _card(listing: ShopListing, item: CatalogItem | None, session: Session | None = None) -> str:
    low_stock = "<span class='tag low'>Last one</span>" if listing.quantity == 1 else ""
    set_name = _set_name(session, item) if session is not None else None
    sub_line = f"{set_name} · {listing.condition or 'Ungraded'}" if set_name else (listing.condition or "Ungraded")
    return (
        f"<a class='card' href='/item/{escape(listing.id)}'>"
        f"<div class='card-art'>{_art(item, listing, session)}</div>"
        "<div class='card-body'>"
        f"<div class='card-title'>{escape(listing.title)}</div>"
        f"<div class='card-sub'>{escape(sub_line)}</div>"
        f"<div class='card-price'>{_money(listing.price)} {low_stock}</div>"
        "</div></a>"
    )


def _dept_hero(session: Session, department: departments.Department) -> str:
    """A representative image for a department tile.

    A game department leads with its real official game logo (see
    assets.GAME_LOGOS) rather than any specific card -- that's brand
    identification a shopper recognises the way a single card doesn't,
    and it never depends on what happens to be catalogued or in stock. A
    game with no logo asset yet falls back to a card/lot photo instead of
    a set-specific logo, which would read as one set's branding rather
    than the game's.

    Never carries the own-photo/stock-image tag that a real product tile
    does -- this image isn't standing in for a specific physical item the
    buyer would receive, so that authenticity claim has nothing to attach
    to and would just be confusing here.
    """
    if department.game:
        logo = GAME_LOGOS.get(department.game)
        if logo:
            return f"<img src='{logo}' alt='{escape(department.name)}' loading='lazy'>"

    row = session.execute(
        select(ShopListing, CatalogItem)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id, isouter=True)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
              departments.listing_filter(department))
        .order_by(ShopListing.price.desc()).limit(1)
    ).first()
    if row:
        listing, item = row
        return _art(item, listing, session, show_tag=False)

    if department.includes_bulk:
        stmt = select(BulkLot).where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
        if department.game:
            stmt = stmt.where(BulkLot.game == department.game)
        lot = session.scalar(stmt.order_by(BulkLot.price.desc()).limit(1))
        if lot and lot.image_url:
            return f"<img src='{escape(lot.image_url)}' alt='{escape(department.name)}'>"

    return f"<div class='dept-hero-mark'>{logo_mark(48)}</div>"


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    stocked = departments.in_stock(session)
    hero = (
        "<div class='hero-banner'>"
        "<div class='hero-inner'>"
        f"<h1 class='hero-brand'>{logo_lockup(72)}</h1>"
        "<p>Hand-picked trading cards and collectibles, shipped from Australia.</p>"
        f"{social_icons_html(session)}"
        "</div></div>"
    )

    if not stocked:
        body = (
            "<div class='panel empty'>"
            f"<div class='mark'>{logo_mark(64)}</div>"
            "<h2>Nothing listed yet</h2>"
            "<p class='muted'>Stock is added from the vault as it's catalogued. Check back shortly.</p>"
            "</div>"
        )
        return shell(session, "NL Vault", body, request=request, hero=hero)

    cards = []
    for department, _count in stocked:
        cards.append(
            f"<a class='dept-card' href='/shop/{escape(department.slug)}'>"
            f"<div class='dept-hero'>{_dept_hero(session, department)}</div>"
            "<div class='dept-card-body'>"
            f"<h2>{escape(department.name)}</h2>"
            f"<p>{escape(department.blurb)}</p>"
            "</div></a>"
        )

    rows = _active_listings(session, limit=8)
    latest = (
        "<h2 class='section-head'>Fresh in the vault</h2>"
        f"<div class='grid'>{''.join(_card(l, i, session) for l, i in rows)}</div>"
    ) if rows else ""

    body = (
        f"<div class='dept-grid'>{''.join(cards)}</div>"
        + latest
        + "<p style='margin-top:26px'><a class='btn ghost' href='/browse'>Browse everything</a></p>"
    )
    return shell(session, "NL Vault", body, request=request, hero=hero)


@router.get("/shop/{slug}", response_class=HTMLResponse)
def department_page(slug: str, request: Request, q: str = Query(""), seg: str = Query("all"),
                    session: Session = Depends(get_session)) -> HTMLResponse:
    department = departments.find(session, slug)
    if department is None:
        raise HTTPException(status_code=404, detail="department not found")

    query = q.strip()
    segment = seg if seg in departments.SEGMENTS else "all"
    rows = _active_listings(session, query, department=department, segment=segment)

    # Segment pills only for a game department -- "Premium"/"Sealed
    # product" are trading-card concepts with no meaning for Collectibles
    # or Apparel, and a pill nobody can act on is worse than no pill.
    segment_bar = ""
    if department.game:
        available = {"all", "singles"}
        if departments.has_premium_stock(session, department):
            available.add("premium")
        if departments.has_sealed_stock(session, department):
            available.add("sealed")
        pill_items = []
        for key, label in departments.SEGMENTS.items():
            if key not in available:
                continue
            active_class = " active" if key == segment else ""
            pill_items.append(
                f"<a class='seg-tab{active_class}' href='/shop/{escape(slug)}?seg={key}'>{escape(label)}</a>"
            )
        pills = "".join(pill_items)
        segment_bar = f"<div class='seg-tabs'>{pills}</div>"

    # Bulk lots live in their own table, so a game department gets a
    # pointer to them (scoped to its own game) rather than trying to
    # merge two differently-shaped products into one grid -- a lot has no
    # condition and no single catalogue item.
    bulk_note = ""
    if department.includes_bulk:
        lots = session.scalar(
            select(func.count()).select_from(BulkLot)
            .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0,
                  BulkLot.game == department.game)
        ) or 0
        if lots:
            bulk_note = (
                "<div class='notice'>"
                f"<strong>{lots} bulk lot(s)</strong> of sorted commons are listed separately in "
                f"<a href='/bulk?game={escape(department.game)}'>Bulk Master</a> — the cheap way to fill a set.</div>"
            )

    if rows:
        content = f"<div class='grid'>{''.join(_card(l, i, session) for l, i in rows)}</div>"
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
        f"{segment_bar}{bulk_note}{content}"
    )
    crumbs = (("Home", "/"), (department.name, ""))
    return shell(session, f"{department.name} — NL Vault", body, request=request, query=query, crumbs=crumbs)


@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, q: str = Query(""), dept: str = Query(""),
           session: Session = Depends(get_session)) -> HTMLResponse:
    query = q.strip()
    department = departments.find(session, dept)
    rows = _active_listings(session, query, department=department)
    scope = department.name if department else "the vault"
    heading = f"Results for “{escape(query)}”" if query else f"Everything in {scope}"
    if rows:
        content = f"<div class='grid'>{''.join(_card(l, i, session) for l, i in rows)}</div>"
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
    crumbs = (("Home", "/"), ("Browse", ""))
    return shell(session, "Browse — NL Vault", body, request=request, query=query, crumbs=crumbs)


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
    set_name = _set_name(session, item)
    set_row = f"<li><span>Set</span><span>{escape(set_name)}</span></li>" if set_name else ""

    body = (
        "<div class='detail'>"
        f"<div class='detail-art'>{_art(item, listing, session)}</div>"
        "<div>"
        f"<h1>{escape(listing.title)}</h1>"
        f"<div class='detail-price'>{_money(listing.price)} <span class='muted' style='font-size:15px'>{escape(listing.currency)}</span></div>"
        f"{description}"
        "<ul class='spec-list'>"
        f"{set_row}"
        f"<li><span>Condition</span><span>{escape(listing.condition or 'Ungraded')}</span></li>"
        f"<li><span>Available</span><span>{listing.quantity}</span></li>"
        "<li><span>Ships from</span><span>Australia</span></li>"
        "</ul>"
        f"<div style='margin-top:22px'>{buy}</div>"
        "<div class='notice'>Tracked postage Australia-wide. International shipping available at checkout.</div>"
        "</div></div>"
    )
    description = (
        f"{listing.title} — {listing.condition or 'ungraded'}, {_money(listing.price)}. "
        "Shipped tracked from Australia."
    )

    department = departments.department_for_item(session, item)
    crumbs = [("Home", "/")]
    if department:
        crumbs.append((department.name, f"/shop/{department.slug}"))
    crumbs.append((listing.title, ""))

    site_url = str(request.base_url).rstrip("/")
    from .photos import has_own_photo
    if has_own_photo(session, listing.id):
        image_url = f"{site_url}/photo/{listing.id}"
    else:
        image_url = listing.image_url or (item.image_url if item else None)
    product_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": listing.title,
        "description": listing.description or description,
        "sku": listing.id,
        "offers": {
            "@type": "Offer",
            "url": site_url + request.url.path,
            "priceCurrency": listing.currency,
            "price": str(listing.price),
            "availability": (
                "https://schema.org/InStock" if listing.quantity > 0 else "https://schema.org/OutOfStock"
            ),
        },
    }
    if image_url:
        product_ld["image"] = [image_url]

    return shell(session, f"{listing.title} — NL Vault", body, request=request, description=description,
                crumbs=tuple(crumbs), og_image=image_url or "", extra_jsonld=(product_ld,))


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
        return shell(session, "Cart — NL Vault", body, request=request, robots="noindex,follow")

    selected_zone = ShippingZone.INTERNATIONAL if zone == "international" else ShippingZone.AU
    raw_items_total = sum((line["line_total"] for line in lines), Decimal("0"))
    groups, near_misses = discounts.evaluate(session, lines)
    discount_total = discounts.total_discount(groups)
    items_total = raw_items_total - discount_total
    ship_cost, ship_label = shipping_for(session, selected_zone, items_total)

    rows = "".join(
        "<div class='line'>"
        f"<div class='line-art'>{_line_art(line, session)}</div>"
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

    discount_block = ""
    if groups or near_misses:
        earned = "".join(
            f"<li><span>{escape(g.label)} &mdash; {g.quantity} cards, {g.discount_pct:.0f}% off</span>"
            f"<span>&minus;{_money(g.discount_amount)}</span></li>"
            for g in groups
        )
        nudges = "".join(
            f"<li class='nudge'><span>Add {n.needed} more from {escape(n.label)} for {n.next_pct:.0f}% off</span></li>"
            for n in near_misses
        )
        discount_block = (
            "<div class='panel discountpanel'><h2>Set discounts</h2>"
            f"<ul class='discountlist'>{earned}{nudges}</ul></div>"
        )

    au_selected = " selected" if selected_zone == ShippingZone.AU else ""
    intl_selected = " selected" if selected_zone == ShippingZone.INTERNATIONAL else ""

    discount_line = (
        f"<div class='discount-row'><span>Set discount</span><span>&minus;{_money(discount_total)}</span></div>"
        if discount_total > 0 else ""
    )

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
        f"<div><span>Items</span><span>{_money(raw_items_total)}</span></div>"
        f"{discount_line}"
        f"<div><span>{escape(ship_label)}</span><span>{_money(ship_cost)}</span></div>"
        f"<div class='grand'><span>Total</span><span>{_money(items_total + ship_cost)}</span></div>"
        "</div>"
        f"<div style='margin-top:20px'><a class='btn brass' href='/checkout?zone={selected_zone.value}'>Checkout</a></div>"
        "</div>"
        + discount_block
    )
    return shell(session, "Cart — NL Vault", body, request=request, robots="noindex,follow")


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
    game: str = Query(""),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    query = q.strip()
    sort_key = sort if sort in BULK_SORTS else "cards_desc"
    game_filter = game.strip().lower()

    stmt = select(BulkLot).where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
    if query:
        stmt = stmt.where(BulkLot.title.ilike(f"%{query}%"))
    if min_cards > 0:
        stmt = stmt.where(BulkLot.card_count >= min_cards)
    if max_price > 0:
        stmt = stmt.where(BulkLot.price <= Decimal(str(max_price)))
    if game_filter:
        # from a game department's "view bulk lots for this game" link --
        # an unrecognised value just matches nothing rather than erroring
        stmt = stmt.where(BulkLot.game == game_filter)
    lots = session.scalars(stmt.order_by(BULK_SORTS[sort_key][1]).limit(120)).all()

    sort_options = "".join(
        f"<option value='{key}'{' selected' if key == sort_key else ''}>{escape(label)}</option>"
        for key, (label, _) in BULK_SORTS.items()
    )
    game_field = f"<input type='hidden' name='game' value='{escape(game_filter)}'>" if game_filter else ""
    filters = (
        "<form class='panel filters' method='get' action='/bulk'>"
        f"{game_field}"
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
            + (f"<img src='{escape(lot.image_url)}' alt='{escape(lot.title)}' loading='lazy'>" if lot.image_url
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

    heading = f"Bulk Master — {departments.GAME_LABELS.get(game_filter, game_filter.title())}" if game_filter else "Bulk Master"
    body = (
        f"<div class='page-head'><h1>{escape(heading)}</h1>"
        f"<p>Sorted, sleeved lots by set — the sensible way to buy commons. {count_note}.</p></div>"
        f"{filters}{content}"
    )
    crumbs = (("Home", "/"), ("Bulk Master", ""))
    return shell(session, "Bulk Master — NL Vault", body, request=request, query=query, crumbs=crumbs)


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
        f"<img src='{escape(lot.image_url)}' alt='{escape(lot.title)}' style='max-height:220px;margin:0 auto'>"
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
    site_url = str(request.base_url).rstrip("/")
    bulk_href = f"/bulk?game={escape(lot.game)}" if lot.game else "/bulk"
    crumbs = (("Home", "/"), ("Bulk Master", bulk_href), (lot.title, ""))
    lot_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": lot.title,
        "description": lot.description or f"{lot.card_count} sorted cards from {lot.title}.",
        "sku": lot.id,
        "offers": {
            "@type": "Offer",
            "url": site_url + request.url.path,
            "priceCurrency": lot.currency,
            "price": str(lot.price),
            "availability": (
                "https://schema.org/InStock" if lot.quantity > 0 else "https://schema.org/OutOfStock"
            ),
        },
    }
    if lot.image_url:
        lot_ld["image"] = [lot.image_url]
    return shell(session, f"{lot.title} — NL Vault", body, request=request, crumbs=crumbs,
                og_image=lot.image_url or "", extra_jsonld=(lot_ld,))


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
    crumbs = (("Home", "/"), ("Shipping & returns", ""))
    return shell(session, "Shipping — NL Vault", body, request=request, crumbs=crumbs)


def create_app() -> FastAPI:
    app = FastAPI(title="NL Vault", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    app.include_router(photo_router)
    from .favicon import router as favicon_router
    app.include_router(favicon_router)
    from .assets import router as assets_router
    app.include_router(assets_router)
    from .checkout import router as checkout_router
    app.include_router(checkout_router)

    from .contact import router as contact_router
    from .hardening import install_error_pages, install_security_headers, install_seo_routes
    from .legal import router as legal_router
    app.include_router(legal_router)
    app.include_router(contact_router)
    install_security_headers(app)
    install_error_pages(app)
    install_seo_routes(app)
    return app


app = create_app()
