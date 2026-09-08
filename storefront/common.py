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
    BulkLot, CardSet, CatalogCard, CatalogItem, CatalogItemType, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, SocialLink, TcgCard,
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


def _is_web_url(url: str | None) -> bool:
    """`listing.image_url`/`item.image_url` turned out to hold a local
    scan-ingest cache path (`scan_ingest_data/cache/...`) for the vast
    majority of real TCG_CARD rows -- an identification-cache artifact,
    never a publicly servable URL -- rather than the hosted image the
    field is meant for. Guard against rendering that straight into an
    <img src>, which would just be a broken image."""
    return bool(url) and (url.startswith("http://") or url.startswith("https://"))


def _reference_image_url(session: Session, item: CatalogItem | None) -> str | None:
    """The one source confirmed to always hold a real hosted URL: the
    catalog_cards cache built for perceptual-hash identification, keyed
    by the same (set, number) a TcgCard already carries."""
    if item is None or item.item_type not in (CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD):
        return None
    tcg = session.get(TcgCard, item.id)
    if tcg is None or not tcg.set_id or not tcg.number:
        return None
    card = session.scalar(
        select(CatalogCard).where(CatalogCard.set_id == tcg.set_id, CatalogCard.number == tcg.number)
    )
    return card.image_url if card and _is_web_url(card.image_url) else None


def _art(item: CatalogItem | None, listing: ShopListing, session: Session | None = None,
         show_tag: bool = True) -> str:
    """Publisher reference art wins over the seller's own scan, for now.

    Used to be the other way around -- a shopper assessing condition on an
    ungraded single deserves to see the actual card, not a pristine studio
    render -- but a chunk of the scanned photos turned out to be genuinely
    saved upside down (a scan-ingest capture bug: the sheet's rotation is
    corrected for card *identification* but not baked into the saved crop
    itself), and a correct-looking stock photo beats a wrong-looking real
    one. Own photos stay as the fallback for anything with no reference
    image at all. Revert this once the capture pipeline is fixed and the
    already-saved crops are reprocessed.

    `show_tag=False` drops the label -- for a context where the photo
    isn't standing in for a specific physical item at all (a department
    tile's mood shot), the authenticity claim the tag makes has nothing
    to attach to and would just be confusing.
    """
    url = listing.image_url if _is_web_url(listing.image_url) else None
    if url is None and item is not None and _is_web_url(item.image_url):
        url = item.image_url
    if url is None and session is not None:
        url = _reference_image_url(session, item)
    if url:
        tag = "<span class='art-tag stock'>Stock image</span>" if show_tag else ""
        return f"<img src='{escape(url)}' alt='{escape(listing.title)}' loading='lazy'>{tag}"

    from .photos import has_own_photo

    if session is not None and has_own_photo(session, listing.id):
        tag = "<span class='art-tag own'>Actual card</span>" if show_tag else ""
        return f"<img src='/photo/{escape(listing.id)}' alt='{escape(listing.title)}' loading='lazy'>{tag}"
    return "<span class='noart'>no image</span>"


def _line_art(line: dict, session: Session | None = None) -> str:
    if line["is_lot"]:
        lot = line["listing"]
        if lot.image_url:
            return f"<img src='{escape(lot.image_url)}' alt='{escape(lot.title)}'>"
        return f"<span class='bulk-count small'>{lot.card_count}</span>"
    return _art(line["item"], line["listing"], session)


def nav_links(session: Session) -> str:
    """Drawer categories built from what's actually in stock, so the shop
    never advertises a section that would land on an empty page."""
    from . import departments

    groups = []
    stocked = departments.in_stock(session)
    if stocked:
        rows = "".join(
            f"<a href='/shop/{escape(d.slug)}'>{escape(d.name)}<span class='n'>{n}</span></a>"
            for d, n in stocked
        )
        groups.append(f"<div class='navgroup'><h4>Departments</h4>{rows}</div>")

    sets = session.execute(
        select(CardSet.name, BulkLot.id, BulkLot.card_count)
        .join(BulkLot, BulkLot.set_id == CardSet.id)
        .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
        .order_by(BulkLot.card_count.desc()).limit(8)
    ).all()
    if sets:
        set_rows = "".join(
            f"<a href='/bulk/{escape(lot_id)}'>{escape(name)}<span class='n'>{count}</span></a>"
            for name, lot_id, count in sets
        )
        groups.append(f"<div class='navgroup'><h4>Bulk by set</h4>{set_rows}</div>")

    return "".join(groups)


def dept_links(session: Session) -> str:
    """Department links for the header -- stocked departments only."""
    from . import departments

    return "".join(
        f"<a href='/shop/{escape(d.slug)}' class='hide-sm'>{escape(d.name)}</a>"
        for d, _ in departments.in_stock(session)
    )


_SOCIAL_ICONS = {
    "instagram": (
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8'>"
        "<rect x='3' y='3' width='18' height='18' rx='5'/>"
        "<circle cx='12' cy='12' r='4'/><circle cx='17.2' cy='6.8' r='1.1' fill='currentColor' stroke='none'/>"
        "</svg>"
    ),
    "ebay": (
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8'>"
        "<path d='M3 8h4l2 8 2.5-8h1l2.5 8L17 8h4'/><path d='M3 16h18' stroke-opacity='0'/>"
        "</svg>"
    ),
    "whatnot": (
        "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8'>"
        "<circle cx='12' cy='12' r='9'/><path d='M10 8.5l6 3.5-6 3.5z' fill='currentColor' stroke='none'/>"
        "</svg>"
    ),
}
_DEFAULT_SOCIAL_ICON = (
    "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8'>"
    "<circle cx='12' cy='12' r='9'/></svg>"
)


def _active_social_links(session: Session) -> list[SocialLink]:
    """Only platforms with a real url configured ever render. A row can
    exist as a placeholder (seeded with url=None, see
    scripts/migrate_add_social_and_contact.py) without ever showing as a
    broken or empty link."""
    return session.scalars(
        select(SocialLink)
        .where(SocialLink.is_active.is_(True), SocialLink.url.isnot(None), SocialLink.url != "")
        .order_by(SocialLink.sort_order)
    ).all()


def social_links_html(session: Session) -> str:
    """The footer row -- icon plus label."""
    rows = _active_social_links(session)
    if not rows:
        return ""
    links = "".join(
        f"<a href='{escape(row.url)}' class='social-link' target='_blank' rel='noopener noreferrer' "
        f"aria-label='{escape(row.label)}'>"
        f"{_SOCIAL_ICONS.get(row.platform, _DEFAULT_SOCIAL_ICON)}"
        f"<span>{escape(row.label)}</span></a>"
        for row in rows
    )
    return f"<div class='social-links'>{links}</div>"


def social_icons_html(session: Session) -> str:
    """A more prominent icon-only row for the home page hero -- same
    underlying links as the footer, larger circular buttons, no text
    label (the aria-label carries the name for assistive tech)."""
    rows = _active_social_links(session)
    if not rows:
        return ""
    icons = "".join(
        f"<a href='{escape(row.url)}' class='social-icon' target='_blank' rel='noopener noreferrer' "
        f"aria-label='{escape(row.label)}' title='{escape(row.label)}'>"
        f"{_SOCIAL_ICONS.get(row.platform, _DEFAULT_SOCIAL_ICON)}</a>"
        for row in rows
    )
    return f"<div class='social-icons'>{icons}</div>"


def shell(session: Session, title: str, body: str, *, request: Request, query: str = "",
          description: str | None = None, hero: str = "", robots: str = "", og_image: str = "",
          crumbs: tuple[tuple[str, str], ...] = (), extra_jsonld: tuple[dict, ...] = ()) -> HTMLResponse:
    kwargs = {"description": description} if description else {}
    site_url = str(request.base_url).rstrip("/")
    same_as = tuple(row.url for row in _active_social_links(session))
    return HTMLResponse(page(
        title, body, cart_count=cart_count(request), query=query,
        nav_links=nav_links(session), dept_links=dept_links(session),
        social_html=social_links_html(session), hero=hero,
        site_url=site_url, canonical=site_url + request.url.path,
        same_as=same_as, robots=robots, og_image=og_image,
        crumbs=crumbs, extra_jsonld=extra_jsonld, **kwargs,
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
