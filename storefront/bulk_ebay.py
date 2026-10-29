"""Bulk & Bundles -- the shop's bulk lots, bundles and mystery listings,
shown straight from the live eBay listings.

These are sold on eBay as multi-card or multi-quantity listings ("Pokemon
50 Card Bulk Lot", "Mystery Energy Cards", "Bundle x4 ..."), so there is
no single catalogue card or ShopListing behind them to show the normal
way. Instead this reads the eBay active-listings cache that the hourly
refresh fills from the eBay API: a lot that ends or sells out drops off
the page on the next refresh, with no copy of it to keep in sync.

Which listings count is decided from the title (it must be a card listing
*and* read as bulk/lot/bundle/mystery), and anything already on the site
as a single is left out so nothing appears twice.
"""

import re
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.database import get_session
from automation_control.models import (
    EbayExportItem, EbayListing, EbayListingLink, ShopListing, ShopListingStatus,
)

router = APIRouter()

SLUG = "bulk"
NAME = "Bulk & Bundles"
BLURB = "Bulk lots, bundles and mystery packs, sold through our eBay store."

_CARD_WORDS = re.compile(r"pok[eé]mon|one piece|dragon ball|\btcg\b|\bccg\b|carddass|\bcards?\b", re.I)
_BULK_WORDS = re.compile(
    r"\bbulk\b|\blots?\b|\bbundle\b|\bmystery\b|\brandom\b|\bcode cards?\b"
    r"|\bx\s?\d+\b|\b\d+\s?x\b|\b\d+\s+cards?\b",
    re.I,
)

# Sub-categories, in tab order.
GAMES = {"pokemon": "Pokémon", "one_piece": "One Piece", "dragon_ball": "Dragon Ball", "other": "Other"}


def _game(title: str) -> str:
    lowered = title.lower()
    if "pokemon" in lowered or "pokémon" in lowered:
        return "pokemon"
    if "one piece" in lowered:
        return "one_piece"
    if "dragon ball" in lowered:
        return "dragon_ball"
    return "other"


def is_bulk_title(title: str | None) -> bool:
    return bool(title) and bool(_CARD_WORDS.search(title)) and bool(_BULK_WORDS.search(title))


def _https(url: object, host: str) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") and host in url.lower() else None


def bulk_listings(session: Session, game: str = "") -> list[dict]:
    """Live eBay bulk/bundle listings, dearest first."""
    on_site = set(session.scalars(
        select(EbayListingLink.ebay_listing_id)
        .join(ShopListing, ShopListing.id == EbayListingLink.shop_listing_id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, EbayListingLink.ebay_listing_id.is_not(None))
    ))
    on_site |= set(session.scalars(
        select(EbayExportItem.ebay_listing_id)
        .join(ShopListing, ShopListing.id == EbayExportItem.sku)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, EbayExportItem.ebay_listing_id.is_not(None))
    ))
    rows = []
    for listing in session.scalars(select(EbayListing).where(EbayListing.listing_status == "Active")):
        if listing.ebay_listing_id in on_site or not is_bulk_title(listing.title):
            continue
        snapshot = listing.raw_snapshot or {}
        url = _https(snapshot.get("view_item_url"), "ebay.")
        if url is None:
            continue
        images = snapshot.get("image_urls") or []
        rows.append({
            "id": listing.ebay_listing_id,
            "title": listing.title,
            "price": Decimal(listing.current_price_minor) / 100,
            "currency": listing.currency or "AUD",
            "quantity": listing.quantity,
            "image": next((u for u in images if _https(u, "ebayimg.com")), None),
            "url": url,
            "game": _game(listing.title),
        })
    if game:
        rows = [r for r in rows if r["game"] == game]
    return sorted(rows, key=lambda r: (-r["price"], r["title"].lower()))


def _tile(row: dict) -> str:
    art = (
        f"<img src='{escape(row['image'])}' alt='{escape(row['title'])}' loading='lazy' referrerpolicy='no-referrer'>"
        if row["image"] else "<span class='noart'>no image</span>"
    )
    left = (f"{row['quantity']} available" if row["quantity"] and row["quantity"] > 1 else "")
    return (
        f"<a class='card' href='{escape(row['url'])}' target='_blank' rel='noopener noreferrer'>"
        f"<div class='card-art'>{art}<span class='art-tag own'>On eBay &#8599;</span></div>"
        "<div class='card-body'>"
        f"<div class='card-title'>{escape(row['title'])}</div>"
        f"<div class='card-sub'>{escape(GAMES[row['game']])}{' · ' + left if left else ''}</div>"
        f"<div class='card-price'>${row['price']:,.2f}</div>"
        "</div></a>"
    )


def home_tile(session: Session) -> str:
    """The homepage tile, or "" while there's nothing to show."""
    rows = bulk_listings(session)
    if not rows:
        return ""
    image = next((r["image"] for r in rows if r["image"]), None)
    art = (f"<img class='dept-art' src='{escape(image)}' alt='{escape(NAME)}' loading='lazy' "
           "referrerpolicy='no-referrer'>" if image else "")
    return (
        f"<a class='dept-card' href='/shop/{SLUG}'>"
        f"<div class='dept-hero'>{art}</div>"
        "<div class='dept-card-body'>"
        f"<h3>{escape(NAME)}</h3><p>{escape(BLURB)}</p>"
        "<div class='dept-meta'><span>Explore bundles</span><span class='arrow' aria-hidden='true'>&rarr;</span></div>"
        "</div></a>"
    )


@router.get("/shop/bulk")
def bulk_page(request: Request, sub: str = Query(""), session: Session = Depends(get_session)):
    from .common import shell

    everything = bulk_listings(session)
    present = [g for g in GAMES if any(r["game"] == g for r in everything)]
    sub = sub if sub in present else ""
    rows = [r for r in everything if r["game"] == sub] if sub else everything

    tabs = ""
    if len(present) > 1:
        links = [f"<a class='sub-tab{'' if sub else ' active'}' href='/shop/{SLUG}'>All bulk</a>"]
        links += [
            f"<a class='sub-tab{' active' if g == sub else ''}' href='/shop/{SLUG}?sub={g}'>{escape(GAMES[g])}</a>"
            for g in present
        ]
        tabs = f"<div class='seg-tabs sub-tabs'>{''.join(links)}</div>"

    if rows:
        content = f"<div class='grid'>{''.join(_tile(r) for r in rows)}</div>"
        sub_line = f"{len(rows)} listing(s)"
    else:
        content = "<div class='panel empty'><h2>Nothing here right now</h2><p class='muted'>Check back soon.</p></div>"
        sub_line = "Nothing listed right now"
    body = (
        f"<div class='page-head'><h1>{escape(NAME)}</h1><p>{escape(BLURB)} {sub_line}.</p></div>"
        f"{tabs}{content}"
        "<div class='notice'>These open on eBay, where you buy, pay and get eBay's buyer protection.</div>"
    )
    return shell(session, f"{NAME} — NL Vault", body, request=request, crumbs=(("Home", "/"), (NAME, "")))
