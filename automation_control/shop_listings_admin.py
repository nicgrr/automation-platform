"""Shop listing review -- where a draft created by scripts/list_for_sale.py
actually becomes visible to a human.

That script's own docstring calls listing "a deliberate act, not a side
effect of scanning" and creates DRAFT rows by default specifically so
there's a review step before anything goes public -- but until now there
was no page anywhere in this app that showed a DRAFT listing. shop-pricing
only ever queried ACTIVE. The review step the script promised didn't
exist; this is it.

Read the row, activate it (or withdraw it), one at a time or in bulk for
whatever the current filter shows -- the bulk action is scoped to the
filtered set on the server, not just whatever page of it is on screen.
"""

import uuid
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import CardSet, EbayListingLink, EbayListingLinkStatus, ShopListing, ShopListingStatus, TcgCard
from .ui import brand_header, page, pill

router = APIRouter(tags=["shop-listings"])

FILTERS = {
    "draft": "Drafts",
    "active": "Active",
    "withdrawn": "Withdrawn",
    "sold_out": "Sold out",
    "all": "All",
}

STATUS_PILLS = {
    ShopListingStatus.DRAFT: "warn",
    ShopListingStatus.ACTIVE: "ok",
    ShopListingStatus.WITHDRAWN: "neutral",
    ShopListingStatus.SOLD_OUT: "bad",
}


def _money(value) -> str:
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


def _filtered(session: Session, status: str, q: str):
    stmt = select(ShopListing)
    if status != "all" and status in {s.value for s in ShopListingStatus}:
        stmt = stmt.where(ShopListing.status == ShopListingStatus(status))
    if q:
        stmt = stmt.where(ShopListing.title.ilike(f"%{q}%"))
    return stmt


def _set_names(session: Session, listings: list[ShopListing]) -> dict[str, str]:
    """catalog_item_id -> set name, for the listings on this page only --
    a listing has no set of its own, that's a TcgCard+CardSet join away."""
    item_ids = [listing.catalog_item_id for listing in listings]
    if not item_ids:
        return {}
    rows = session.execute(
        select(TcgCard.catalog_item_id, CardSet.name)
        .join(CardSet, CardSet.id == TcgCard.set_id, isouter=True)
        .where(TcgCard.catalog_item_id.in_(item_ids))
    ).all()
    return {catalog_item_id: name for catalog_item_id, name in rows if name}


def _ebay_links(session: Session, listings: list[ShopListing]) -> dict[str, EbayListingLink]:
    ids = [listing.id for listing in listings]
    if not ids:
        return {}
    rows = session.scalars(select(EbayListingLink).where(EbayListingLink.shop_listing_id.in_(ids))).all()
    return {link.shop_listing_id: link for link in rows}


_EBAY_LINK_PILLS = {
    EbayListingLinkStatus.PUBLISHING: "warn",
    EbayListingLinkStatus.ACTIVE: "ok",
    EbayListingLinkStatus.FAILED: "bad",
    EbayListingLinkStatus.ENDING: "warn",
    EbayListingLinkStatus.ENDED: "neutral",
}


def _row(listing: ShopListing, set_name: str | None, ebay_link: EbayListingLink | None) -> str:
    sub = f"{escape(set_name)} · " if set_name else ""
    actions = []
    if listing.status == ShopListingStatus.DRAFT:
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/activate'>"
            "<button class='btn small'>Activate</button></form>"
        )
    if listing.status in (ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE):
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/withdraw'>"
            "<button class='btn small ghost'>Withdraw</button></form>"
        )
    if ebay_link is None or ebay_link.status in (EbayListingLinkStatus.FAILED, EbayListingLinkStatus.ENDED):
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/publish-to-ebay'>"
            f"<button class='btn small ghost'>{'Retry eBay' if ebay_link else 'Publish to eBay'}</button></form>"
        )
    else:
        actions.append(pill(f"eBay: {ebay_link.status.value}", _EBAY_LINK_PILLS.get(ebay_link.status, "neutral")))
    return (
        "<tr>"
        f"<td><strong>{escape(listing.title)}</strong><div class='muted'>{sub}{escape(listing.condition or 'Ungraded')}</div></td>"
        f"<td>{listing.quantity}</td>"
        f"<td>{_money(listing.price)} {escape(listing.currency)}</td>"
        f"<td>{pill(listing.status.value.replace('_', ' ').upper(), STATUS_PILLS.get(listing.status, 'neutral'))}</td>"
        f"<td class='listing-actions'>{''.join(actions) or '—'}</td>"
        "</tr>"
    )


@router.get("/shop-listings", response_class=HTMLResponse)
def shop_listings_page(
    status: str = Query("draft"), q: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    status = status if status in FILTERS else "draft"
    query = q.strip()
    # counted separately from the capped page of rows below, so "Activate
    # all" can say -- and act on -- the true total, not just what's shown
    count = len(session.scalars(_filtered(session, status, query)).all())
    listings = session.scalars(_filtered(session, status, query).order_by(ShopListing.price.desc()).limit(200)).all()
    set_names = _set_names(session, listings)
    ebay_links = _ebay_links(session, listings)

    rows = "".join(
        _row(listing, set_names.get(listing.catalog_item_id), ebay_links.get(listing.id))
        for listing in listings
    ) or "<tr><td colspan='5' class='muted'>Nothing here.</td></tr>"

    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' "
        f"href='/shop-listings?status={key}{'&q=' + escape(query) if query else ''}'>{escape(label)}</a>"
        for key, label in FILTERS.items()
    )

    search_form = (
        "<form method='get' action='/shop-listings' class='listing-search'>"
        f"<input type='hidden' name='status' value='{escape(status)}'>"
        f"<input name='q' value='{escape(query)}' placeholder='Search by title…'>"
        "<button class='btn small'>Search</button>"
        + (f"<a class='btn small ghost' href='/shop-listings?status={escape(status)}'>Clear</a>" if query else "")
        + "</form>"
    )

    bulk_action = ""
    if status == "draft" and count:
        bulk_action = (
            f"<form method='post' action='/shop-listings/activate-all?status=draft"
            f"{'&q=' + escape(query) if query else ''}'>"
            f"<button class='btn'>Activate all {count} draft(s){' matching this search' if query else ''}</button>"
            "</form>"
        )

    note = (
        f"<p class='subtitle'>{count} listing(s){' shown, capped at 200 rows -- narrow your search to see the rest' if count > 200 else ''}.</p>"
    )

    body = (
        brand_header("Shop listings")
        + note
        + f"<div class='filter-tabs'>{filter_bar}</div>"
        + f"<div class='listing-toolbar'>{search_form}{bulk_action}</div>"
        + "<div class='panel'><div class='table-wrap'><table><thead><tr>"
        + "<th>Item</th><th>Qty</th><th>Price</th><th>Status</th><th></th>"
        + f"</tr></thead><tbody>{rows}</tbody></table></div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Shop listings", body))


@router.post("/shop-listings/{listing_id}/activate")
def activate_listing(
    listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if listing.status == ShopListingStatus.DRAFT:
        listing.status = ShopListingStatus.ACTIVE
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_listing.activate",
                    resource_type="shop_listing", resource_id=listing_id, outcome="success",
                    correlation_id=listing_id, details={})
    return RedirectResponse("/shop-listings?status=draft", status_code=303)


@router.post("/shop-listings/{listing_id}/withdraw")
def withdraw_listing(
    listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if listing.status in (ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE):
        listing.status = ShopListingStatus.WITHDRAWN
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_listing.withdraw",
                    resource_type="shop_listing", resource_id=listing_id, outcome="success",
                    correlation_id=listing_id, details={})
    return RedirectResponse("/shop-listings", status_code=303)


@router.post("/shop-listings/activate-all")
def activate_all_drafts(
    status: str = Query("draft"), q: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    query = q.strip()
    listings = session.scalars(_filtered(session, "draft", query)).all()
    count = 0
    for listing in listings:
        listing.status = ShopListingStatus.ACTIVE
        count += 1
    if count:
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_listing.activate_all",
                    resource_type="shop_listing", resource_id="bulk", outcome="success",
                    correlation_id=str(uuid.uuid4()), details={"count": count, "query": query})
    return RedirectResponse("/shop-listings?status=active", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.listing-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
.listing-search{display:flex;gap:8px}
.listing-search input{padding:7px 10px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px;width:220px}
.listing-actions{display:flex;gap:6px}
.btn.small{padding:6px 12px;font-size:13px}
</style>"""
