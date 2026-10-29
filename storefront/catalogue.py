"""Catalogue (display-only) mode.

While the shop is a window rather than a till, NL Vault shows what's in
stock and sends buyers to the marketplace that actually sells it -- eBay,
Vinted, Whatnot -- instead of running its own cart and checkout.

It is a switch, not a deletion: `STOREFRONT_MODE=catalogue` hides the cart,
turns the cart/checkout URLs into redirects and swaps every "Add to cart"
for outbound buttons. Leaving it at the default `shop` brings the original
behaviour back untouched, so moving to direct sales later is one setting.
"""

from html import escape

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from automation_control.config import get_settings
from automation_control.models import (
    EbayExportItem, EbayExportItemStatus, EbayListing, EbayListingLink, ShopListing, SocialLink,
)

# Platforms that sell stock, in the order their buttons appear. Anything else
# in social_links (Instagram) is a "follow" link, not a place to buy.
MARKETPLACES = {"ebay": "eBay", "vinted": "Vinted", "whatnot": "Whatnot"}

# Paths that belong to the till. Webhooks are left alone on purpose: a
# payment that was already in flight must still be able to land.
_COMMERCE_PREFIXES = ("/cart", "/checkout")


def is_catalogue() -> bool:
    return (get_settings().storefront_mode or "shop").strip().lower() == "catalogue"


def install(app) -> None:
    """Redirect the cart/checkout URLs home while in catalogue mode, so an
    old bookmark or cached page lands somewhere useful instead of a till
    that isn't open."""

    @app.middleware("http")
    async def _no_till(request: Request, call_next):
        if is_catalogue() and request.url.path.startswith(_COMMERCE_PREFIXES):
            return RedirectResponse("/", status_code=303)
        return await call_next(request)


def _is_http_url(url: str | None) -> bool:
    return bool(url) and url.lower().startswith(("https://", "http://"))


def _live_url(session: Session, ebay_listing_id: str | None) -> str | None:
    if not ebay_listing_id:
        return None
    row = session.execute(
        select(EbayListing.raw_snapshot, EbayListing.listing_status)
        .where(EbayListing.ebay_listing_id == ebay_listing_id)
    ).first()
    if row is None or (row.listing_status or "").lower() != "active":
        return None
    url = (row.raw_snapshot or {}).get("view_item_url")
    return url if _is_http_url(url) and "ebay." in url.lower() else None


def ebay_item_url(session: Session, shop_listing_id: str) -> str | None:
    """The live eBay page for one shop listing, or None when it isn't (or is
    no longer) listed there.

    URLs come from the eBay API's own listing data (the hourly refresh stores
    each item's `view_item_url`). A listing is tied to its eBay item the same
    ways the rest of the app ties them, in order:
      1. its Sell API link,
      2. its confirmed File Exchange export (older listings),
      3. an *identical* title, only when exactly one live listing has it
         (a re-listed item gets a new id). Never a fuzzy match: a base card
         and its alternate art must not share a link.
    """
    link_id = session.scalar(
        select(EbayListingLink.ebay_listing_id).where(EbayListingLink.shop_listing_id == shop_listing_id)
    )
    url = _live_url(session, link_id)
    if url:
        return url

    export_ids = session.scalars(
        select(EbayExportItem.ebay_listing_id)
        .where(
            EbayExportItem.sku == shop_listing_id, EbayExportItem.kind == "single",
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            EbayExportItem.ebay_listing_id.is_not(None),
        )
        .order_by(EbayExportItem.exported_at.desc())
    ).all()
    for export_id in export_ids:
        url = _live_url(session, export_id)
        if url:
            return url

    title = session.scalar(select(ShopListing.title).where(ShopListing.id == shop_listing_id))
    if title:
        same = session.execute(
            select(EbayListing.ebay_listing_id)
            .where(func.lower(EbayListing.title) == title.strip().lower(), EbayListing.listing_status == "Active")
        ).scalars().all()
        if len(same) == 1:
            return _live_url(session, same[0])
    return None


def marketplace_links(session: Session) -> list[tuple[str, str, str]]:
    """(platform, label, url) for each marketplace with a real URL set."""
    rows = session.scalars(
        select(SocialLink).where(SocialLink.is_active.is_(True)).order_by(SocialLink.sort_order)
    ).all()
    found = {r.platform: r for r in rows if r.platform in MARKETPLACES and _is_http_url(r.url)}
    return [(p, MARKETPLACES[p], found[p].url) for p in MARKETPLACES if p in found]


def _button(url: str, label: str, primary: bool = False) -> str:
    cls = "btn brass" if primary else "btn ghost"
    return (f"<a class='{cls}' href='{escape(url)}' target='_blank' rel='noopener noreferrer'>"
            f"{escape(label)} &#8599;</a>")


def buy_block(session: Session, listing_id: str, sold_out: bool) -> str:
    """The item page's replacement for "Add to cart"."""
    direct = None if sold_out else ebay_item_url(session, listing_id)
    others = [(p, label, url) for p, label, url in marketplace_links(session)]
    if direct:
        # eBay is the exact listing; the other marketplaces are shop-wide.
        buttons = [_button(direct, "View on eBay", primary=True)]
        buttons += [_button(url, f"{label} shop") for p, label, url in others if p != "ebay"]
        note = "Buy securely on eBay. Postage and returns are handled by eBay."
    else:
        buttons = [_button(url, f"Find us on {label}", primary=(i == 0)) for i, (p, label, url) in enumerate(others)]
        if sold_out:
            note = "This one has sold. Have a look at what's new on our other channels."
        elif buttons:
            note = "Ask about this item on any of our channels below."
        else:
            note = "Not currently available to buy online."
    return (
        f"<div class='buy-links' style='display:flex;flex-wrap:wrap;gap:10px'>{''.join(buttons)}</div>"
        f"<div class='notice'>{escape(note)}</div>"
    )
