"""Turns priced, approved CapturedCard rows into eBay-ready listing drafts
for human review -- the DB-driven counterpart to the Excel pipeline's
`export` stage, except it stops at "here's what would get listed" instead
of writing a CSV. Nothing here calls eBay; the actual Sell API write lives
in listing_pipeline/publish.py and is wired up separately (see that
module's docstring for the approval contract this queue feeds into).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.r2 import R2UploadError, upload_card_image
from .approvals import decide_approval
from .audit import record_event
from .auth import require_dashboard_user
from .classification import load_pipeline_config
from .database import SessionLocal, get_session
from .listing_pipeline import ingest_db, render
from .models import Approval, CapturedCard, ListingBuildStatus, PendingListing, PricingStatus
from .ui import brand_header, page

router = APIRouter(prefix="/listings", tags=["listings"])


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _card_image_urls(card_ids: tuple[str, ...], cards_by_id: dict[str, CapturedCard], settings) -> list[str]:
    urls: list[str] = []
    for card_id in card_ids:
        card = cards_by_id[card_id]
        for side, local_path in (("front", card.front_image_path), ("back", card.back_image_path)):
            if not local_path:
                continue
            key = f"captured/{card_id}-{side}.jpg"
            urls.append(
                upload_card_image(
                    local_path,
                    key,
                    endpoint_url=settings.r2_endpoint_url,
                    access_key_id=settings.r2_access_key_id,
                    secret_access_key=settings.r2_secret_access_key,
                    bucket_name=settings.r2_bucket_name,
                    public_base_url=load_pipeline_config().images.base_url,
                )
            )
    return urls


def _run_build_listings(user: str, settings) -> None:
    """Background task: build PendingListing drafts from every PRICE_APPROVED
    card that isn't already part of one. Uses its own DB session, same
    reasoning as _run_price_check in price_review.py.
    """
    with SessionLocal() as session:
        if not all([settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
            record_event(session, actor_type="user", actor_id=user, action="listing.build", resource_type="pending_listing", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": "R2 image hosting is not configured"})
            return

        try:
            pipeline_config = load_pipeline_config()
        except Exception as exc:
            record_event(session, actor_type="user", actor_id=user, action="listing.build", resource_type="pending_listing", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": f"could not load pipeline config: {exc}"})
            return

        already_listed_ids = {card_id for row in session.scalars(select(PendingListing)) for card_id in row.card_ids}
        approved_cards = [
            card
            for card in session.scalars(select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PRICE_APPROVED))
            if card.id not in already_listed_ids
        ]
        if not approved_cards:
            return

        cards_by_id = {card.id: card for card in approved_cards}
        existing_labels = list(session.scalars(select(PendingListing.custom_label)))
        listings = ingest_db.build_listings(approved_cards, pipeline_config, existing_labels)
        rendered = render.render_all(listings, pipeline_config)

        for item in rendered:
            listing = item.listing
            try:
                image_urls = _card_image_urls(listing.card_ids, cards_by_id, settings)
            except R2UploadError as exc:
                record_event(session, actor_type="user", actor_id=user, action="listing.build", resource_type="pending_listing", resource_id=listing.custom_label, outcome="failed", correlation_id=_correlation_id(), details={"error": str(exc)})
                continue  # cards stay unlinked to any listing, so they're retried on the next build

            session.add(
                PendingListing(
                    custom_label=listing.custom_label,
                    title=item.title,
                    description_html=item.description_html,
                    list_price=listing.list_price,
                    floor_price=listing.floor_price,
                    category_id=pipeline_config.ebay.category_id,
                    condition_id=pipeline_config.ebay.condition_id,
                    image_urls=image_urls,
                    card_ids=list(listing.card_ids),
                )
            )
            session.commit()
            record_event(session, actor_type="user", actor_id=user, action="listing.build", resource_type="pending_listing", resource_id=listing.custom_label, outcome="success", correlation_id=_correlation_id(), details={"card_count": len(listing.cards)})


@router.post("/build")
def listings_build(request: Request, background_tasks: BackgroundTasks, user: str = Depends(require_dashboard_user)):
    settings = request.app.state.settings
    background_tasks.add_task(_run_build_listings, user, settings)
    return RedirectResponse("/listings/review?building=1", status_code=303)


@router.get("/review")
def listings_list(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    drafts = session.scalars(select(PendingListing).where(PendingListing.status == ListingBuildStatus.DRAFT).order_by(PendingListing.created_at)).all()
    rows = "".join(
        f"<li><a href='/listings/review/{d.id}'>{escape(d.custom_label)} — {escape(d.title)} — AUD ${d.list_price}</a></li>" for d in drafts
    ) or "<li>No listings waiting for review.</li>"

    head_extra = "<meta http-equiv='refresh' content='4'>" if request.query_params.get("building") else ""
    note = "<p class='subtitle'>Building listings in the background -- this page refreshes automatically.</p>" if request.query_params.get("building") else ""

    body = (
        brand_header("Listing review")
        + note
        + f"<div class='panel'><h2>Draft listings ({len(drafts)})</h2><ul class='events'>{rows}</ul>"
        + "<form method='post' action='/listings/build'><button>Build from approved cards</button></form></div>"
    )
    return HTMLResponse(page("EzBay — Listing review", body, head_extra=head_extra))


@router.get("/review/{listing_id}")
def listing_review_form(listing_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    listing = session.get(PendingListing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="listing not found")

    images_html = "".join(f"<img src='{escape(url)}' style='max-width:180px;border-radius:8px;margin:4px'>" for url in listing.image_urls)
    body = (
        brand_header("Listing review")
        + "<div class='panel'>"
        + f"<h2>{escape(listing.custom_label)}</h2>"
        + f"<p>{images_html}</p>"
        + f"<form method='post' action='/listings/review/{listing_id}'>"
        + f"<label>Title <input name='title' value='{escape(listing.title)}' maxlength='80' required></label>"
        + f"<label>List price (AUD) <input name='list_price' value='{listing.list_price}' required></label>"
        + f"<label>Floor price (AUD) <input name='floor_price' value='{listing.floor_price}' required></label>"
        + f"<div class='panel' style='margin:12px 0'>{listing.description_html}</div>"
        + "<div style='display:flex;gap:12px'>"
        + "<button name='action' value='approve' style='background:linear-gradient(120deg,var(--success),var(--accent))'>Approve</button>"
        + "<button name='action' value='reject' style='background:var(--danger);color:#fff'>Reject</button>"
        + "</div></form></div>"
    )
    return HTMLResponse(page("EzBay — Listing review", body))


@router.post("/review/{listing_id}")
def listing_review_submit(
    listing_id: str,
    action: str = Form("approve"),
    title: str = Form(""),
    list_price: str = Form(""),
    floor_price: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    listing = session.get(PendingListing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="listing not found")

    if action == "reject":
        listing.status = ListingBuildStatus.REJECTED
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="listing.reject", resource_type="pending_listing", resource_id=listing_id, outcome="success", correlation_id=_correlation_id(), details={})
        return RedirectResponse("/listings/review", status_code=303)

    try:
        listing.title = title[:80]
        listing.list_price = Decimal(list_price)
        listing.floor_price = Decimal(floor_price)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="price must be a valid number") from None
    if listing.list_price <= 0 or listing.floor_price <= 0:
        raise HTTPException(status_code=400, detail="prices must be greater than zero")

    # A human clicking Approve here *is* the approval decision -- same
    # pattern as /cards/review and /cards/pricing. The actual eBay Sell API
    # write (listing_pipeline.publish.publish) is wired up separately and,
    # per that module's contract, only ever runs against an Approval whose
    # status is already APPROVED -- it is not called from this route yet.
    approval = Approval(requested_at=datetime.now(UTC), requested_by=user, action="ebay.publish_listing", arguments={"pending_listing_id": listing_id, "custom_label": listing.custom_label})
    session.add(approval)
    session.commit()
    decide_approval(session, approval, approved=True, actor=user)

    listing.status = ListingBuildStatus.APPROVED
    listing.approval_id = approval.id
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="listing.approve", resource_type="pending_listing", resource_id=listing_id, outcome="success", correlation_id=_correlation_id(), details={"approval_id": approval.id})

    return RedirectResponse("/listings/review", status_code=303)
