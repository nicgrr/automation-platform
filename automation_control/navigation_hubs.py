"""Small workflow entrances for EzBay's condensed navigation.

The underlying tools intentionally keep their original routes. These pages
organise them without adding a second application layer or proxying their
behaviour, which means saved links, forms and browser history keep working.
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import (
    ContactMessage,
    EbayExportBatch,
    EbayListing,
    MarketListing,
    PurchaseLot,
    ReleaseCalendarEntry,
    ShopListing,
    ShopListingStatus,
    ShopOrder,
    Supplier,
)
from .market_watch_service import allowed_country_codes
from .nav_counts import queue_counts
from .ui import brand_header, page, pill

router = APIRouter(tags=["navigation"])


def _card(href: str, title: str, description: str, *, status: str = "", kind: str = "neutral",
          external: bool = False) -> str:
    external_attrs = " target='_blank' rel='noopener'" if external else ""
    return (
        f"<a class='hub-card' href='{escape(href, quote=True)}'{external_attrs}>"
        "<div class='hub-card-head'>"
        f"<h2>{escape(title)}</h2>{pill(escape(status), kind) if status else ''}</div>"
        f"<p>{escape(description)}</p><span class='hub-open'>Open <b>&rarr;</b></span></a>"
    )


def _section(title: str, introduction: str, cards: list[str]) -> str:
    return (
        "<section class='hub-section'>"
        f"<div class='hub-section-head'><h2>{escape(title)}</h2><p>{escape(introduction)}</p></div>"
        f"<div class='hub-grid'>{''.join(cards)}</div></section>"
    )


def _render(title: str, subtitle: str, sections: list[str]) -> HTMLResponse:
    body = (
        brand_header(title)
        + f"<p class='hub-intro'>{escape(subtitle)}</p>"
        + "".join(sections)
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {title}", body))


def _count(session: Session, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    if criteria:
        stmt = stmt.where(*criteria)
    return int(session.scalar(stmt) or 0)


@router.get("/scan-add", response_class=HTMLResponse)
def scan_and_add(user: str = Depends(require_dashboard_user),
                 session: Session = Depends(get_session)) -> HTMLResponse:
    return _render(
        "Add stock",
        "Upload photos, review the identity and existing stock, then continue to Listing Studio.",
        [
            _section("Photo upload", "One entry point for cards, graded slabs and collectibles. Keep each item's front and back together.", [
                _card("/ebay-listing-bulk-photos", "Upload photos", "Add new items or update existing photos. Slabs require their company, grade and certificate; collectibles can reuse existing stock."),
                _card("/shop-listings", "Listing Studio", "Continue with saved drafts, quantities and price review."),
            ]),
            _section("Specialist tools", "Use these only for scanner sheets, supplies or a product link.", [
                _card("/feed", "Scan feed", "Review flatbed scanner sheets."),
                _card("/accessories/capture/bulk", "Upload supplies", "Sleeves, binders and card supplies."),
                _card("/collectibles/identify", "Identify from a link", "Find collectible details from a product URL."),
                _card("/review-centre", "Earlier uploads", "Resume items captured through the older tools."),
            ]),
        ],
    )


@router.get("/review-centre", response_class=HTMLResponse)
def review_centre(request: Request, user: str = Depends(require_dashboard_user),
                  session: Session = Depends(get_session)) -> HTMLResponse:
    counts = queue_counts(session, request.app.state.settings)

    def work_card(href: str, title: str, description: str) -> str:
        count = counts[href]
        if count is None:
            return _card(href, title, description, status="Scan required", kind="neutral")
        if count:
            return _card(href, title, description, status=f"{count} waiting", kind="warn")
        return _card(href, title, description, status="Clear", kind="ok")

    total = counts["/review-centre"]
    summary = (
        f"{total} item{'s' if total != 1 else ''} currently need attention."
        if total is not None else
        "Known queues are clear; run the image audits when you want to check for new candidates."
    )
    return _render(
        "Review centre",
        summary,
        [
            _section("Scanning and identification", "Correct items that could not be confidently resolved during intake.", [
                work_card("/review", "Scan crops", "Match held flatbed crops to the correct card and set."),
                work_card("/cards/review", "Photographed cards", "Approve cards captured from phone or uploaded photos."),
                work_card("/collectibles/review", "Photographed collectibles", "Approve and catalogue collectible captures."),
                work_card("/accessories/review", "Photographed supplies", "Approve photographed supply products."),
            ]),
            _section("Quality and pricing", "Review classifications, suspicious photos and missing market values.", [
                work_card("/foil-review", "Foil review", "Confirm Normal, Holo or Reverse Holo candidates."),
                work_card("/inventory-audit", "Photo audit", "Resolve inventory photos that may show the wrong card."),
                work_card("/cards/pricing", "Card price review", "Approve, retry or manually enter card prices."),
            ]),
        ],
    )


@router.get("/ebay", response_class=HTMLResponse)
def ebay_hub(user: str = Depends(require_dashboard_user),
             session: Session = Depends(get_session)) -> HTMLResponse:
    drafts = _count(session, ShopListing, ShopListing.status == ShopListingStatus.DRAFT)
    live = _count(session, EbayListing)
    batches = _count(session, EbayExportBatch)
    return _render(
        "eBay",
        "Prepare listings, compare them with the live eBay account, and track every bulk-upload file from one place.",
        [
            _section("Listing workflow", "EzBay never publishes automatically; these screens keep preparation and live state distinct.", [
                _card("/shop-listings?status=draft", "Sellable drafts", "Review each listing, then publish it directly to eBay or include it in a bulk-upload file.", status=f"{drafts} total"),
                _card("/ebay-listing-cleanup", "Listing cleanup", "Review the titles, descriptions, photos and CRM links for everything currently active on eBay."),
                _card("/ebay-listings", "Live listings", "Refresh and inspect the listings eBay reports as live.", status=f"{live} mirrored"),
                _card("/ebay-export", "Bulk upload", "Build CSV files, import eBay results and prevent duplicate exports.", status=f"{batches} exports"),
            ]),
        ],
    )


@router.get("/nl-vault", response_class=HTMLResponse)
def nl_vault_hub(request: Request, user: str = Depends(require_dashboard_user),
                 session: Session = Depends(get_session)) -> HTMLResponse:
    listings = _count(session, ShopListing)
    orders = _count(session, ShopOrder)
    unread = _count(session, ContactMessage, ContactMessage.is_read.is_(False))
    storefront_url = f"{request.url.scheme}://{request.url.hostname}:8444/"
    return _render(
        "NL Vault shop",
        "Manage EzBay's own storefront separately from eBay while keeping inventory connected.",
        [
            _section("Catalogue and pricing", "Control what customers see and what each listing costs.", [
                _card("/shop-listings", "Shop listings", "Edit listing titles, descriptions, quantities and status.", status=f"{listings} total"),
                _card("/shop-pricing", "Shop pricing", "Review and update prices for active storefront items."),
            ]),
            _section("Customers", "Process purchases and respond to storefront enquiries.", [
                _card("/shop-orders", "Shop orders", "Review payment, fulfilment, shipping and tracking.", status=f"{orders} total"),
                _card("/contact-messages", "Contact messages", "Read enquiries submitted through the storefront.", status=f"{unread} unread", kind="warn" if unread else "ok"),
                _card(storefront_url, "Open public shop", "View the customer-facing NL Vault storefront.", external=True),
            ]),
        ],
    )


@router.get("/market-tools", response_class=HTMLResponse)
def market_tools(request: Request, user: str = Depends(require_dashboard_user),
                 session: Session = Depends(get_session)) -> HTMLResponse:
    allowed = allowed_country_codes(request.app.state.settings.market_allowed_countries)
    watched = _count(
        session, MarketListing, MarketListing.active.is_(True), MarketListing.country.in_(allowed),
    )
    releases = _count(session, ReleaseCalendarEntry)
    purchase_lots = _count(session, PurchaseLot)
    suppliers = _count(session, Supplier)
    return _render(
        "Market tools",
        "Research current listings and purchase economics. These are comparison tools, not automated purchasing or investment advice.",
        [
            _section("Find and compare", "Use live listings, monitored products or a listing screenshot.", [
                _card("/market-search", "Market search", "Search current eBay listings from text, a URL or screenshot."),
                _card("/market-watch", "Collectible watch", "Monitor saved searches, marketplace alerts and landed prices across Australia and Japan.", status=f"{watched} listings"),
                _card("/deal-check", "Deal check", "Extract a listing and compare its products with EzBay values."),
            ]),
            _section("Plan a purchase", "Calculate affordability, costs and timing before buying manually.", [
                _card("/buying-calculator", "Buying calculator", "Calculate fees, break-even, margin and maximum buy price."),
                _card("/release-calendar", "Release calendar", "Track preorder dates, deadlines, releases, quantities and costs.", status=f"{releases} releases"),
                _card("/retailer-goals", "Retailer Goals", "Track retailer loyalty milestones, eligible spend and purchase recommendations."),
                _card("/purchase-lots", "Purchase lots", "Evaluate a deal, allocate its final cost and receive catalogue-linked stock.", status=f"{purchase_lots} lots"),
                _card("/suppliers", "Suppliers", "Maintain catalogue-linked wholesale products and start supplier purchase lots.", status=f"{suppliers} suppliers"),
            ]),
        ],
    )


_STYLE = """<style>
.hub-intro{max-width:760px;color:var(--text-dim);font-size:14px;margin:-16px 0 30px}
.hub-section{margin:0 0 30px}.hub-section-head{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:12px}
.hub-section-head h2{font-size:17px;margin:0}.hub-section-head p{color:var(--text-dim);font-size:12px;max-width:560px;margin:0;text-align:right}
.hub-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}
.hub-card{display:flex;flex-direction:column;min-height:145px;background:var(--panel);border:1px solid var(--panel-border);border-radius:var(--radius);padding:17px;color:var(--text);box-shadow:var(--shadow);text-decoration:none}
.hub-card:hover{border-color:rgba(var(--accent-rgb),.45);box-shadow:var(--shadow-lift);text-decoration:none;transform:translateY(-1px)}
.hub-card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}.hub-card h2{font-size:14px;margin:0}.hub-card p{font-size:12px;color:var(--text-dim);margin:10px 0 14px;line-height:1.5}
.hub-open{margin-top:auto;font-size:12px;font-weight:700;color:var(--accent)}.hub-open b{font-size:15px}
@media(max-width:800px){.hub-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.hub-section-head{align-items:flex-start;flex-direction:column;gap:4px}.hub-section-head p{text-align:left}}
@media(max-width:520px){.hub-grid{grid-template-columns:1fr}.hub-card{min-height:128px}.hub-intro{margin-bottom:24px}}
</style>"""
