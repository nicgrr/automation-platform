import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_inventory, normalize_market
from .business_records import router as business_records_router
from .buying import router as buying_router
from .commerce import router as commerce_router
from .audit import record_event
from .auth import create_session, require_dashboard_user, verify_password
from .capture import router as cards_router
from .config import Settings, get_settings
from .database import Base, engine, get_session
from .ebay_oauth import EbayOAuthClient, OAuthError, TokenCipher, authorization_url, consume_oauth_state, new_oauth_state, store_user_tokens, valid_sandbox_client_id, valid_sandbox_runame, valid_user_access_token
from .foil_review import router as foil_review_router
from .inventory_review import router as inventory_router
from .listings_review import router as listings_router
from .models import Approval, AuditEvent, CapturedCard, CardCaptureStatus, EbayCredential, EbayListing, InventoryItem, JobRun, ListingBuildStatus, PendingListing, PricingStatus, TcgCard
from .price_review import router as pricing_router
from .review_queue import router as review_router
from .scan_feed import router as scan_feed_router
from .search import router as search_router
from .scan_ingest_status import router as scan_ingest_status_router
from .schemas import ApprovalCreate, ApprovalRead, HealthResponse, JobCreate, JobRead
from .ui import brand_header, page, pill

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="EzBay Private Control Plane", version="0.2.0", lifespan=lifespan)
app.state.settings = get_settings()
app.include_router(cards_router)
app.include_router(pricing_router)
app.include_router(listings_router)
app.include_router(inventory_router)
app.include_router(scan_ingest_status_router)
app.include_router(review_router)
app.include_router(scan_feed_router)
app.include_router(foil_review_router)
app.include_router(search_router)
app.include_router(buying_router)
app.include_router(commerce_router)
app.include_router(business_records_router)


def correlation_id() -> str:
    return str(uuid.uuid4())


def configured(settings: Settings) -> bool:
    return bool(
        valid_sandbox_client_id(settings.ebay_client_id)
        and valid_sandbox_runame(settings.ebay_runame)
        and settings.ebay_client_secret
        and not settings.ebay_client_secret.startswith("REPLACE_WITH_")
        and settings.ebay_token_encryption_key
        and not settings.ebay_token_encryption_key.startswith("REPLACE_WITH_")
        and settings.app_public_base_url
    )


def ebay_services(settings: Settings):
    if not configured(settings):
        raise HTTPException(status_code=503, detail="eBay Sandbox OAuth is not configured")
    return TokenCipher(settings.ebay_token_encryption_key), EbayOAuthClient(settings.ebay_client_id, settings.ebay_client_secret)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    body = (
        brand_header("Private Control Plane", show_back=False)
        + "<p class='subtitle'>Sign in to continue.</p>"
        + "<div class='panel'><form method=post>"
        + "<label>Username <input name=username autocomplete=username required></label>"
        + "<label>Password <input type=password name=password autocomplete=current-password required></label>"
        + "<button>Sign in</button></form></div>"
    )
    return page("EzBay Login", body)


@app.post("/login")
def login(request: Request, username: str = Form(), password: str = Form(), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    ok = bool(settings.dashboard_username and settings.dashboard_password_hash and settings.session_signing_key and username == settings.dashboard_username and verify_password(password, settings.dashboard_password_hash))
    record_event(session, actor_type="user", actor_id=username[:128], action="dashboard.login", resource_type="session", resource_id=None, outcome="success" if ok else "denied", correlation_id=correlation_id(), details={})
    if not ok:
        raise HTTPException(status_code=401, detail="invalid credentials")
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("rbay_session", create_session(username, settings.session_signing_key), httponly=True, secure=True, samesite="strict", max_age=8 * 3600)
    return response


def _review_queue_size(settings) -> int:
    """How many crops the scanner set aside. A plain directory count -- the
    queue is files on disk, not a table."""
    directory = Path(settings.scan_media_dir) / "needs_review"
    return len(list(directory.glob("*.jpg"))) if directory.exists() else 0


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    events = session.scalars(select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(10)).all()
    event_html = "".join(f"<li>{escape(str(e.occurred_at))} — {escape(e.action)} — {escape(e.outcome)}</li>" for e in events) or "<li>No events</li>"
    pending_review_count = len(session.scalars(select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW)).all())
    pending_price_count = len(session.scalars(select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW)).all())
    pending_listing_count = len(session.scalars(select(PendingListing).where(PendingListing.status == ListingBuildStatus.DRAFT)).all())
    scanned_items = session.scalars(select(InventoryItem)).all()
    scanned_holdings = len(scanned_items)
    scanned_copies = sum(i.quantity for i in scanned_items)
    review_count = _review_queue_size(request.app.state.settings)
    catalog_by_game = session.execute(
        select(TcgCard.game, func.count()).group_by(TcgCard.game)
    ).all()
    catalog_summary = ", ".join(f"{count:,} {game}" for game, count in catalog_by_game) or "none yet"

    # Grouped by which pipeline the number belongs to -- "pending review"
    # (single capture) and "awaiting review" (bulk scan) sat side by side as
    # one flat, same-looking grid before, which read as one confusing number
    # split in two rather than two different queues.
    stat_grid = (
        "<div class='stat-group'>"
        "<h3 class='group-label'>Bulk scan pipeline</h3>"
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Scanned inventory</div><div class='value'>{scanned_holdings} <span class='value-sub'>({scanned_copies} copies)</span></div></div>"
        f"<div class='stat-card'><div class='label'>Awaiting review</div><div class='value'>{review_count}</div></div>"
        "</div></div>"
        "<div class='stat-group'>"
        "<h3 class='group-label'>Single capture &amp; listings</h3>"
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Pending review</div><div class='value'>{pending_review_count}</div></div>"
        f"<div class='stat-card'><div class='label'>Pending price approval</div><div class='value'>{pending_price_count}</div></div>"
        f"<div class='stat-card'><div class='label'>Listings pending review</div><div class='value'>{pending_listing_count}</div></div>"
        "</div></div>"
    )

    body = (
        f"<div class='brand-row'>{brand_header('Private Control Plane', show_back=False)}{pill('Platform OK', 'ok')}</div>"
        + "<p class='subtitle'>Trading card listing operations.</p>"
        + stat_grid
        + f"<div class='panel'><h2>Card capture</h2><p><a class='btn' href='/cards/capture'>Capture new card</a> &nbsp; <a href='/cards/capture/bulk'>Bulk upload</a> &nbsp; <a href='/cards/review'>Review queue ({pending_review_count})</a> &nbsp; <a href='/cards/pricing'>Price review ({pending_price_count})</a> &nbsp; <a href='/listings/review'>Listing review ({pending_listing_count})</a></p></div>"
        + f"<div class='panel'><h2>Bulk scan inventory</h2><p><a class='btn' href='/inventory'>Browse inventory ({scanned_holdings} distinct, {scanned_copies} copies)</a> &nbsp; <a href='/feed'>Scan feed</a> &nbsp; <a href='/scan-ingest'>Status &amp; logs</a> &nbsp; <a href='/review'>Review queue ({review_count})</a> &nbsp; <a href='/foil-review'>Foil review</a></p></div>"
        + f"<div class='panel'><h2>Catalogue</h2><p><a class='btn' href='/search'>Search catalogue</a> &nbsp; <span class='subtitle' style='margin:0'>{escape(catalog_summary)}</span></p></div>"
        + "<div class='panel'><h2>Business</h2><p>"
          "<a class='btn' href='/buying-calculator'>Buying calculator</a> &nbsp; "
          "<a href='/purchase-lots'>Purchase lots</a> &nbsp; "
          "<a href='/potential-stock'>Potential stock</a> &nbsp; "
          "<a href='/sales'>Sales</a> &nbsp; "
          "<a href='/customers'>Customers</a> &nbsp; "
          "<a href='/suppliers'>Suppliers</a> &nbsp; "
          "<a href='/marketplaces'>Marketplaces</a> &nbsp; "
          "<a href='/goals'>Goals</a> &nbsp; "
          "<a href='/release-calendar'>Release calendar</a>"
          "</p></div>"
        + f"<div class='panel'><h2>Recent audit events</h2><ul class='events'>{event_html}</ul></div>"
    )
    return page("EzBay Dashboard", body)


@app.get("/auth/ebay/start")
def ebay_start(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if not configured(settings):
        raise HTTPException(status_code=503, detail="eBay Sandbox OAuth is not configured")
    state_value = new_oauth_state(session)
    record_event(session, actor_type="user", actor_id=user, action="ebay.oauth.start", resource_type="ebay_connection", resource_id="sandbox", outcome="success", correlation_id=correlation_id(), details={})
    try:
        target = authorization_url(settings.ebay_client_id, settings.ebay_runame, state_value)
    except OAuthError:
        raise HTTPException(status_code=503, detail="eBay Sandbox OAuth configuration is invalid")
    return RedirectResponse(target, status_code=302)


@app.get("/auth/ebay/callback")
async def ebay_callback(request: Request, state: str | None = None, code: str | None = None, error: str | None = None, session: Session = Depends(get_session)):
    if error or not code or not consume_oauth_state(session, state):
        record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id="sandbox", outcome="denied", correlation_id=correlation_id(), details={"error": "provider_error" if error else "invalid_callback"})
        raise HTTPException(status_code=400, detail="invalid or expired OAuth callback")
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        payload = await oauth.exchange_code(code, settings.ebay_runame)
        store_user_tokens(session, cipher, payload)
    except OAuthError:
        record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id="sandbox", outcome="failed", correlation_id=correlation_id(), details={"error": "token_exchange_failed"})
        raise HTTPException(status_code=502, detail="eBay Sandbox authorization failed")
    record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id="sandbox", outcome="success", correlation_id=correlation_id(), details={})
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/api/ebay/status")
def ebay_status(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    record = session.get(EbayCredential, 1)
    return {"environment": "SANDBOX", "configured": configured(app.state.settings), "connected": bool(record), "access_token_expires_at": record.access_token_expires_at if record else None, "last_successful_sync": record.last_successful_sync_at if record else None}


@app.get("/api/ebay/listings")
async def ebay_listings(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        payload = await EbaySandboxReadAdapter(token).get_listings()
        items = normalize_inventory(payload)
        for item in items:
            price = item["price"]
            price_minor = int(Decimal(str(price)) * 100) if price is not None else 0
            existing = session.scalar(select(EbayListing).where(EbayListing.ebay_listing_id == item["ebay_item_id"]))
            values = dict(sku=item["sku"], title=item["title"], currency=item["currency"] or "AUD", current_price_minor=price_minor, quantity=item["quantity"], listing_status=item["listing_status"], marketplace=item["marketplace"], raw_snapshot=item["raw"], observed_at=item["retrieved_at"])
            if existing:
                for key, value in values.items(): setattr(existing, key, value)
            else:
                session.add(EbayListing(ebay_listing_id=item["ebay_item_id"], **values))
        credential = session.get(EbayCredential, 1)
        credential.last_successful_sync_at = datetime.now(UTC)
        session.commit()
        record_event(session, actor_type="user", actor_id=user, action="ebay.listings.read", resource_type="ebay_listing", resource_id=None, outcome="success", correlation_id=correlation_id(), details={"count": len(items)})
        return {"environment": "SANDBOX", "count": len(items), "items": [{k: v for k, v in item.items() if k != "raw"} for item in items]}
    except (OAuthError, EbayApiError, InvalidOperation):
        record_event(session, actor_type="user", actor_id=user, action="ebay.listings.read", resource_type="ebay_listing", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail="eBay Sandbox listing retrieval failed")


@app.get("/api/ebay/seller")
async def ebay_seller(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        seller = await EbaySandboxReadAdapter(token).get_seller()
        record_event(session, actor_type="user", actor_id=user, action="ebay.seller.read", resource_type="ebay_seller", resource_id=None, outcome="success", correlation_id=correlation_id(), details={})
        return {"environment": "SANDBOX", "seller": seller}
    except (OAuthError, EbayApiError):
        record_event(session, actor_type="user", actor_id=user, action="ebay.seller.read", resource_type="ebay_seller", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail="eBay Sandbox seller retrieval failed")


@app.get("/api/ebay/market-search")
async def ebay_market_search(request: Request, q: str = Query(min_length=1, max_length=200), user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    _, oauth = ebay_services(settings)
    try:
        token_payload = await oauth.application_token()
        payload = await EbaySandboxReadAdapter(token_payload["access_token"]).search_market(q)
        items = normalize_market(payload)
        record_event(session, actor_type="user", actor_id=user, action="ebay.market_search.read", resource_type="market_observation", resource_id=None, outcome="success", correlation_id=correlation_id(), details={"query": q, "count": len(items)})
        return {"environment": "SANDBOX", "count": len(items), "items": items}
    except (OAuthError, EbayApiError, KeyError):
        record_event(session, actor_type="user", actor_id=user, action="ebay.market_search.read", resource_type="market_observation", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail="eBay Sandbox market search failed")


@app.post("/approvals", response_model=ApprovalRead, status_code=status.HTTP_201_CREATED)
def request_approval(payload: ApprovalCreate, session: Session = Depends(get_session), user: str = Depends(require_dashboard_user)) -> Approval:
    approval = Approval(**payload.model_dump()); session.add(approval); session.commit(); session.refresh(approval); return approval


@app.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, session: Session = Depends(get_session), user: str = Depends(require_dashboard_user)) -> JobRun:
    if payload.job_type not in {"grocery.snapshot", "ebay.market_snapshot", "docker.status_snapshot"}:
        raise HTTPException(status_code=400, detail="unregistered job type")
    job = JobRun(**payload.model_dump()); session.add(job); session.commit(); session.refresh(job); return job
