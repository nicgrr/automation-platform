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
from .classification import classify_pending_cards, load_pipeline_config
from .scan_ingest.pricing import POKEMONPRICETRACKER_SOURCE_NAME
from .database import SessionLocal, get_session
from .models import (CapturedCard, CardCaptureStatus, CardPrice, CatalogItem, CatalogItemType,
                     InventoryItem, PricingStatus)
from .ui import brand_header, page

router = APIRouter(prefix="/cards/pricing", tags=["pricing"])

PACING_DELAY_SECONDS = 1.0

# Whether asking pokemontcg.io the same question again could plausibly give a
# different answer. This is the difference between the two halves of the
# not-yet-priced list, and it matters because they need opposite things from
# you: one needs a click, the other needs a decision.
#
# Twelve cards sat unpriced for a month because the page showed both the same
# way -- ten of them behind nothing worse than a 500 from an API that was
# briefly down on 15 August.
#
# An unrecognised error is treated as retryable on purpose. Guessing
# "transient" costs one API call; guessing "permanent" strands a card in a
# list nobody revisits, which is the failure this is fixing.
_NEEDS_A_PERSON = (
    "ambiguous match",                    # two candidates, different prices
    "no priced match found",              # searched every variant, found nothing
    "missing character or set name",      # the capture itself is incomplete
)


def needs_a_person(error: str | None) -> bool:
    """True when re-running the lookup cannot help -- only you can."""
    return bool(error) and any(hint in error.lower() for hint in _NEEDS_A_PERSON)



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


def _run_price_check(api_key: str | None, user: str, include_blocked: bool = False) -> None:
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
            if not include_blocked:
                # Re-asking about an ambiguous card returns the same ambiguity
                # and spends a rate-limited call to do it.
                cards = [c for c in cards if not needs_a_person(c.price_lookup_error)]
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

            try:
                pipeline_config = load_pipeline_config()
            except Exception as exc:
                record_event(session, actor_type="user", actor_id=user, action="card.auto_classify", resource_type="captured_card", resource_id=None, outcome="failed", correlation_id=_correlation_id(), details={"error": f"could not load classification config: {exc}"})
            else:
                if pipeline_config.classification is not None:
                    classify_pending_cards(
                        session,
                        threshold_aud=pipeline_config.classification.bulk_price_threshold_aud,
                        max_lot_size=pipeline_config.classification.max_lot_size,
                    )
    finally:
        _finish_price_check(run_id)


# Both gap counts below join on InventoryItem.catalog_item_id, not on the
# legacy InventoryItem.card_id they used to use.
#
# `card_prices.card_id` is a catalog_items id despite the column name --
# import_ebay_prices.py writes `card_id=item.id` from a CatalogItem, and
# every reader of the table (the dashboard gauge, /inventory's totals)
# looks it up that way. Counting against InventoryItem.card_id compared
# two different key spaces, so both numbers were inflated by every holding
# whose two ids differ and by every row whose catalog link is missing: the page
# advertised a backlog of ~1,600 pairs and ~300 eBay gaps that no amount
# of running the importers could ever close, because the importers target
# catalog_item_id too.
def _held_tcg_cards(*extra_columns):
    """Held TCG cards, identified by catalog_item_id, for the gap counts.

    Restricted by CatalogItem.item_type rather than by "card_id is not
    NULL", which is what both counts used to lean on. That was only ever a
    proxy for "is a card rather than a Sonny Angel", and an inaccurate one:
    392 held cards reach the catalogue through catalog_items alone (a
    Japanese print, a Dragon Ball card -- anything never imported into
    catalog_cards) and were being dropped from the work these numbers
    describe, even though import_ebay_prices.py prices them perfectly well.
    Asking the catalogue what kind of thing something is answers the
    question the proxy was approximating.
    """
    return (
        select(InventoryItem.catalog_item_id, *extra_columns)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .where(CatalogItem.item_type == CatalogItemType.TCG_CARD)
        .distinct()
    )


def _inventory_price_backlog(session: Session) -> int:
    """Held card+variant pairs with no pokemonpricetracker.com price yet.

    The number the "check prices for everything" button can't make go away
    in one click: the free tier is 100 credits a day and a search costs 3,
    so a run covers ~25 cards. Showing the backlog is the honest version of
    that button -- it says how far through you are rather than implying the
    job finishes when the page reloads.
    """
    priced = select(CardPrice.card_id, CardPrice.variant).where(
        CardPrice.source == POKEMONPRICETRACKER_SOURCE_NAME)
    held = session.execute(_held_tcg_cards(InventoryItem.variant)).all()
    return len(set(held) - set(session.execute(priced).all()))


def _ebay_price_gap(session: Session) -> int:
    """Held cards with no eBay active-listing signal at all.

    Tracked separately from the pokemonpricetracker backlog because the two
    sources have opposite shapes. eBay's Browse quota is ~5,000 calls a day
    at one per card, so the whole held set fits in a single run -- this is
    the source that genuinely can be "everything". `list_for_sale` also
    prefers `ebay_active_low` as its conservative figure, which makes a
    stale one the more expensive gap of the two.
    """
    held = {row[0] for row in session.execute(_held_tcg_cards())}
    priced = {row[0] for row in session.execute(
        select(CardPrice.card_id).where(CardPrice.source == "ebay_active_low").distinct())}
    return len(held - priced)


def _run_ebay_price_refresh(user: str) -> None:
    from scripts.import_ebay_prices import main as import_prices

    outcome, details = "success", {}
    try:
        details["exit_code"] = import_prices([])
    except Exception as exc:
        outcome, details = "failed", {"error": f"{type(exc).__name__}: {exc}"}
    with SessionLocal() as session:
        record_event(session, actor_type="user", actor_id=user,
                     action="inventory.ebay_price_refresh", resource_type="inventory_item",
                     resource_id=None, outcome=outcome,
                     correlation_id=_correlation_id(), details=details)


def _run_inventory_price_refresh(user: str) -> None:
    """One day's worth of the nightly backfill, on demand.

    Runs the same script the systemd timer runs rather than a second
    implementation -- that timer spent six days failing on a TypeError
    nobody saw, and two copies of this logic would double the odds of it
    happening again.
    """
    from scripts.backfill_pokemonpricetracker_prices import main as backfill

    outcome, details = "success", {}
    try:
        details["exit_code"] = backfill([])
    except Exception as exc:  # a background task that dies silently is the bug above
        outcome, details = "failed", {"error": f"{type(exc).__name__}: {exc}"}
    with SessionLocal() as session:
        record_event(session, actor_type="user", actor_id=user,
                     action="inventory.price_refresh", resource_type="inventory_item",
                     resource_id=None, outcome=outcome,
                     correlation_id=_correlation_id(), details=details)


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

    # Two lists, because they need opposite things from you: one needs a
    # click, the other needs a decision. Showing them as one list is how
    # twelve cards sat unpriced for a month.
    blocked = [c for c in unpriced if needs_a_person(c.price_lookup_error)]
    retryable = [c for c in unpriced if not needs_a_person(c.price_lookup_error)]

    def _row(card, *, show_error: bool) -> str:
        label = f"{escape(card.character or '?')} — {escape(card.set_name or '?')}"
        if card.card_number:
            label += f" #{escape(card.card_number)}"
        reason = (f"<span class='pill bad'>{escape(card.price_lookup_error)}</span>"
                  if show_error and card.price_lookup_error else "")
        return f"<li>{label} {reason} <a href='/cards/pricing/{card.id}'>set price manually</a></li>"

    retry_rows = "".join(_row(c, show_error=False) for c in retryable) or \
        "<li>Nothing waiting on a retry.</li>"
    blocked_rows = "".join(_row(c, show_error=True) for c in blocked) or \
        "<li>Nothing needs a decision.</li>"
    never_checked = sum(1 for c in retryable if not c.price_lookup_error)
    failed_before = len(retryable) - never_checked

    backlog = _inventory_price_backlog(session)
    ebay_gap = _ebay_price_gap(session)
    head_extra = "<meta http-equiv='refresh' content='4'>" if request.query_params.get("checking") else ""
    checking_note = "<p class='subtitle'>Checking prices in the background -- this page refreshes automatically.</p>" if request.query_params.get("checking") else ""

    if never_checked and failed_before:
        retry_note = (f"{never_checked} never checked, {failed_before} failed on a temporary error "
                      "and can be tried again.")
    elif failed_before:
        retry_note = (f"{failed_before} failed on a temporary error &mdash; an upstream outage or a "
                      "timeout, not a problem with the card.")
    else:
        retry_note = "Captured and reviewed, waiting on their first price check."

    button = ("Check prices" if not failed_before
              else f"Retry {len(retryable)} lookup{'s' if len(retryable) != 1 else ''}")

    body = (
        brand_header("Price review")
        + checking_note
        + f"<div class='panel'><h2>Awaiting approval ({len(pending)})</h2><ul class='events'>{pending_rows}</ul></div>"
        + f"<div class='panel'><h2>Ready to price ({len(retryable)})</h2>"
        + f"<p class='subtitle'>{retry_note}</p>"
        + f"<ul class='events'>{retry_rows}</ul>"
        + (f"<form method='post' action='/cards/pricing/check-all'><button>{button}</button></form>"
           if retryable else "")
        + "</div>"
        + f"<div class='panel'><h2>Needs your decision ({len(blocked)})</h2>"
        + "<p class='subtitle'>Asking again returns the same answer &mdash; these need a price set by "
          "hand, or the capture corrected.</p>"
        + f"<ul class='events'>{blocked_rows}</ul>"
        + ("<form method='post' action='/cards/pricing/check-all'>"
           "<input type='hidden' name='include_blocked' value='1'>"
           "<button class='ghost'>Try these anyway</button></form>" if blocked else "")
        + "</div>"
        + f"<div class='panel'><h2>Inventory prices</h2>"
        + (f"<p class='subtitle'>{backlog:,} held card/variant pair{'s' if backlog != 1 else ''} "
           "still have no market price. The free tier allows about 25 a day, so this "
           "works through the backlog rather than finishing it &mdash; it also runs "
           "automatically at noon.</p>"
           if backlog else
           "<p class='subtitle'>Every held card has a market price. Nothing to do.</p>")
        + ("<form method='post' action='/cards/pricing/refresh-inventory'>"
           "<button>Price the next 25</button></form>" if backlog else "")
        + "</div>"
        + "<div class='panel'><h2>eBay market signal</h2>"
        + (f"<p class='subtitle'>{ebay_gap:,} held "
           f"{'cards have' if ebay_gap != 1 else 'card has'} no eBay "
           "asking-price signal. This is the figure listing prices are built from, and eBay's quota "
           "is high enough to refresh the whole held set in one run.</p>"
           if ebay_gap else
           "<p class='subtitle'>Every held card has an eBay signal. Re-run to refresh it &mdash; "
           "asking prices move.</p>")
        + "<form method='post' action='/cards/pricing/refresh-ebay'>"
          "<button>Refresh eBay prices</button></form></div>"
    )
    return HTMLResponse(page("EzBay — Price review", body, head_extra=head_extra))


@router.post("/check-all")
def pricing_check_all(request: Request, background_tasks: BackgroundTasks,
                      include_blocked: str = Form(""),
                      user: str = Depends(require_dashboard_user)) -> RedirectResponse:
    """Price everything a lookup could still help with.

    `include_blocked` is the escape hatch behind "Try these anyway": the
    classifier reads error strings, so a wrongly-classified card would
    otherwise never be retried again by anything.
    """
    settings = request.app.state.settings
    background_tasks.add_task(_run_price_check, settings.pokemontcg_api_key, user, bool(include_blocked))
    return RedirectResponse("/cards/pricing?checking=1", status_code=303)


@router.post("/refresh-inventory")
def pricing_refresh_inventory(background_tasks: BackgroundTasks,
                              user: str = Depends(require_dashboard_user)) -> RedirectResponse:
    """Run one day's worth of the inventory price backfill now.

    Deliberately not "re-price everything": the upstream free tier is 100
    credits a day at 3 credits a search, so everything is roughly 48 days
    of runs. The button does what is actually possible and the page says
    how much is left.
    """
    background_tasks.add_task(_run_inventory_price_refresh, user)
    return RedirectResponse("/cards/pricing?checking=1", status_code=303)


@router.post("/refresh-ebay")
def pricing_refresh_ebay(background_tasks: BackgroundTasks,
                         user: str = Depends(require_dashboard_user)) -> RedirectResponse:
    """Re-price held inventory against eBay active listings.

    Unlike the pokemonpricetracker backfill this has no daily timer, so
    until one exists this button is the only thing that runs it -- the
    prices behind every listing decision were last refreshed whenever
    someone last remembered to open a terminal.
    """
    background_tasks.add_task(_run_ebay_price_refresh, user)
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
