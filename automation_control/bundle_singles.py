"""Review page for the cards split out of the October 2026 bundles (photo batch 7db66a0c).

Read-only. It lists the cards that now sell as singles, one tap from each card's
eBay preview and edit pages, and steps through them one at a time with their
photos, CRM price, quantities, eBay range and storage slot. Prices change only
on the listing's own edit page, and reach eBay only through "Revise on eBay".

A card's listing comes from the pair's matched listing, or, for pairs the
recognition could not match, from the inventory row stored in its placement.
Nothing here depends on metadata a batch retry would clear.
"""

from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CardPrice, InventoryItem, InventoryPlacement, ListingPhotoPair, ShopListing,
    ShopListingStatus, StorageLocation,
)
from .ui import brand_header, page

router = APIRouter(tags=["bundle-singles"])

BATCH_ID = "7db66a0c-47d4-437c-bfc3-685c6683f98c"
# Wrapper reverse, shared card back and the Broly back: photos, not cards for sale.
REFERENCE_POSITIONS = (2, 32, 34)

POSITIONS = {
    "below": ("Below eBay low", "low"),
    "range": ("Within eBay range", "mid"),
    "above": ("Above eBay median", "high"),
    "none": ("No eBay data", "none"),
}


def _latest_price(session: Session, catalog_item_id: str, source: str) -> Decimal | None:
    row = session.scalar(
        select(CardPrice.price)
        .where(CardPrice.card_id == catalog_item_id, CardPrice.source == source)
        .order_by(CardPrice.fetched_at.desc()).limit(1)
    )
    return Decimal(row) if row is not None else None


def _position(price: Decimal, low: Decimal | None, median: Decimal | None) -> str:
    if low is None or median is None:
        return "none"
    if price < low - Decimal("0.005"):
        return "below"
    return "range" if price <= median + Decimal("0.005") else "above"


def _listing_for(session: Session, pair: ListingPhotoPair, placement: InventoryPlacement | None) -> ShopListing | None:
    if pair.matched_shop_listing_id:
        return session.get(ShopListing, pair.matched_shop_listing_id)
    if placement is not None and placement.inventory_item_id:
        return session.scalar(
            select(ShopListing)
            .where(ShopListing.inventory_item_id == placement.inventory_item_id,
                   ShopListing.status != ShopListingStatus.WITHDRAWN)
            .order_by(ShopListing.updated_at.desc()).limit(1)
        )
    return None


def _cards(session: Session) -> list[dict]:
    pairs = session.scalars(
        select(ListingPhotoPair)
        .where(ListingPhotoPair.batch_id == BATCH_ID, ListingPhotoPair.position.not_in(REFERENCE_POSITIONS))
        .order_by(ListingPhotoPair.position)
    )
    cards = []
    for pair in pairs:
        placement = session.scalar(
            select(InventoryPlacement).where(InventoryPlacement.listing_photo_pair_id == pair.id)
        )
        listing = _listing_for(session, pair, placement)
        if listing is None:
            continue
        inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
        slot = session.get(StorageLocation, placement.storage_location_id) if placement else None
        low = _latest_price(session, listing.catalog_item_id, "ebay_active_low")
        median = _latest_price(session, listing.catalog_item_id, "ebay_active_median")
        cards.append({
            "pair": pair, "listing": listing, "inventory": inventory, "slot": slot,
            "stored": bool(placement and placement.confirmed_at),
            "low": low, "median": median,
            "position": _position(listing.price, low, median),
        })
    return cards


def _money(value: Decimal | None) -> str:
    return "&ndash;" if value is None else f"${value:,.2f}"


def _chip(key: str) -> str:
    label, cls = POSITIONS[key]
    return f"<span class='bs-chip {cls}'>{label}</span>"


def _qty(card: dict) -> str:
    listing, inventory = card["listing"], card["inventory"]
    if inventory is None:
        return f"{listing.quantity} <small class='bs-warn'>no inventory</small>"
    note = "" if inventory.quantity == listing.quantity else f" <small>inventory {inventory.quantity}</small>"
    return f"{listing.quantity}{note}"


def _slot(card: dict) -> str:
    if card["slot"] is None:
        return "<span class='bs-dim'>none</span>"
    state = "stored" if card["stored"] else "reserved"
    return f"{escape(card['slot'].name)} <small>{state}</small>"


_STYLE = """<style>
.bs-wrap{display:grid;gap:14px}.bs-dim,.bs-card small,.bs-facts small{color:var(--text-dim)}
.bs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.bs-card{display:grid;grid-template-columns:72px 1fr;gap:10px;padding:10px;border:1px solid var(--panel-border);border-radius:10px;background:var(--panel);color:var(--text);text-decoration:none}
.bs-card:hover{border-color:var(--accent);text-decoration:none}.bs-card img{width:72px;aspect-ratio:3/4;object-fit:cover;border-radius:6px;background:#fff}
.bs-card b{display:block;line-height:1.25}.bs-card .bs-row{display:flex;gap:8px;flex-wrap:wrap;align-items:baseline;margin-top:4px;font-size:13px}
.bs-chip{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px}
.bs-chip.low{background:rgba(214,158,46,.18);color:#c98a12}.bs-chip.mid{background:rgba(46,160,90,.16);color:#2e9f5a}
.bs-chip.high{background:rgba(217,83,79,.16);color:#d9534f}.bs-chip.none{background:rgba(127,127,127,.18);color:var(--text-dim)}
.bs-warn{color:#d9534f;font-weight:700}
.bs-step{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;align-items:start}
.bs-photos{display:grid;grid-template-columns:1fr 1fr;gap:8px}.bs-photos img{width:100%;aspect-ratio:3/4;object-fit:contain;background:#fff;border-radius:8px}
.bs-facts{display:grid;grid-template-columns:minmax(120px,auto) 1fr;gap:6px 14px;margin:0}.bs-facts dt{font-size:12px;font-weight:700;color:var(--text-dim)}.bs-facts dd{margin:0}
.bs-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.bs-pager{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.bs-note{padding:10px 12px;border:1px solid var(--panel-border);border-radius:8px;background:var(--surface-sunken);font-size:13px;color:var(--text-dim)}
@media(max-width:720px){.bs-step{grid-template-columns:1fr}}
</style>"""


@router.get("/bundle-singles", response_class=HTMLResponse)
def bundle_singles(user: str = Depends(require_dashboard_user),
                   session: Session = Depends(get_session)) -> HTMLResponse:
    cards = _cards(session)
    tiles = "".join(
        f"<a class='bs-card' href='/bundle-singles/{c['pair'].position}'>"
        f"<img loading='lazy' src='/shop-listings/{escape(c['listing'].id)}/thumb' alt=''>"
        f"<div><b>{escape(c['listing'].title)}</b>"
        f"<div class='bs-row'><strong>{_money(c['listing'].price)}</strong><span>qty {_qty(c)}</span></div>"
        f"<div class='bs-row'>{_chip(c['position'])}</div>"
        f"<div class='bs-row'><small>Slot {_slot(c)}</small></div></div></a>"
        for c in cards
    )
    start = (f"<a class='btn' href='/bundle-singles/{cards[0]['pair'].position}'>Start with the first card</a>"
             if cards else "")
    body = (
        brand_header("Bundle singles")
        + f"<div class='bs-wrap'><p class='subtitle'>{len(cards)} cards split out of the October bundles. "
          "Open one to see its photos, price, quantities and eBay range, then edit or revise it.</p>"
        + "<p class='bs-note'>Prices shown are the CRM prices. They reach eBay only when you press "
          "<b>Revise on eBay</b> on a listing. Don't use Apply on the batch page for these cards.</p>"
        + f"<div>{start}</div><div class='bs-grid'>{tiles or '<p>No cards found for this batch.</p>'}</div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Bundle singles", body))


@router.get("/bundle-singles/{position}", response_class=HTMLResponse)
def bundle_single(position: int, user: str = Depends(require_dashboard_user),
                  session: Session = Depends(get_session)) -> HTMLResponse:
    cards = _cards(session)
    index = next((i for i, c in enumerate(cards) if c["pair"].position == position), None)
    if index is None:
        raise HTTPException(status_code=404, detail="card not found in this batch")
    card = cards[index]
    listing, inventory, pair = card["listing"], card["inventory"], card["pair"]
    prev_link = (f"<a class='btn ghost' href='/bundle-singles/{cards[index - 1]['pair'].position}'>&larr; Previous</a>"
                 if index > 0 else "<span></span>")
    next_link = (f"<a class='btn' href='/bundle-singles/{cards[index + 1]['pair'].position}'>Next &rarr;</a>"
                 if index + 1 < len(cards) else "<span></span>")
    images = f"/ebay-listing-bulk-photos/{BATCH_ID}/image/{escape(pair.id)}"
    back = (f"<img src='{images}/back' alt='Back of card'>" if pair.back_image_path else "")
    facts = [
        ("CRM price", f"<strong>{_money(listing.price)}</strong>"),
        ("Listing status", escape(listing.status.value)),
        ("Quantity", _qty(card)),
        ("Printing", escape(inventory.variant.value.replace("_", " ").lower()) if inventory else "&ndash;"),
        ("Condition", escape(listing.condition or "&ndash;")),
        ("eBay low", _money(card["low"])),
        ("eBay median", _money(card["median"])),
        ("Position", _chip(card["position"])),
        ("Storage slot", _slot(card)),
    ]
    rows = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in facts)
    body = (
        brand_header("Bundle singles")
        + "<div class='bs-wrap'>"
        + f"<div class='bs-pager'>{prev_link}<span class='bs-dim'>{index + 1} of {len(cards)}</span>{next_link}</div>"
        + f"<h2>{escape(listing.title)}</h2>"
        + f"<div class='bs-step'><div class='bs-photos'><img src='{images}/front' alt='Front of card'>{back}</div>"
        + f"<div><dl class='bs-facts'>{rows}</dl>"
        + "<div class='bs-actions'>"
          f"<a class='btn' href='/shop-listings/{escape(listing.id)}/edit'>Edit price and details</a>"
          f"<a class='btn ghost' href='/shop-listings/{escape(listing.id)}/ebay-preview'>Preview and revise on eBay</a>"
          "<a class='btn ghost' href='/bundle-singles'>All cards</a></div>"
        + "<p class='bs-note'>Changing a price here only changes the CRM. eBay updates when you press "
          "<b>Revise on eBay</b> on the preview page.</p></div></div></div>"
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {listing.title}", body))
