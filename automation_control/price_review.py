import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.currency import CurrencyLookupError, get_usd_to_aud_rate, usd_to_aud
from .adapters.pokemontcg import PriceLookupError, PriceNotFound, lookup_price
from .audit import record_event
from .auth import require_dashboard_user
from .database import SessionLocal, get_session
from .models import CapturedCard, CardCaptureStatus, PricingStatus
from .ui import brand_header, page

router = APIRouter(prefix="/cards/pricing", tags=["pricing"])

PACING_DELAY_SECONDS = 1.0

# Guards against two overlapping "Check prices" runs stepping on each other:
# without it, a second click while the first run is still in progress opens
# a second background task that re-reads the same not-yet-priced cards and
# can commit stale field values over the first run's results.
#
# This is a time-bounded guard, not a plain lock: a run older than
# STALE_RUN_SECONDS is treated as wedged (crashed thread, hung network call
# that ignored its own timeout, etc) rather than a permanent block on every
# future "Check prices" click until the whole process restarts. Worst-case
# time to fully process one card is roughly 4 search strategies x 3 retries
# x up to 10s backoff each ~= 2 minutes; 5 minutes gives real headroom above
# that before assuming a run is dead. Each run gets a unique id so a stale
# run that eventually does wake up and finish can't clear a newer run's
# in-progress state out from under it.
_price_check_guard = threading.Lock()
_price_check_run_id: str | None = None
_price_check_started_at: float | None = None
STALE_RUN_SECONDS = 300


def _correlation_id() -> str:
    return str(uuid.uuid4())


def _try_start_price_check() -> str | None:
    global _price_check_run_id, _price_check_started_at
    with _price_check_guard:
        now = time.monotonic()
        if _price_check_run_id is not None and now - _price_check_started_at < STALE_RUN_SECONDS:
            return None
        _price_check_run_id = str(uuid.uuid4())
        _price_check_started_at = now
        return _price_check_run_id


def _finish_price_check(run_id: str) -> None:
    global _price_check_run_id, _price_check_started_at
    with _price_check_guard:
        if _price_check_run_id == run_id:
            _price_check_run_id = None
            _price_check_started_at = None


def _run_price_check(api_key: str | None, user: str) -> None:
    """Background task: look up a suggested price for every reviewed card
    that doesn't have one yet. Uses its own DB session -- the request-scoped
    one closes as soon as the triggering request returns, before this runs.
    A card that can't be confidently priced (not found, or ambiguous) gets
    its price_lookup_error recorded instead of a guessed price, and stays
    available for the human to price manually on the review page.

    pokemontcg.io only has USD pricing; every price is converted to AUD
    using one exchange rate fetched at the start of the run (not re-fetched
    per card -- it doesn't move fast enough to matter here, and re-fetching
    would just be extra load for no benefit). If the rate can't be fetched,
    the whole run is skipped rather than falling back to a stale or guessed
    rate.
    """
    run_id = _try_start_price_check()
    if run_id is None:
        return
    try:
        try:
            usd_to_aud_rate = get_usd_to_aud_rate()
        except CurrencyLookupError as exc:
            with SessionLocal() as session:
                record_event(session, actor_type="user", actor_id=user, action="card.price_check", resource_type="captured_card", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": f"could not get USD->AUD rate: {exc}"})
            return

        with SessionLocal() as session:
            cards = session.scalars(
                select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.REVIEWED, CapturedCard.pricing_status == PricingStatus.NOT_PRICED)
            ).all()
            for index, card in enumerate(cards):
                if not card.character or not card.set_name:
                    card.price_lookup_error = "missing character or set name, needs manual price"
                    session.commit()
                    continue
                try:
                    price_usd = lookup_price(card.character, card.set_name, card.card_number, api_key=api_key)
                except (PriceLookupError, PriceNotFound) as exc:
                    card.price_lookup_error = str(exc)
                    outcome = "failed"
                else:
                    card.suggested_price = usd_to_aud(price_usd, usd_to_aud_rate)
                    card.price_source = "pokemontcg_market_usd_to_aud"
                    card.price_lookup_error = None
                    card.pricing_status = PricingStatus.PENDING_PRICE_REVIEW
                    outcome = "success"

                card.price_checked_at = datetime.now(UTC)
                session.commit()
                record_event(session, actor_type="user", actor_id=user, action="card.price_check", resource_type="captured_card", resource_id=card.id, outcome=outcome, correlation_id=_correlation_id(), details={})

                if index < len(cards) - 1:
                    time.sleep(PACING_DELAY_SECONDS)
    finally:
        _finish_price_check(run_id)


def _next_price_review_card_id(session: Session, exclude_id: str) -> str | None:
    return session.scalars(
        select(CapturedCard.id)
        .where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW, CapturedCard.id != exclude_id)
        .order_by(CapturedCard.price_checked_at)
        .limit(1)
    ).first()


@router.get("", response_class=HTMLResponse)
def pricing_list(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = session.scalars(select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW).order_by(CapturedCard.price_checked_at)).all()
    unpriced = session.scalars(select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.REVIEWED, CapturedCard.pricing_status == PricingStatus.NOT_PRICED)).all()

    pending_rows = "".join(
        f"<li><a href='/cards/pricing/{c.id}'>{escape(c.character or '?')} — {escape(c.set_name or '?')} #{escape(c.card_number or '?')} — AUD ${c.suggested_price}</a></li>"
        for c in pending
    ) or "<li>Nothing waiting for price approval.</li>"

    unpriced_rows = "".join(
        f"<li>{escape(c.character or '?')} — {escape(c.set_name or '?')}"
        + (f" <span class='pill bad'>{escape(c.price_lookup_error)}</span>" if c.price_lookup_error else "")
        + f" <a href='/cards/pricing/{c.id}'>set price manually</a></li>"
        for c in unpriced
    ) or "<li>All reviewed cards have been checked.</li>"

    head_extra = "<meta http-equiv='refresh' content='4'>" if request.query_params.get("checking") else ""
    checking_note = "<p class='subtitle'>Checking prices in the background -- this page refreshes automatically.</p>" if request.query_params.get("checking") else ""

    body = (
        brand_header("Price review")
        + checking_note
        + f"<div class='panel'><h2>Awaiting approval ({len(pending)})</h2><ul class='events'>{pending_rows}</ul></div>"
        + f"<div class='panel'><h2>Not yet priced ({len(unpriced)})</h2><ul class='events'>{unpriced_rows}</ul>"
        + "<form method='post' action='/cards/pricing/check-all'><button>Check prices</button></form></div>"
    )
    return HTMLResponse(page("EzBay — Price review", body, head_extra=head_extra))


@router.post("/check-all")
def pricing_check_all(request: Request, background_tasks: BackgroundTasks, user: str = Depends(require_dashboard_user)) -> RedirectResponse:
    settings = request.app.state.settings
    background_tasks.add_task(_run_price_check, settings.pokemontcg_api_key, user)
    return RedirectResponse("/cards/pricing?checking=1", status_code=303)


@router.get("/{card_id}", response_class=HTMLResponse)
def price_review_form(card_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    candidates = session.scalars(
        select(CapturedCard).where(
            CapturedCard.id != card_id,
            CapturedCard.status == CardCaptureStatus.REVIEWED,
            CapturedCard.pricing_status.in_([PricingStatus.NOT_PRICED, PricingStatus.PENDING_PRICE_REVIEW]),
        )
    ).all()
    bundle_options = "".join(
        f"<label><input type='checkbox' name='bundle_with' value='{c.id}'"
        + (" checked" if card.bundle_id and c.bundle_id == card.bundle_id else "")
        + f"> {escape(c.character or '?')} — {escape(c.set_name or '?')} #{escape(c.card_number or '?')}</label>"
        for c in candidates
    ) or "<p class='subtitle'>No other unbundled cards available to bundle with.</p>"

    error_html = f"<p class='pill bad'>{escape(card.price_lookup_error)}</p>" if card.price_lookup_error else ""
    price_value = card.suggested_price if card.suggested_price is not None else ""
    source_note = f"<p class='subtitle'>Suggested from {escape(card.price_source)} on {card.price_checked_at}</p>" if card.price_source else ""

    body = (
        brand_header("Price review")
        + "<div class='panel'>"
        + f"<h2>{escape(card.character or '?')} — {escape(card.set_name or '?')} #{escape(card.card_number or '?')}</h2>"
        + f"<p><img src='/cards/image/{card_id}/front' style='max-width:280px;border-radius:8px'></p>"
        + error_html + source_note
        + f"<form method='post' action='/cards/pricing/{card_id}'>"
        + f"<label>Price (AUD) <input name='price' value='{price_value}' required></label>"
        + f"<h3>Bundle with</h3>{bundle_options}"
        + "<div style='display:flex;gap:12px'>"
        + "<button name='action' value='approve' style='background:linear-gradient(120deg,var(--success),var(--accent))'>Approve</button>"
        + "<button name='action' value='reject' style='background:var(--danger);color:#fff'>Deny</button>"
        + "</div></form></div>"
    )
    return HTMLResponse(page("EzBay — Price review", body))


@router.post("/{card_id}")
def price_review_submit(
    card_id: str,
    action: str = Form("approve"),
    price: str = Form(""),
    bundle_with: list[str] = Form([]),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    card = session.get(CapturedCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    if action == "reject":
        card.pricing_status = PricingStatus.PRICE_REJECTED
        audit_action = "card.price_reject"
    else:
        try:
            decimal_price = Decimal(price)
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="price must be a valid number") from None
        if decimal_price <= 0:
            raise HTTPException(status_code=400, detail="price must be greater than zero")

        bundle_id = card.bundle_id or str(uuid.uuid4())
        bundle_members = [card]
        if bundle_with:
            others = session.scalars(select(CapturedCard).where(CapturedCard.id.in_(bundle_with))).all()
            bundle_members.extend(others)

        for member in bundle_members:
            member.approved_price = decimal_price
            member.pricing_status = PricingStatus.PRICE_APPROVED
            if len(bundle_members) > 1:
                member.bundle_id = bundle_id
        audit_action = "card.price_approve"

    session.commit()
    record_event(session, actor_type="user", actor_id=user, action=audit_action, resource_type="captured_card", resource_id=card_id, outcome="success", correlation_id=_correlation_id(), details={"bundle_with": bundle_with} if action != "reject" else {})

    next_id = _next_price_review_card_id(session, card_id)
    if next_id:
        return RedirectResponse(f"/cards/pricing/{next_id}", status_code=303)
    return RedirectResponse("/cards/pricing", status_code=303)
