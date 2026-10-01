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
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .images import serve_image, serve_thumb
from .models import (
    AuditEvent,
    CardSet,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
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


THUMB_WIDTH = 160


def _is_web_url(url: str | None) -> bool:
    """ShopListing.image_url holds a local scan-ingest cache path
    (`scan_ingest_data/cache/...`) for the vast majority of rows -- an
    identification-cache artifact, not a publicly servable URL. Same guard
    personal_collection.py uses, kept local for the same reason it is."""
    return bool(url) and (url.startswith("http://") or url.startswith("https://"))


def _resolve_image(session: Session, listing: ShopListing) -> tuple[str | None, bool]:
    """(source, is_own_photo). scripts/list_for_sale.py seeds
    ShopListing.image_url from CatalogItem.image_url at creation time --
    the same generic reference picture every copy of this card shares,
    whether or not it matches what's physically in hand. The one thing
    that actually shows the physical item is InventoryItem.scan_image_path
    on the linked stock row, so that wins whenever it exists; the listing's
    own image_url is only ever a fallback, and callers must say so rather
    than let a reference picture pass as proof of what's in the box."""
    if listing.inventory_item_id:
        item = session.get(InventoryItem, listing.inventory_item_id)
        if item and item.scan_image_path:
            return item.scan_image_path, True
    return listing.image_url, False


def _resolve_images(session: Session, listings: list[ShopListing]) -> dict[str, tuple[str | None, bool]]:
    """Batch form of _resolve_image for a page of rows, one query instead
    of one InventoryItem lookup per listing."""
    inv_ids = [listing.inventory_item_id for listing in listings if listing.inventory_item_id]
    scans: dict[str, str | None] = {}
    if inv_ids:
        rows = session.execute(
            select(InventoryItem.id, InventoryItem.scan_image_path).where(InventoryItem.id.in_(inv_ids))
        ).all()
        scans = dict(rows)
    result = {}
    for listing in listings:
        scan = scans.get(listing.inventory_item_id) if listing.inventory_item_id else None
        result[listing.id] = (scan, True) if scan else (listing.image_url, False)
    return result


def _filter_counts(session: Session, q: str) -> dict[str, int]:
    """How many listings sit behind each tab, for the current search.

    This page opens on Drafts by design -- it exists because nothing else
    showed a DRAFT row. But with one draft and 995 active listings it
    greeted you with "1 listing(s)", which reads as the shop being empty.
    The counts fix that without moving the review step off the front.
    """
    return {key: len(session.scalars(_filtered(session, key, q)).all()) for key in FILTERS}


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


def _withhold_reasons(session: Session, listings: list[ShopListing]) -> dict[str, str]:
    """resource_id -> reason, for listings withheld by a one-off review
    script (see e.g. scripts/withhold_unphotographed_151_for_pattern_review.py)
    rather than withdrawn by hand -- so a batch of withdrawn rows reads as
    "why" instead of a plain, unexplained status change."""
    ids = [listing.id for listing in listings if listing.status == ShopListingStatus.WITHDRAWN]
    if not ids:
        return {}
    rows = session.execute(
        select(AuditEvent.resource_id, AuditEvent.details)
        .where(AuditEvent.action == "listing.withhold_for_review", AuditEvent.resource_id.in_(ids))
        .order_by(AuditEvent.occurred_at.desc())
    ).all()
    reasons: dict[str, str] = {}
    for resource_id, details in rows:
        reasons.setdefault(resource_id, (details or {}).get("reason", ""))
    return {rid: reason for rid, reason in reasons.items() if reason}


_EBAY_LINK_PILLS = {
    EbayListingLinkStatus.PUBLISHING: "warn",
    EbayListingLinkStatus.ACTIVE: "ok",
    EbayListingLinkStatus.FAILED: "bad",
    EbayListingLinkStatus.ENDING: "warn",
    EbayListingLinkStatus.ENDED: "neutral",
}


def _row(listing: ShopListing, set_name: str | None, ebay_link: EbayListingLink | None, withhold_reason: str = "",
        image_source: str | None = None, is_own_photo: bool = False) -> str:
    sub = f"{escape(set_name)} · " if set_name else ""
    if image_source:
        badge = "<span class='listing-photo-badge own'>Your photo</span>" if is_own_photo \
            else "<span class='listing-photo-badge ref'>Reference art</span>"
        thumb = (
            f"<a class='listing-thumb-link' href='/shop-listings/{escape(listing.id)}/image' target='_blank' rel='noopener'>"
            f"<img class='listing-thumb' src='/shop-listings/{escape(listing.id)}/thumb' loading='lazy' alt=''>{badge}</a>"
        )
    else:
        thumb = "<div class='listing-thumb listing-thumb-empty'></div>"
    reason_html = f"<div class='listing-reason'>{escape(withhold_reason)}</div>" if withhold_reason else ""
    actions = [
        # Correcting a wrong title or price used to mean editing the
        # database by hand -- a real listing was found live at ~9.6x its
        # correct price for a week because nothing else could fix it.
        # First in the list, not gated on status: a withdrawn or sold-out
        # listing's data can be just as wrong as an active one's.
        f"<a class='btn small ghost' href='/shop-listings/{escape(listing.id)}/edit'>Edit</a>"
    ]
    if listing.status == ShopListingStatus.DRAFT:
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/activate'>"
            "<button class='btn small'>Activate</button></form>"
        )
    if listing.status in (ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE):
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/withdraw' "
            # Static confirm text, not the listing's own title: a title
            # containing an apostrophe (a real card name can, e.g.
            # "Farfetch'd") would break out of the JS string literal here
            # if interpolated -- same reasoning business_records.py's own
            # confirm() dialogs already follow.
            "onsubmit=\"return confirm('Withdraw this listing?');\">"
            "<button class='btn small ghost'>Withdraw</button></form>"
        )
    if ebay_link is None or ebay_link.status in (EbayListingLinkStatus.FAILED, EbayListingLinkStatus.ENDED):
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/publish-to-ebay'>"
            f"<button class='btn small ghost'>{'Retry eBay' if ebay_link else 'Publish to eBay'}</button></form>"
        )
    else:
        actions.append(pill(f"eBay: {ebay_link.status.value}", _EBAY_LINK_PILLS.get(ebay_link.status, "neutral")))
    status_pill = pill(listing.status.value.replace('_', ' ').upper(), STATUS_PILLS.get(listing.status, 'neutral'))
    mobile_meta = (
        f"<div class='listing-mobile-meta'>Qty {listing.quantity} · "
        f"{_money(listing.price)} {escape(listing.currency)} · {status_pill}</div>"
    )
    return (
        "<tr>"
        f"<td>{thumb}</td>"
        f"<td><strong>{escape(listing.title)}</strong><div class='muted'>{sub}{escape(listing.condition or 'Ungraded')}</div>{reason_html}{mobile_meta}</td>"
        f"<td>{listing.quantity}</td>"
        f"<td>{_money(listing.price)} {escape(listing.currency)}</td>"
        f"<td>{status_pill}</td>"
        f"<td class='listing-actions'>{''.join(actions) or '—'}</td>"
        "</tr>"
    )


# --- images -----------------------------------------------------------

@router.get("/shop-listings/{listing_id}/thumb")
def shop_listing_thumb(listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """The row thumbnail -- the real scan when the linked stock has one,
    the catalogue reference picture otherwise (see _resolve_image). A
    remote reference/R2 URL is fast to hotlink directly; a local cache or
    scan path needs its own route since nothing else serves
    scan_ingest_data/."""
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    source, _ = _resolve_image(session, listing)
    if not source:
        raise HTTPException(status_code=404, detail="no image for this listing")
    if _is_web_url(source):
        return RedirectResponse(source)
    path = Path(source)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return serve_thumb(path, f"{listing_id}-{THUMB_WIDTH}.jpg", THUMB_WIDTH)


@router.get("/shop-listings/{listing_id}/image")
def shop_listing_image(listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Full-size view, opened from the thumbnail -- the actual "view the
    photo" action for deciding whether a withheld listing's physical card
    matches what the catalogue thinks it is. Same source preference as
    the thumbnail: the real scan first, the reference picture only if
    there isn't one."""
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    source, _ = _resolve_image(session, listing)
    if not source:
        raise HTTPException(status_code=404, detail="no image for this listing")
    if _is_web_url(source):
        return RedirectResponse(source)
    return serve_image(source)


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
    withhold_reasons = _withhold_reasons(session, listings)
    images = _resolve_images(session, listings)

    rows = "".join(
        _row(listing, set_names.get(listing.catalog_item_id), ebay_links.get(listing.id), withhold_reasons.get(listing.id, ""),
            *images.get(listing.id, (None, False)))
        for listing in listings
    ) or "<tr><td colspan='6' class='muted'>Nothing here.</td></tr>"

    counts = _filter_counts(session, query)
    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' "
        f"href='/shop-listings?status={key}{'&q=' + escape(query) if query else ''}'>{escape(label)}"
        f"<span class='filter-count'>{counts.get(key, 0)}</span></a>"
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
        + "<div class='panel'><div class='table-wrap'><table class='listing-table'><thead><tr>"
        + "<th></th><th>Item</th><th>Qty</th><th>Price</th><th>Status</th><th></th>"
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


@router.get("/shop-listings/{listing_id}/edit", response_class=HTMLResponse)
def edit_listing_form(
    listing_id: str, error: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")

    error_html = f"<p class='linkfill-note bad'>{escape(error)}</p>" if error else ""
    image_source, is_own_photo = _resolve_image(session, listing)
    if image_source:
        badge = "<span class='listing-photo-badge own'>Your photo</span>" if is_own_photo \
            else "<span class='listing-photo-badge ref'>Reference art — not a photo of your card</span>"
        photo_html = (
            f"<a class='edit-photo-link' href='/shop-listings/{escape(listing.id)}/image' target='_blank' rel='noopener'>"
            f"<img class='edit-photo' src='/shop-listings/{escape(listing.id)}/thumb' loading='lazy' alt=''></a>{badge}"
        )
    else:
        photo_html = "<div class='edit-photo edit-photo-empty'>No image</div>"
    body = (
        brand_header("Edit listing")
        + error_html
        + f"<div class='panel listing-edit-panel'>{photo_html}"
        + f"<form method='post' action='/shop-listings/{escape(listing.id)}/edit' class='listing-edit-form'>"
        + f"<label class='field'>Title<input name='title' value='{escape(listing.title)}' required maxlength='300'></label>"
        + f"<label class='field'>Price<input name='price' type='number' step='0.01' min='0.01' value='{listing.price}' required></label>"
        + f"<label class='field'>Quantity<input name='quantity' type='number' step='1' min='0' value='{listing.quantity}' required></label>"
        + f"<label class='field'>Condition<input name='condition' value='{escape(listing.condition or "")}' maxlength='120'></label>"
        + f"<label class='field'>Description<textarea name='description' rows='4' maxlength='4000'>{escape(listing.description or "")}</textarea></label>"
        + "<div class='listing-edit-actions'>"
        + "<button class='btn small'>Save</button>"
        + f"<a class='btn small ghost' href='/shop-listings?status={escape(listing.status.value)}'>Cancel</a>"
        + "</div></form></div>"
    )
    return HTMLResponse(page("EzBay — Edit listing", body))


@router.post("/shop-listings/{listing_id}/edit")
def edit_listing_save(
    listing_id: str, title: str = Form(...), price: str = Form(...), quantity: str = Form(...),
    condition: str = Form(""), description: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    from urllib.parse import quote

    listing = session.get(ShopListing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")

    title = title.strip()
    if not title:
        return RedirectResponse(f"/shop-listings/{listing_id}/edit?error={quote('Title cannot be empty')}", status_code=303)
    try:
        price_value = Decimal(price)
    except InvalidOperation:
        return RedirectResponse(f"/shop-listings/{listing_id}/edit?error={quote('Price must be a number')}", status_code=303)
    if price_value <= 0:
        return RedirectResponse(f"/shop-listings/{listing_id}/edit?error={quote('Price must be greater than zero')}", status_code=303)
    try:
        quantity_value = int(quantity)
    except ValueError:
        return RedirectResponse(f"/shop-listings/{listing_id}/edit?error={quote('Quantity must be a whole number')}", status_code=303)
    if quantity_value < 0:
        return RedirectResponse(f"/shop-listings/{listing_id}/edit?error={quote('Quantity cannot be negative')}", status_code=303)

    before = {"title": listing.title, "price": str(listing.price), "quantity": listing.quantity,
             "condition": listing.condition, "description": listing.description}
    listing.title = title
    listing.price = price_value
    listing.quantity = quantity_value
    listing.condition = condition.strip() or None
    listing.description = description.strip() or None
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="shop_listing.edit",
                resource_type="shop_listing", resource_id=listing_id, outcome="success",
                correlation_id=str(uuid.uuid4()),
                details={"before": before, "after": {"title": listing.title, "price": str(listing.price),
                                                      "quantity": listing.quantity, "condition": listing.condition,
                                                      "description": listing.description}})
    return RedirectResponse(f"/shop-listings?status={listing.status.value}", status_code=303)


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
.filter-count{margin-left:6px;opacity:.62;font-variant-numeric:tabular-nums;font-weight:500}
.listing-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
.listing-search{display:flex;gap:8px}
.listing-search input{padding:7px 10px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px;width:220px}
.listing-actions{display:flex;gap:6px}
.btn.small{padding:6px 12px;font-size:13px}
.listing-edit-form{display:flex;flex-direction:column;gap:14px;max-width:420px}
.listing-edit-form .field{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.listing-edit-form .field input, .listing-edit-form .field textarea{padding:9px 11px;border-radius:6px;
  border:1px solid var(--panel-border);font-size:14px;color:var(--text);background:var(--surface-sunken);
  font-family:inherit}
.listing-edit-actions{display:flex;gap:8px}
.listing-thumb{width:44px;height:60px;object-fit:cover;border-radius:5px;display:block}
.listing-thumb-empty{width:44px;height:60px;border-radius:5px;background:var(--surface-sunken)}
.listing-thumb-link{display:block;width:44px}
.listing-reason{margin-top:4px;font-size:12px;color:var(--text-dim);max-width:420px}
.listing-photo-badge{display:block;margin-top:3px;font-size:9px;line-height:1.3;font-weight:650;
  text-align:center;padding:1px 3px;border-radius:3px}
.listing-photo-badge.own{background:var(--success-bg,#1c3a2a);color:var(--success-text,#5fd88a)}
.listing-photo-badge.ref{background:var(--surface-sunken);color:var(--text-dim)}
.listing-edit-panel{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}
.edit-photo{width:160px;height:auto;border-radius:8px;display:block}
.edit-photo-empty{width:160px;height:220px;border-radius:8px;background:var(--surface-sunken);
  display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:13px;text-align:center}
.edit-photo-link{flex:none;display:block}
.listing-edit-panel .listing-photo-badge{margin-top:6px;width:160px;font-size:11px;padding:4px 6px}
.listing-table{white-space:normal}
.listing-mobile-meta{display:none}
@media (max-width: 680px) {
  .listing-table th:nth-child(3), .listing-table td:nth-child(3),
  .listing-table th:nth-child(4), .listing-table td:nth-child(4),
  .listing-table th:nth-child(5), .listing-table td:nth-child(5) { display: none; }
  .listing-mobile-meta { display: block; margin-top: 4px; font-size: 12px; color: var(--text-dim); }
  .listing-reason { max-width: none; }
}
</style>"""
