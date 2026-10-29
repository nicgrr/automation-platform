import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_inventory, normalize_market
from .analytics import router as analytics_router
from .audit import record_event
from .auth import (
    SESSION_MAX_AGE_SECONDS,
    clear_login_failures,
    create_session,
    csrf_token_for,
    login_rate_limited,
    record_login_failure,
    require_dashboard_user,
    verify_password,
)
from .business_records import router as business_records_router
from .accessories import router as accessories_router
from .buying import router as buying_router
from .capture import router as cards_router
from .collectible_link import router as collectible_link_router
from .contact_messages import router as contact_messages_router
from .collectibles import router as collectibles_router
from .collectible_pricing import router as collectible_pricing_router
from .commerce import router as commerce_router
from .config import Settings, get_settings
from .dashboard import router as dashboard_router
from .nav_counts import router as nav_counts_router
from .data_export import router as data_export_router
from .deal_check import router as deal_check_router
from .database import engine, get_session
from .ebay_oauth import OAuthError, authorization_url, configured, consume_oauth_state, ebay_services, new_oauth_state, store_user_tokens, valid_user_access_token
from .foil_review import router as foil_review_router
from .inventory_audit_review import router as inventory_audit_router
from .inventory_review import router as inventory_router
from .listings_review import router as listings_router
from .market_search import router as market_search_router
from .market_watch import router as market_watch_router
from .market_watch_service import allowed_country_codes
from .models import EbayCredential, EbayListing
from .price_history import router as price_history_router
from .price_review import router as pricing_router
from .quick_price import router as quick_price_router
from .review_queue import router as review_router
from .scan_feed import router as scan_feed_router
from .scan_ingest_status import router as scan_ingest_status_router
from .schemas import HealthResponse
from .sealed_economics import router as sealed_economics_router
from .search import router as search_router
from .ebay_bulk_export import router as ebay_bulk_export_router
from .ebay_listings import router as ebay_listings_router
from .settings_page import router as settings_page_router
from .shop_admin import router as shop_admin_router
from .shop_listings_admin import router as shop_listings_router
from .ebay_publish import router as ebay_publish_router
from .ebay_photo_batch import router as ebay_photo_batch_router
from .photo_identification_review import router as photo_identification_review_router
from .ebay_sales import router as ebay_sales_router
from .labels import router as labels_router
from .shop_orders import router as shop_orders_router
from .personal_collection import router as personal_collection_router
from .retailer_goals import router as retailer_goals_router
from .release_info import expected_database_revision, require_database_revision
from .navigation_hubs import router as navigation_hubs_router
from .ui import brand_header, page
from .whatnot import router as whatnot_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Alembic is authoritative from the 2026-10-01 baseline onward. Starting
    # against an older/newer or unversioned schema is more dangerous than a
    # clear failed start: create_all() cannot add columns or prove that a
    # restored database belongs to this release.
    with Session(engine) as session:
        require_database_revision(session, expected_database_revision())
    yield


app = FastAPI(
    title="EzBay Private Control Plane",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# PWA manifest + icons (Module 18) -- unauthenticated on purpose, matching
# any app icon/manifest: no inventory or business data lives under here,
# just the install-to-home-screen assets.
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
app.state.settings = get_settings()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_STATIC_DIR / "icons" / "icon-192.png", media_type="image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_touch_icon() -> FileResponse:
    return FileResponse(_STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    # A root-scoped worker must opt into a scope wider than its file only
    # when served below a subdirectory. This file is already at root, but the
    # explicit header documents and locks down the intended scope.
    return FileResponse(
        _STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.middleware("http")
async def renew_session_middleware(request: Request, call_next):
    """Writes the sliding-session cookie onto the real response, after the
    route has already returned its own Response object.

    require_dashboard_user can't set this cookie itself: FastAPI only
    merges a dependency's injected Response headers into the final
    response when the route doesn't return its own, and every route here
    does (`return HTMLResponse(...)`, `return RedirectResponse(...)`).
    Middleware sees the actual object the route returned, so it works
    regardless of which Response subclass that route happens to use.
    """
    response = await call_next(request)
    username = getattr(request.state, "renew_session_for", None)
    if username:
        settings = request.app.state.settings
        issued = int(time.time())
        response.set_cookie("rbay_session", create_session(username, settings.session_signing_key, now=issued),
                            httponly=True, secure=True, samesite="strict", max_age=SESSION_MAX_AGE_SECONDS)
        response.set_cookie("rbay_csrf", csrf_token_for(username, issued, settings.session_signing_key),
                            httponly=False, secure=True, samesite="strict", max_age=SESSION_MAX_AGE_SECONDS)
    # These pages contain live business state and should never be restored
    # from an HTTP cache after the app has spent time in the background.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
app.include_router(cards_router)
app.include_router(pricing_router)
app.include_router(listings_router)
app.include_router(inventory_router)
app.include_router(scan_ingest_status_router)
app.include_router(review_router)
app.include_router(scan_feed_router)
app.include_router(foil_review_router)
app.include_router(inventory_audit_router)
app.include_router(search_router)
app.include_router(settings_page_router)
app.include_router(ebay_listings_router)
app.include_router(ebay_bulk_export_router)
app.include_router(buying_router)
app.include_router(commerce_router)
app.include_router(business_records_router)
app.include_router(sealed_economics_router)
app.include_router(analytics_router)
app.include_router(collectibles_router)
app.include_router(collectible_pricing_router)
app.include_router(collectible_link_router)
app.include_router(accessories_router)
app.include_router(contact_messages_router)
app.include_router(whatnot_router)
app.include_router(price_history_router)
app.include_router(dashboard_router)
app.include_router(nav_counts_router)
app.include_router(data_export_router)
app.include_router(quick_price_router)
app.include_router(market_search_router)
app.include_router(market_watch_router)
app.include_router(deal_check_router)
app.include_router(shop_admin_router)
app.include_router(shop_listings_router)
app.include_router(ebay_publish_router)
app.include_router(ebay_photo_batch_router)
app.include_router(photo_identification_review_router)
app.include_router(shop_orders_router)
app.include_router(ebay_sales_router)
app.include_router(labels_router)
app.include_router(personal_collection_router)
app.include_router(retailer_goals_router)
app.include_router(navigation_hubs_router)


def correlation_id() -> str:
    return str(uuid.uuid4())




@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    body = (
        brand_header("Private Control Plane", show_back=False)
        + "<div class='panel'><form method=post>"
        + "<label>Username <input name=username autocomplete=username required></label>"
        + "<label>Password <input type=password name=password autocomplete=current-password required></label>"
        + "<button>Sign in</button></form></div>"
    )
    return page("EzBay Login", body, show_nav=False)


@app.post("/login")
def login(request: Request, username: str = Form(), password: str = Form(), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    client_key = request.client.host if request.client else "unknown"
    if login_rate_limited(client_key):
        record_event(session, actor_type="user", actor_id=username[:128], action="dashboard.login",
                    resource_type="session", resource_id=None, outcome="rate_limited",
                    correlation_id=correlation_id(), details={"client": client_key})
        raise HTTPException(status_code=429, detail="too many attempts -- wait a few minutes and try again")
    ok = bool(settings.dashboard_username and settings.dashboard_password_hash and settings.session_signing_key and username == settings.dashboard_username and verify_password(password, settings.dashboard_password_hash))
    record_event(session, actor_type="user", actor_id=username[:128], action="dashboard.login", resource_type="session", resource_id=None, outcome="success" if ok else "denied", correlation_id=correlation_id(), details={})
    if not ok:
        record_login_failure(client_key)
        raise HTTPException(status_code=401, detail="invalid credentials")
    clear_login_failures(client_key)
    issued = int(time.time())
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("rbay_session", create_session(username, settings.session_signing_key, now=issued),
                        httponly=True, secure=True, samesite="strict", max_age=SESSION_MAX_AGE_SECONDS)
    # Deliberately NOT httponly -- see auth.csrf_token_for's docstring:
    # the shared client-side JS (ui.py's CSRF_SCRIPT) needs to read this
    # one to attach it to every form post and fetch() POST automatically.
    response.set_cookie("rbay_csrf", csrf_token_for(username, issued, settings.session_signing_key),
                        httponly=False, secure=True, samesite="strict", max_age=SESSION_MAX_AGE_SECONDS)
    return response




@app.get("/auth/ebay/start")
def ebay_start(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if not configured(settings):
        raise HTTPException(status_code=503, detail=f"eBay {settings.ebay_env.title()} OAuth is not configured")
    state_value = new_oauth_state(session)
    record_event(session, actor_type="user", actor_id=user, action="ebay.oauth.start", resource_type="ebay_connection", resource_id=settings.ebay_env, outcome="success", correlation_id=correlation_id(), details={})
    try:
        target = authorization_url(settings.ebay_client_id, settings.ebay_runame, state_value, environment=settings.ebay_env)
    except OAuthError:
        raise HTTPException(status_code=503, detail=f"eBay {settings.ebay_env.title()} OAuth configuration is invalid")
    return RedirectResponse(target, status_code=302)


@app.get("/auth/ebay/callback")
async def ebay_callback(request: Request, state: str | None = None, code: str | None = None, error: str | None = None, session: Session = Depends(get_session)):
    settings = request.app.state.settings
    if error or not code or not consume_oauth_state(session, state):
        record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id=settings.ebay_env, outcome="denied", correlation_id=correlation_id(), details={"error": "provider_error" if error else "invalid_callback"})
        raise HTTPException(status_code=400, detail="invalid or expired OAuth callback")
    cipher, oauth = ebay_services(settings)
    try:
        payload = await oauth.exchange_code(code, settings.ebay_runame)
        store_user_tokens(session, cipher, payload, environment=settings.ebay_env)
    except OAuthError:
        record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id=settings.ebay_env, outcome="failed", correlation_id=correlation_id(), details={"error": "token_exchange_failed"})
        raise HTTPException(status_code=502, detail=f"eBay {settings.ebay_env.title()} authorization failed")
    record_event(session, actor_type="external", actor_id="ebay", action="ebay.oauth.callback", resource_type="ebay_connection", resource_id=settings.ebay_env, outcome="success", correlation_id=correlation_id(), details={})
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/api/ebay/status")
def ebay_status(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    record = session.get(EbayCredential, 1)
    return {"environment": app.state.settings.ebay_env.upper(), "configured": configured(app.state.settings), "connected": bool(record), "access_token_expires_at": record.access_token_expires_at if record else None, "last_successful_sync": record.last_successful_sync_at if record else None}


@app.get("/api/ebay/listings")
async def ebay_listings(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        payload = await EbaySandboxReadAdapter(token, environment=settings.ebay_env).get_listings()
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
        return {"environment": settings.ebay_env.upper(), "count": len(items), "items": [{k: v for k, v in item.items() if k != "raw"} for item in items]}
    except (OAuthError, EbayApiError, InvalidOperation):
        record_event(session, actor_type="user", actor_id=user, action="ebay.listings.read", resource_type="ebay_listing", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail=f"eBay {settings.ebay_env.title()} listing retrieval failed")


@app.get("/api/ebay/seller")
async def ebay_seller(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    settings = request.app.state.settings
    cipher, oauth = ebay_services(settings)
    try:
        token = await valid_user_access_token(session, cipher, oauth)
        seller = await EbaySandboxReadAdapter(token, environment=settings.ebay_env).get_seller()
        record_event(session, actor_type="user", actor_id=user, action="ebay.seller.read", resource_type="ebay_seller", resource_id=None, outcome="success", correlation_id=correlation_id(), details={})
        return {"environment": settings.ebay_env.upper(), "seller": seller}
    except (OAuthError, EbayApiError):
        record_event(session, actor_type="user", actor_id=user, action="ebay.seller.read", resource_type="ebay_seller", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail=f"eBay {settings.ebay_env.title()} seller retrieval failed")


@app.get("/api/ebay/market-search")
async def ebay_market_search(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    country: str = Query("AU", min_length=2, max_length=2),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    country = country.upper()
    if country not in allowed_country_codes(settings.market_allowed_countries):
        raise HTTPException(status_code=400, detail="Seller location must be Australia or Japan")
    _, oauth = ebay_services(settings)
    try:
        token_payload = await oauth.application_token()
        payload = await EbaySandboxReadAdapter(
            token_payload["access_token"], environment=settings.ebay_env,
        ).search_market(q, marketplace="EBAY_AU", item_location_country=country)
        items = [
            item for item in normalize_market(payload)
            if (item.get("marketplace") or "").upper() == country
        ]
        record_event(session, actor_type="user", actor_id=user, action="ebay.market_search.read", resource_type="market_observation", resource_id=None, outcome="success", correlation_id=correlation_id(), details={"query": q, "country": country, "count": len(items)})
        return {"environment": settings.ebay_env.upper(), "country": country, "count": len(items), "items": items}
    except (OAuthError, EbayApiError, KeyError):
        record_event(session, actor_type="user", actor_id=user, action="ebay.market_search.read", resource_type="market_observation", resource_id=None, outcome="failed", correlation_id=correlation_id(), details={"error": "ebay_api_failure"})
        raise HTTPException(status_code=502, detail=f"eBay {settings.ebay_env.title()} market search failed")


def _deprecated_control_plane_write(session: Session, user: str, resource: str) -> None:
    record_event(
        session, actor_type="user", actor_id=user, action=f"deprecated.{resource}.write",
        resource_type=resource, resource_id=None, outcome="rejected",
        correlation_id=correlation_id(), details={"replacement": "workflow-specific operations"},
    )
    raise HTTPException(
        status_code=410,
        detail=f"the generic {resource} endpoint is deprecated; use the workflow-specific operation",
    )


@app.post("/approvals")
def deprecated_approval_write(
    session: Session = Depends(get_session), user: str = Depends(require_dashboard_user),
) -> None:
    _deprecated_control_plane_write(session, user, "approval")


@app.post("/jobs")
def deprecated_job_write(
    session: Session = Depends(get_session), user: str = Depends(require_dashboard_user),
) -> None:
    _deprecated_control_plane_write(session, user, "job")
