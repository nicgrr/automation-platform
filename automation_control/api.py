import csv
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError, EbaySandboxReadAdapter, normalize_inventory, normalize_market
from .audit import record_event
from .auth import create_session, require_dashboard_user, verify_password
from .capture import router as cards_router
from .config import Settings, get_settings
from .database import Base, engine, get_session
from .ebay_oauth import EbayOAuthClient, OAuthError, TokenCipher, authorization_url, consume_oauth_state, new_oauth_state, store_user_tokens, valid_sandbox_client_id, valid_sandbox_runame, valid_user_access_token
from .models import Approval, AuditEvent, CapturedCard, CardCaptureStatus, EbayCredential, EbayListing, JobRun, PricingStatus
from .price_review import router as pricing_router
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
        brand_header("Private Control Plane")
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


LISTING_SUMMARY_COLUMNS = ["CustomLabel", "*Title", "*StartPrice", "BestOfferAutoAcceptPrice", "MinimumBestOfferPrice"]


def _read_csv_rows(path: Path) -> tuple[list[str], list[list[str]]] | None:
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return None
    return rows[0], rows[1:]


def _html_table(header: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{escape(col)}</th>" for col in header)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _listing_pipeline_section(output_dir: Path) -> str:
    bulk = _read_csv_rows(output_dir / "ebay_bulk_upload.csv")
    checklist = _read_csv_rows(output_dir / "image_naming_checklist.csv")
    report = _read_csv_rows(output_dir / "validation_report.csv")

    if bulk is None and checklist is None and report is None:
        return f"<div class='panel'><h2>Listing pipeline</h2><p>No output yet in {escape(str(output_dir))} — run the CLI to generate it.</p></div>"

    parts = ["<div class='panel'><h2>Listing pipeline</h2>"]

    if bulk:
        header, rows = bulk
        indices = [header.index(col) for col in LISTING_SUMMARY_COLUMNS if col in header]
        summary_header = [header[i] for i in indices]
        summary_rows = [[row[i] for i in indices] for row in rows]
        parts.append(f"<h3>Listings ({len(rows)})</h3>")
        parts.append(_html_table(summary_header, summary_rows))
    else:
        parts.append("<h3>Listings</h3><p>No ebay_bulk_upload.csv found.</p>")

    if checklist:
        header, rows = checklist
        parts.append(f"<h3>Image checklist ({len(rows)} photos)</h3>")
        parts.append(_html_table(header, rows))
    else:
        parts.append("<h3>Image checklist</h3><p>No image_naming_checklist.csv found.</p>")

    if report:
        header, rows = report
        parts.append(f"<h3>Validation warnings ({len(rows)})</h3>")
        parts.append(_html_table(header, rows) if rows else "<p>None.</p>")
    else:
        parts.append("<h3>Validation warnings</h3><p>No validation_report.csv found.</p>")

    parts.append("</div>")
    return "".join(parts)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> str:
    credential = session.get(EbayCredential, 1)
    listing_count = len(session.scalars(select(EbayListing)).all())
    events = session.scalars(select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(10)).all()
    event_html = "".join(f"<li>{escape(str(e.occurred_at))} — {escape(e.action)} — {escape(e.outcome)}</li>" for e in events) or "<li>No events</li>"
    connected = bool(credential)
    connection_pill = pill("Connected", "ok") if connected else pill("Not connected", "bad")
    last_sync = credential.last_successful_sync_at if credential and credential.last_successful_sync_at else "Never"
    output_dir = Path(request.app.state.settings.listing_pipeline_output_dir)
    pipeline_html = _listing_pipeline_section(output_dir)
    pending_review_count = len(session.scalars(select(CapturedCard).where(CapturedCard.status == CardCaptureStatus.PENDING_REVIEW)).all())
    pending_price_count = len(session.scalars(select(CapturedCard).where(CapturedCard.pricing_status == PricingStatus.PENDING_PRICE_REVIEW)).all())
    checklist = _read_csv_rows(output_dir / "image_naming_checklist.csv")
    photo_count = len(checklist[1]) if checklist else 0

    stat_grid = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Platform status</div><div class='value'>{pill('OK', 'ok')}</div></div>"
        f"<div class='stat-card'><div class='label'>eBay connection</div><div class='value'>{connection_pill}</div></div>"
        f"<div class='stat-card'><div class='label'>eBay environment</div><div class='value'>{pill('Sandbox', 'neutral')}</div></div>"
        f"<div class='stat-card'><div class='label'>Last successful sync</div><div class='value'>{escape(str(last_sync))}</div></div>"
        f"<div class='stat-card'><div class='label'>Listings retrieved</div><div class='value'>{listing_count}</div></div>"
        f"<div class='stat-card'><div class='label'>Cards pending review</div><div class='value'>{pending_review_count}</div></div>"
        f"<div class='stat-card'><div class='label'>Cards pending price approval</div><div class='value'>{pending_price_count}</div></div>"
        f"<div class='stat-card'><div class='label'>Photos needed</div><div class='value'>{photo_count}</div></div>"
        "</div>"
    )

    body = (
        brand_header("Private Control Plane")
        + "<p class='subtitle'>Trading card listing operations.</p>"
        + stat_grid
        + f"<div class='panel'><h2>eBay Sandbox</h2><p><a class='btn' href='/auth/ebay/start'>Connect eBay Sandbox</a></p></div>"
        + f"<div class='panel'><h2>Card capture</h2><p><a class='btn' href='/cards/capture'>Capture new card</a> &nbsp; <a href='/cards/capture/bulk'>Bulk upload</a> &nbsp; <a href='/cards/review'>Review queue ({pending_review_count})</a> &nbsp; <a href='/cards/pricing'>Price review ({pending_price_count})</a></p></div>"
        + f"<div class='panel'><h2>Recent audit events</h2><ul class='events'>{event_html}</ul></div>"
        + pipeline_html
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
