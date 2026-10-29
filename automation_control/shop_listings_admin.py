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
import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_, select, func
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .images import serve_image, serve_thumb
from .models import (
    AuditEvent,
    CardSet,
    CatalogItem,
    CatalogItemType,
    EbayExportItem,
    EbayExportItemStatus,
    EbayListing,
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
    "all": "All",
    "draft": "EZBay drafts",
    "active": "EZBay enabled",
    "withdrawn": "Withdrawn",
    "sold_out": "Out of stock",
}

EBAY_FILTERS = {
    "unpublished": "Not on eBay",
    "not_listed": "Not listed / ended",
    "all": "All listings",
    "live": "Live on eBay",
    "pending": "Publishing / pending",
    "attention": "Needs attention",
}

CATEGORY_FILTERS = {
    "all": "All categories",
    "pokemon": "Pokémon",
    "one_piece": "One Piece",
    "dragon_ball": "Dragon Ball",
    "collectibles": "Collectibles",
    "other": "Other",
}

GAME_CATEGORIES = {
    "pokemon": {"pokemon", "pokemon_japan"},
    "one_piece": {"one_piece"},
    "dragon_ball": {
        "dragon_ball_art_card", "dragon_ball_artbox", "dragon_ball_carddass",
        "dragon_ball_fusion_world", "dragon_ball_super", "dragon_ball_z",
        "super_dragon_ball_heroes", "dragon_ball_super_divers",
    },
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
    """Use the saved listing gallery before legacy inventory/reference art."""
    if listing.ebay_photo_paths is not None:
        photos = [str(p).strip() for p in listing.ebay_photo_paths if str(p).strip()]
        return (photos[0], True) if photos else (None, False)
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
        if listing.ebay_photo_paths is not None:
            result[listing.id] = _resolve_image(session, listing)
            continue
        scan = scans.get(listing.inventory_item_id) if listing.inventory_item_id else None
        result[listing.id] = (scan, True) if scan else (listing.image_url, False)
    return result


def _photo_version(source: str) -> str:
    fingerprint = source
    if not _is_web_url(source):
        try:
            stat = Path(source).stat()
            fingerprint += f":{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            pass
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def _filter_counts(session: Session, q: str, category: str, ebay: str) -> dict[str, int]:
    """How many listings sit behind each tab, for the current search.

    The page opens on All so returning from an edit or publication never
    makes the rest of the inventory appear to have disappeared. Counts
    keep every local-status tab understandable under the current filters.
    """
    return {key: len(session.scalars(_filtered(key, q, category, "all" if key == "sold_out" else ebay)).all()) for key in FILTERS}


def _ebay_state_clauses():
    direct_status = (
        select(EbayListingLink.status)
        .where(EbayListingLink.shop_listing_id == ShopListing.id)
        .correlate(ShopListing)
        .scalar_subquery()
    )
    export_status = (
        select(EbayExportItem.status)
        .where(EbayExportItem.sku == ShopListing.id)
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
        .limit(1)
        .correlate(ShopListing)
        .scalar_subquery()
    )
    live = or_(
        select(EbayListingLink.id).join(EbayListing,
            EbayListing.ebay_listing_id == EbayListingLink.ebay_listing_id).where(
            EbayListingLink.shop_listing_id == ShopListing.id,
            EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
            EbayListing.listing_status == "Active").correlate(ShopListing).exists(),
        select(EbayExportItem.id).join(EbayListing,
            EbayListing.ebay_listing_id == EbayExportItem.ebay_listing_id).where(
            EbayExportItem.sku == ShopListing.id, EbayExportItem.kind == "single",
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            EbayListing.listing_status == "Active").correlate(ShopListing).exists(),
    )
    pending = or_(
        direct_status.in_((EbayListingLinkStatus.PUBLISHING, EbayListingLinkStatus.ENDING)),
        and_(direct_status.is_(None), export_status == EbayExportItemStatus.PENDING),
    )
    attention = or_(
        direct_status == EbayListingLinkStatus.FAILED,
        and_(direct_status.is_(None), export_status == EbayExportItemStatus.FAILED),
    )
    direct_unknown = select(EbayListingLink.id).where(
        EbayListingLink.shop_listing_id == ShopListing.id,
        EbayListingLink.status == EbayListingLinkStatus.ACTIVE,
        ~select(EbayListing.id).where(EbayListing.ebay_listing_id ==
            EbayListingLink.ebay_listing_id).exists()).correlate(ShopListing).exists()
    export_unknown = select(EbayExportItem.id).where(
        EbayExportItem.sku == ShopListing.id, EbayExportItem.kind == "single",
        EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
        ~select(EbayListing.id).where(EbayListing.ebay_listing_id ==
            EbayExportItem.ebay_listing_id).exists()).correlate(ShopListing).exists()
    pending = and_(~live, or_(func.coalesce(pending, False), direct_unknown, export_unknown))
    possible_match = select(EbayListing.id).where(
        EbayListing.title == ShopListing.title,
        EbayListing.listing_status == "Active").correlate(ShopListing).exists()
    attention = and_(~live, ~pending, or_(func.coalesce(attention, False), possible_match))
    return {
        "live": live,
        "pending": pending,
        "attention": attention,
        "not_listed": and_(~live, ~pending, ~attention),
        "unpublished": and_(~live, ~pending, ~possible_match, ShopListing.status.in_(
            (ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE))),
    }


def _filtered(status: str, q: str, category: str = "all", ebay: str = "all"):
    stmt = select(ShopListing)
    out_of_stock = or_(ShopListing.status == ShopListingStatus.SOLD_OUT, ShopListing.quantity <= 0)
    if status == "sold_out":
        stmt = stmt.where(out_of_stock)
    else:
        stmt = stmt.where(~out_of_stock)
    if status not in ("all", "sold_out") and status in {s.value for s in ShopListingStatus}:
        stmt = stmt.where(ShopListing.status == ShopListingStatus(status))
    if q:
        stmt = stmt.where(ShopListing.title.ilike(f"%{q}%"))
    if category in GAME_CATEGORIES:
        stmt = stmt.join(TcgCard, TcgCard.catalog_item_id == ShopListing.catalog_item_id).where(
            TcgCard.game.in_(GAME_CATEGORIES[category])
        )
    elif category == "collectibles":
        stmt = stmt.join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id).where(
            CatalogItem.item_type == CatalogItemType.COLLECTIBLE
        )
    elif category == "other":
        known_games = set().union(*GAME_CATEGORIES.values())
        stmt = (
            stmt.join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .outerjoin(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
            .where(or_(
                CatalogItem.item_type.not_in((CatalogItemType.TCG_CARD, CatalogItemType.COLLECTIBLE)),
                and_(CatalogItem.item_type == CatalogItemType.TCG_CARD, TcgCard.game.not_in(known_games)),
            ))
        )
    if ebay in EBAY_FILTERS and ebay != "all":
        stmt = stmt.where(_ebay_state_clauses()[ebay])
    return stmt


def _page_url(**params: str) -> str:
    return "/shop-listings?" + urlencode({key: value for key, value in params.items() if value})


def _set_names(session: Session, listings: list[ShopListing]) -> dict[str, str]:
    """catalog_item_id -> set name, for the listings on this page only --
    a listing has no set of its own, that's a TcgCard+CardSet join away."""
    item_ids = [listing.catalog_item_id for listing in listings]
    if not item_ids:
        return {}
    rows = session.execute(
        select(TcgCard.catalog_item_id, TcgCard.set_id, CardSet.name)
        .join(CardSet, CardSet.id == TcgCard.set_id, isouter=True)
        .where(TcgCard.catalog_item_id.in_(item_ids))
    ).all()
    return {
        catalog_item_id: name or set_id
        for catalog_item_id, set_id, name in rows
        if name or set_id
    }


def _ebay_links(session: Session, listings: list[ShopListing]) -> dict[str, EbayListingLink]:
    ids = [listing.id for listing in listings]
    if not ids:
        return {}
    rows = session.scalars(select(EbayListingLink).where(EbayListingLink.shop_listing_id.in_(ids))).all()
    return {link.shop_listing_id: link for link in rows}


def _latest_exports(session: Session, listings: list[ShopListing]) -> dict[str, EbayExportItem]:
    ids = [listing.id for listing in listings]
    if not ids:
        return {}
    rows = session.scalars(
        select(EbayExportItem)
        .where(EbayExportItem.sku.in_(ids))
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
    ).all()
    latest: dict[str, EbayExportItem] = {}
    for item in rows:
        latest.setdefault(item.sku, item)
    return latest


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


@dataclass(frozen=True)
class EbayPublicationState:
    key: str
    label: str
    tone: str


def _publication_state(
    ebay_link: EbayListingLink | None, export_item: EbayExportItem | None,
) -> EbayPublicationState:
    if ebay_link:
        if ebay_link.status == EbayListingLinkStatus.ACTIVE:
            return EbayPublicationState("live", "LIVE ON EBAY", "ok")
        if ebay_link.status in (EbayListingLinkStatus.PUBLISHING, EbayListingLinkStatus.ENDING):
            return EbayPublicationState("pending", ebay_link.status.value.upper(), "warn")
        if ebay_link.status == EbayListingLinkStatus.FAILED:
            return EbayPublicationState("attention", "FAILED", "bad")
        return EbayPublicationState("not_listed", "NOT ON EBAY", "neutral")
    if export_item:
        if export_item.status == EbayExportItemStatus.CONFIRMED:
            return EbayPublicationState("live", "LIVE ON EBAY", "ok")
        if export_item.status == EbayExportItemStatus.PENDING:
            return EbayPublicationState("pending", "BULK PENDING", "warn")
        if export_item.status == EbayExportItemStatus.FAILED:
            return EbayPublicationState("attention", "PUBLISH FAILED", "bad")
    return EbayPublicationState("not_listed", "NOT ON EBAY", "neutral")


def _row(listing: ShopListing, set_name: str | None, ebay_link: EbayListingLink | None,
        export_item: EbayExportItem | None, withhold_reason: str = "",
        image_source: str | None = None, is_own_photo: bool = False, publication: EbayPublicationState | None = None) -> str:
    sub = f"{escape(set_name)} · " if set_name else ""
    if image_source:
        version = _photo_version(image_source)
        badge = "<span class='listing-photo-badge own'>Your photo</span>" if is_own_photo \
            else "<span class='listing-photo-badge ref'>Reference art</span>"
        thumb = (
            f"<a class='listing-thumb-link' href='/shop-listings/{escape(listing.id)}/image?v={version}' target='_blank' rel='noopener'>"
            f"<img class='listing-thumb' src='/shop-listings/{escape(listing.id)}/thumb?v={version}' loading='lazy' alt=''>{badge}</a>"
        )
    else:
        thumb = "<div class='listing-thumb listing-thumb-empty'></div>"
    reason_html = f"<div class='listing-reason'>{escape(withhold_reason)}</div>" if withhold_reason else ""
    publication = publication or _publication_state(ebay_link, export_item)
    studio_label = {
        "live": "Improve listing",
        "attention": "Fix & retry",
        "pending": "Check publication",
    }.get(publication.key, "Review & Publish")
    actions = [
        # Correcting a wrong title or price used to mean editing the
        # database by hand -- a real listing was found live at ~9.6x its
        # correct price for a week because nothing else could fix it.
        # First in the list, not gated on status: a withdrawn or sold-out
        # listing's data can be just as wrong as an active one's.
        f"<a class='btn small ghost' href='/shop-listings/{escape(listing.id)}/edit'>Edit</a>",
        # One canonical place to edit the channel-specific copy, manage the
        # exact photos and see the buyer-facing result. It remains useful
        # after a listing is live because saving a proposal here is local
        # only; a separate revision approval will handle the eBay write.
        f"<a class='btn small' href='/shop-listings/{escape(listing.id)}/ebay-preview'>{studio_label}</a>",
    ]
    if listing.status == ShopListingStatus.DRAFT:
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/activate'>"
            "<button class='btn small'>Enable in EZBay</button></form>"
        )
    if listing.status in (ShopListingStatus.DRAFT, ShopListingStatus.ACTIVE):
        actions.append(
            f"<form method='post' action='/shop-listings/{escape(listing.id)}/withdraw' "
            # Static confirm text, not the listing's own title: a title
            # containing an apostrophe (a real card name can, e.g.
            # "Farfetch'd") would break out of the JS string literal here
            # if interpolated -- same reasoning business_records.py's own
            # confirm() dialogs already follow.
            "onsubmit=\"return confirm('Hide this record in EZBay? This does not end an eBay listing.');\">"
            "<button class='btn small ghost'>Hide in EZBay</button></form>"
        )
    if publication.key in ("not_listed", "attention") or (
        ebay_link and ebay_link.status == EbayListingLinkStatus.PUBLISHING
    ):
        actions.append("<a class='btn small ghost' href='/ebay-export'>Bulk Upload</a>")
    status_pill = pill('EZBAY: ' + ('ENABLED' if listing.status == ShopListingStatus.ACTIVE else listing.status.value.replace('_', ' ').upper()), 'neutral')
    ebay_pill = pill(publication.label, publication.tone)
    mobile_meta = (
        f"<div class='listing-mobile-meta'>Qty {listing.quantity} · "
        f"Saved price {_money(listing.price)} {escape(listing.currency)}<br>{ebay_pill} {status_pill}</div>"
    )
    return (
        "<tr>"
        f"<td>{thumb}</td>"
        f"<td><strong>{escape(listing.title)}</strong><div class='muted'>{sub}{escape(listing.condition or 'Ungraded')}</div>{reason_html}{mobile_meta}</td>"
        f"<td>{listing.quantity}</td>"
        f"<td>{_money(listing.price)} {escape(listing.currency)}</td>"
        f"<td>{status_pill}</td>"
        f"<td>{ebay_pill}</td>"
        f"<td class='listing-actions'>{actions[1]}<details class='listing-more'><summary>More options</summary>{actions[0]}{''.join(actions[2:])}</details></td>"
        "</tr>"
    )


# --- images -----------------------------------------------------------

@router.get("/shop-listings/{listing_id}/thumb")
def shop_listing_thumb(listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Serve the gallery cover, falling back only for an unset gallery."""
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
    return serve_thumb(path, f"{listing_id}-{_photo_version(source)}-{THUMB_WIDTH}.jpg", THUMB_WIDTH)


@router.get("/shop-listings/{listing_id}/image")
def shop_listing_image(listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Open the same current gallery photo used by the overview thumbnail."""
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
    status: str = Query("all"), q: str = Query(""), category: str = Query("all"),
    ebay: str = Query("unpublished"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    status = status if status in FILTERS else "all"
    if status in ("withdrawn", "sold_out") and ebay == "unpublished":
        ebay = "all"
    category = category if category in CATEGORY_FILTERS else "all"
    ebay = ebay if ebay in EBAY_FILTERS else "unpublished"
    if status == "sold_out":
        ebay = "all"
    query = q.strip()
    out_of_stock_count = len(session.scalars(_filtered("sold_out", query, category, "all")).all())
    stock_section = (
        f"<p><a class='btn small ghost' href='{escape(_page_url(status='sold_out', ebay='all', category=category, q=query), quote=True)}'>"
        f"Out of stock ({out_of_stock_count})</a></p>"
        if status != "sold_out" else
        "<p class='muted'>Sold-out and zero-quantity records are kept here, separate from current listings.</p>"
        "<p><a class='btn small ghost' href='/shop-listings?status=all&amp;ebay=all'>Back to current listings</a></p>"
    )
    # counted separately from the capped page of rows below, so "Activate
    # all" can say -- and act on -- the true total, not just what's shown
    count = len(session.scalars(_filtered(status, query, category, ebay)).all())
    listings = session.scalars(
        _filtered(status, query, category, ebay).order_by(ShopListing.price.desc()).limit(200)
    ).all()
    set_names = _set_names(session, listings)
    ebay_links = _ebay_links(session, listings)
    export_items = _latest_exports(session, listings)
    withhold_reasons = _withhold_reasons(session, listings)
    images = _resolve_images(session, listings)

    states = {}
    for key, clause in _ebay_state_clauses().items():
        if key == "unpublished":
            continue
        for listing_id in session.scalars(select(ShopListing.id).where(
                ShopListing.id.in_([x.id for x in listings]), clause)):
            states[listing_id] = {
                "live": EbayPublicationState("live", "LIVE ON EBAY", "ok"),
                "pending": EbayPublicationState("pending", "EBAY UPDATE PENDING", "warn"),
                "attention": EbayPublicationState("attention", "NEEDS ATTENTION", "bad"),
                "not_listed": EbayPublicationState("not_listed", "NOT ON EBAY", "neutral"),
            }[key]

    # Exact-title matches are review hints, never proof that two stock records
    # represent the same physical item.
    live_titles = set(session.scalars(select(EbayListing.title).where(
        EbayListing.listing_status == "Active")))
    for listing in listings:
        if states[listing.id].key == "attention" and listing.title in live_titles:
            withhold_reasons[listing.id] = "An eBay listing has this title. Check the link before publishing another copy."

    rows = "".join(
        _row(listing, set_names.get(listing.catalog_item_id), ebay_links.get(listing.id),
            export_items.get(listing.id), withhold_reasons.get(listing.id, ""),
            *images.get(listing.id, (None, False)), publication=states[listing.id])
        for listing in listings
    ) or "<tr><td colspan='7' class='muted'>Nothing here.</td></tr>"

    counts = _filter_counts(session, query, category, ebay)
    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' "
        f"href='{escape(_page_url(status=key, q=query, category=category, ebay=ebay), quote=True)}'>{escape(label)}"
        f"<span class='filter-count'>{counts.get(key, 0)}</span></a>"
        for key, label in FILTERS.items()
    )

    ebay_counts = {key: len(session.scalars(_filtered("all", query, category, key)).all())
                   for key in EBAY_FILTERS}
    ebay_bar = "".join(
        f"<a class='filter-tab{' active' if key == ebay else ''}' "
        f"href='{escape(_page_url(status="all", q=query, category=category, ebay=key), quote=True)}'>{escape(label)} <span class='filter-count'>{ebay_counts[key]}</span></a>"
        for key, label in EBAY_FILTERS.items() if key != "not_listed"
    )

    category_options = "".join(
        f"<option value='{key}'{' selected' if key == category else ''}>{escape(label)}</option>"
        for key, label in CATEGORY_FILTERS.items()
    )
    status_options = "".join(
        f"<option value='{key}'{' selected' if key == status else ''}>"
        f"{escape(label)} ({counts.get(key, 0)})</option>"
        for key, label in FILTERS.items()
    )
    ebay_options = "".join(
        f"<option value='{key}'{' selected' if key == ebay else ''}>{escape(label)}</option>"
        for key, label in EBAY_FILTERS.items()
    )

    search_form = (
        "<form method='get' action='/shop-listings' class='listing-search'>"
        f"<input type='hidden' name='status' value='{escape(status)}'>"
        f"<input type='hidden' name='ebay' value='{escape(ebay)}'>"
        f"<select name='category' aria-label='Category'>{category_options}</select>"
        f"<input name='q' value='{escape(query)}' placeholder='Search by title…'>"
        "<button class='btn small'>Apply</button>"
        + (f"<a class='btn small ghost' href='{escape(_page_url(status=status, ebay=ebay), quote=True)}'>Clear</a>"
           if query or category != "all" else "")
        + "</form>"
    )

    active_filters = " · ".join((FILTERS[status], EBAY_FILTERS[ebay], CATEGORY_FILTERS[category]))
    if query:
        active_filters += f" · “{query}”"
    mobile_filters = (
        "<details class='listing-mobile-filters'>"
        "<summary><span class='mobile-filter-title'>Filters</span>"
        f"<span class='mobile-filter-summary'>{escape(active_filters)}</span></summary>"
        "<form method='get' action='/shop-listings' class='mobile-filter-form'>"
        "<label>EZBay workflow (internal)"
        f"<select name='status'>{status_options}</select></label>"
        "<label>eBay status"
        f"<select name='ebay'>{ebay_options}</select></label>"
        "<label>Category"
        f"<select name='category'>{category_options}</select></label>"
        "<label>Card title"
        f"<input name='q' value='{escape(query, quote=True)}' placeholder='Search by title…'></label>"
        "<div class='mobile-filter-actions'><button class='btn'>Show results</button>"
        "<a class='btn ghost' href='/shop-listings?status=all&amp;ebay=unpublished&amp;category=all'>Reset</a>"
        "</div></form></details>"
    )

    bulk_action = ""
    if status == "draft" and count:
        bulk_url = _page_url(status="draft", q=query, category=category, ebay=ebay).replace(
            "/shop-listings?", "/shop-listings/activate-all?", 1
        )
        bulk_action = (
            f"<form class='listing-bulk-action' method='post' action='{escape(bulk_url, quote=True)}'>"
            f"<button class='btn'>Enable {count} draft(s) in EZBay{' matching this search' if query else ''}</button>"
            "</form>"
        )

    note = (
        f"<p class='subtitle'>{count} listing(s){' shown, capped at 200 rows -- narrow your search to see the rest' if count > 200 else ''}.</p>"
    )

    body = (
        brand_header("Out of stock" if status == "sold_out" else "Listing Studio")
        + note
        + stock_section
        + "<p class='muted'>Live on eBay is confirmed by the latest eBay sync. EZBay enabled is an internal workflow status. Saved prices may include unpublished edits.</p>"
        + f"<nav aria-label='eBay publication status' class='filter-tabs listing-primary-tabs'>{ebay_bar}</nav>"
        + "<div class='listing-desktop-filters'>"
        + f"<div class='listing-toolbar'>{search_form}</div>"
        + "<details><summary>EZBay workflow filters</summary>"
        + f"<div class='filter-tabs'>{filter_bar}</div></details></div>"
        + mobile_filters
        + bulk_action
        + "<div class='panel listing-results'><div class='table-wrap'><table class='listing-table'><thead><tr>"
        + "<th></th><th>Item</th><th>Qty</th><th>Saved price</th><th>EZBay workflow</th><th>eBay status</th><th></th>"
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
    return RedirectResponse("/shop-listings", status_code=303)


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
        version = _photo_version(image_source)
        badge = "<span class='listing-photo-badge own'>Your photo</span>" if is_own_photo \
            else "<span class='listing-photo-badge ref'>Reference art — not a photo of your card</span>"
        photo_html = (
            f"<a class='edit-photo-link' href='/shop-listings/{escape(listing.id)}/image?v={version}' target='_blank' rel='noopener'>"
            f"<img class='edit-photo' src='/shop-listings/{escape(listing.id)}/thumb?v={version}' loading='lazy' alt=''></a>{badge}"
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
        + "<a class='btn small ghost' href='/shop-listings'>Cancel</a>"
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
    return RedirectResponse("/shop-listings", status_code=303)


@router.post("/shop-listings/activate-all")
def activate_all_drafts(
    status: str = Query("draft"), q: str = Query(""), category: str = Query("all"),
    ebay: str = Query("all"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    query = q.strip()
    category = category if category in CATEGORY_FILTERS else "all"
    ebay = ebay if ebay in EBAY_FILTERS else "all"
    listings = session.scalars(_filtered("draft", query, category, ebay)).all()
    count = 0
    for listing in listings:
        listing.status = ShopListingStatus.ACTIVE
        count += 1
    if count:
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="shop_listing.activate_all",
                    resource_type="shop_listing", resource_id="bulk", outcome="success",
                    correlation_id=str(uuid.uuid4()), details={"count": count, "query": query})
    return RedirectResponse("/shop-listings", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.filter-count{margin-left:6px;opacity:.62;font-variant-numeric:tabular-nums;font-weight:500}
.listing-filter-label{margin:8px 0 5px;font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.06em;color:var(--text-dim)}
.listing-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
.listing-search{display:flex;gap:8px;flex-wrap:wrap}
.listing-search input,.listing-search select{padding:7px 10px;border-radius:6px;border:1px solid var(--panel-border);
  font-size:13px;background:var(--surface-sunken);color:var(--text)}
.listing-search input{width:220px}
.listing-mobile-filters{display:none}
.listing-bulk-action{margin:0 0 14px}
.listing-actions{display:flex;gap:6px;flex-wrap:wrap}
.listing-more summary{cursor:pointer;padding:8px;font-size:13px}
.listing-more[open]{display:grid;gap:8px}
.listing-primary-tabs{margin-top:18px}
.listing-primary-tabs .filter-tab{min-height:44px;display:flex;align-items:center}
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
  .listing-desktop-filters { display: none; }
  .listing-mobile-filters {
    display: block;
    margin: 0 0 14px;
    border: 1px solid var(--panel-border);
    border-radius: 12px;
    background: var(--panel);
    overflow: hidden;
  }
  .listing-mobile-filters summary {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    min-height: 52px;
    padding: 10px 14px;
    cursor: pointer;
    list-style: none;
  }
  .listing-mobile-filters summary::-webkit-details-marker { display: none; }
  .listing-mobile-filters summary::after {
    content: '▼';
    color: var(--text-dim);
    transition: transform .15s ease;
  }
  .listing-mobile-filters[open] summary::after { transform: rotate(180deg); }
  .mobile-filter-title { font-weight: 750; color: var(--text); }
  .mobile-filter-summary {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-dim);
    font-size: 12px;
    text-align: right;
  }
  .mobile-filter-form {
    display: grid;
    gap: 12px;
    padding: 14px;
    border-top: 1px solid var(--panel-border);
  }
  .mobile-filter-form label {
    display: grid;
    gap: 5px;
    color: var(--text-dim);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .05em;
    text-transform: uppercase;
  }
  .mobile-filter-form select, .mobile-filter-form input {
    width: 100%;
    min-width: 0;
    min-height: 46px;
    padding: 9px 11px;
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    background: var(--surface-sunken);
    color: var(--text);
    font-size: 16px;
    text-transform: none;
    letter-spacing: normal;
  }
  .mobile-filter-actions {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
    gap: 8px;
  }
  .mobile-filter-actions .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    width: 100%;
    text-align: center;
  }
  .listing-bulk-action, .listing-bulk-action .btn { width: 100%; }
  .listing-results {
    padding: 0;
    border: 0;
    background: transparent;
    box-shadow: none;
  }
  .listing-results .table-wrap { overflow: visible; }
  .listing-table, .listing-table tbody { display: block; width: 100%; }
  .listing-table thead { display: none; }
  .listing-table tr {
    display: grid;
    grid-template-columns: 66px minmax(0, 1fr);
    column-gap: 14px;
    row-gap: 10px;
    width: 100%;
    margin-bottom: 12px;
    padding: 16px;
    border: 1px solid var(--panel-border);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow);
  }
  .listing-table td { display: block; min-width: 0; padding: 0; border: 0; }
  .listing-table td:first-child { grid-column: 1; grid-row: 1; }
  .listing-table td:nth-child(2) { grid-column: 2; grid-row: 1; }
  .listing-table th:nth-child(3), .listing-table td:nth-child(3),
  .listing-table th:nth-child(4), .listing-table td:nth-child(4),
  .listing-table th:nth-child(5), .listing-table td:nth-child(5),
  .listing-table th:nth-child(6), .listing-table td:nth-child(6) { display: none; }
  .listing-table td:nth-child(2) strong {
    display: block;
    font-size: 15px;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  .listing-table td:nth-child(2) .muted { margin-top: 4px; line-height: 1.45; }
  .listing-mobile-meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px;
    margin-top: 9px;
    font-size: 12px;
    line-height: 1.5;
    color: var(--text-dim);
  }
  .listing-reason { max-width: none; }
  .listing-thumb, .listing-thumb-empty { width: 66px; height: 90px; border-radius: 8px; }
  .listing-thumb-link { width: 66px; }
  .listing-photo-badge { margin-top: 4px; font-size: 9px; padding: 2px 3px; }
  .listing-actions {
    grid-column: 1 / -1;
    grid-row: 2;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    padding-top: 12px !important;
    border-top: 1px solid var(--panel-border) !important;
  }
  .listing-actions form { width: 100%; }
  .listing-actions .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    min-height: 44px;
    padding: 8px 10px;
    white-space: normal;
    text-align: center;
    line-height: 1.25;
  }
  .listing-edit-panel { display: block; }
  .edit-photo-link { width: min(220px, 100%); margin: 0 auto 18px; }
  .edit-photo, .edit-photo-empty { width: 100%; height: auto; }
  .listing-edit-panel .listing-photo-badge { width: 100%; }
  .listing-edit-form { max-width: none; width: 100%; }
  .listing-edit-form .field input, .listing-edit-form .field textarea { min-height: 44px; font-size: 16px; }
  .listing-edit-actions { display: grid; grid-template-columns: 1fr; }
  .listing-edit-actions .btn { width: 100%; min-height: 44px; text-align: center; }
}
</style>"""
