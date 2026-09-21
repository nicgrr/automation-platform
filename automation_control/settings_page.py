"""What this app is connected to, and whether any of it actually works.

Everything here was already knowable and scattered: the eBay connection
lived only in a 503 message you had to trigger, the API keys only in
`.env.local`, and the background jobs only on the dashboard. Someone
wanting to know "is eBay hooked up?" had no page to look at.

**Nothing on this page is a secret.** Credentials are reported as present
or absent and never rendered, not even truncated -- a masked key still
tells an onlooker its length and prefix, and there is no question this
page answers that needs the value. The one exception is what eBay itself
publishes about a connection: environment, expiry and granted scopes,
none of which can be used to authenticate.

Read-only apart from the Connect button, which hands off to the existing
OAuth flow at /auth/ebay/start. Editing settings from here is deliberately
not offered: they come from `.env.local`, which is also what systemd's
timers read, so a value changed in the browser would disagree with the one
the nightly jobs use.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import Settings
from .database import get_session
from .ebay_oauth import configured, configured_for_writes
from .models import EbayCredential
from .ui import brand_header, page

router = APIRouter()

GOOD, BAD, WARN = "ok", "bad", "warn"


@dataclass
class Row:
    label: str
    state: str
    value: str = ""
    hint: str = ""


def _ebay_rows(settings: Settings, session: Session) -> tuple[list[Row], str | None]:
    """Connection state, plus the one action worth offering."""
    record = session.get(EbayCredential, 1)
    rows: list[Row] = [Row("Configured environment", GOOD, settings.ebay_env)]
    action = None

    if not configured(settings):
        rows.append(Row("OAuth credentials", BAD, "not configured",
                        "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET / EBAY_RUNAME are missing from .env.local"))
        return rows, None

    if record is None:
        rows.append(Row("Connection", BAD, "not connected",
                        "no eBay account has been linked yet"))
        action = "Connect eBay"
    elif record.environment != settings.ebay_env:
        # The failure this page exists to make obvious: a mismatched token
        # fails at the API as a generic auth error, which reads as broken
        # credentials rather than the wrong eBay.
        rows.append(Row("Connection", BAD, f"connected to {record.environment}",
                        f"this app is configured for {settings.ebay_env} -- "
                        "a token from the other environment fails as an ordinary auth error"))
        action = f"Reconnect to {settings.ebay_env}"
    else:
        expiry = record.access_token_expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        expired = expiry is not None and expiry <= datetime.now(UTC)
        rows.append(Row("Connection", WARN if expired else GOOD,
                        f"connected to {record.environment}",
                        "access token has expired; it refreshes automatically on next use"
                        if expired else ""))
        if expiry:
            rows.append(Row("Access token expires", GOOD, f"{expiry:%Y-%m-%d %H:%M} UTC"))
        action = "Reconnect eBay"

    rows.append(Row("Marketplace", GOOD, settings.ebay_marketplace_id))

    from .ebay_listings import _cached_rows

    active = _cached_rows(session)
    last_synced = max((r.observed_at for r in active), default=None)
    if last_synced:
        rows.append(Row("Active eBay listings", GOOD,
                        f"{len(active)} &middot; <a href='/ebay-listings'>view</a>",
                        f"last checked {last_synced:%Y-%m-%d %H:%M} UTC"))
    else:
        # This is exactly the state that prompted the page to exist: the
        # Sell Inventory API this app is built around can report 0 while
        # eBay's own Seller Hub shows real listings, because it only sees
        # listings it created itself. Never checked reads as "unknown",
        # deliberately not "0" -- 0 is a claim about eBay this app cannot
        # make without visiting /ebay-listings at least once.
        # Row.hint is HTML-escaped by _table, Row.value is not -- the link
        # has to live in value or it renders as literal "<a href=...>" text.
        rows.append(Row("Active eBay listings", WARN, "<a href='/ebay-listings'>not checked yet</a>",
                        "the Sell API this app is built around can't see listings created "
                        "outside it (Relist, bulk upload) -- click to check the real API"))
    if configured_for_writes(settings):
        rows.append(Row("Listing prerequisites", GOOD, "payment, postage, returns and location all set"))
    else:
        missing = [name for name, value in (
            ("payment policy", settings.ebay_payment_policy_id),
            ("postage policy", settings.ebay_fulfillment_policy_id),
            ("returns policy", settings.ebay_return_policy_id),
            ("merchant location", settings.ebay_merchant_location_key),
        ) if not value]
        rows.append(Row("Listing prerequisites", BAD, "missing " + ", ".join(missing),
                        "eBay refuses to create an offer without these, so publishing stays blocked"))
    return rows, action


def _integration_rows(settings: Settings) -> list[Row]:
    """Present or absent only -- never the value, not even masked."""
    return [
        Row("Anthropic (photo identification)", GOOD if settings.anthropic_api_key else BAD,
            "configured" if settings.anthropic_api_key else "not configured",
            "" if settings.anthropic_api_key else "capture and link-fill cannot identify anything"),
        Row("Apify (Google Lens)", GOOD if settings.apify_token else BAD,
            "configured" if settings.apify_token else "not configured",
            "" if settings.apify_token else "reverse image search is unavailable"),
        Row("pokemonpricetracker.com", GOOD if settings.pokemonpricetracker_api_key else BAD,
            "configured" if settings.pokemonpricetracker_api_key else "not configured",
            "" if settings.pokemonpricetracker_api_key else "the nightly price backfill does nothing"),
        Row("pokemontcg.io", GOOD if settings.pokemontcg_api_key else WARN,
            "configured" if settings.pokemontcg_api_key else "anonymous",
            "" if settings.pokemontcg_api_key else "works without a key, at a lower rate limit"),
        Row("Stripe (shop checkout)", GOOD if settings.stripe_secret_key else WARN,
            "configured" if settings.stripe_secret_key else "not configured",
            "" if settings.stripe_secret_key else "the storefront cannot take payment"),
    ]


def _job_rows() -> list[Row]:
    from .job_health import FAILED, OK, OVERDUE, RUNNING, all_statuses

    mapping = {OK: GOOD, RUNNING: GOOD, FAILED: BAD, OVERDUE: BAD}
    rows = []
    for status in all_statuses():
        value = status.state
        if status.last_run and status.state in (OK, RUNNING):
            value = f"{status.state} &middot; last ran {status.last_run:%a %H:%M}"
        rows.append(Row(status.job.label, mapping.get(status.state, WARN), value, status.detail))
    return rows


def _table(rows: list[Row]) -> str:
    body = "".join(
        f"<tr><th>{escape(r.label)}</th>"
        f"<td><span class='set-pill {r.state}'>{r.value}</span>"
        + (f"<span class='set-hint'>{escape(r.hint)}</span>" if r.hint else "")
        + "</td></tr>"
        for r in rows
    )
    return f"<div class='table-wrap'><table class='set-table'>{body}</table></div>"


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: str = Depends(require_dashboard_user),
                  session: Session = Depends(get_session)) -> HTMLResponse:
    settings: Settings = request.app.state.settings
    ebay_rows, action = _ebay_rows(settings, session)

    connect = (f"<a class='btn' href='/auth/ebay/start'>{escape(action)}</a>" if action else "")
    body = (
        brand_header("Settings")
        + f"<div class='panel'><div class='set-head'><h2>eBay</h2>{connect}</div>{_table(ebay_rows)}</div>"
        + f"<div class='panel'><h2>Integrations</h2>"
          "<p class='subtitle'>Whether a credential is present. Keys are never displayed.</p>"
        + _table(_integration_rows(settings)) + "</div>"
        + f"<div class='panel'><div class='set-head'><h2>Background jobs</h2>"
          "<a class='btn ghost' href='/scan-ingest'>Logs</a></div>"
        + _table(_job_rows()) + "</div>"
        + f"<div class='panel'><h2>Storage</h2>{_table([
            Row('Captured photos', GOOD, settings.captured_cards_dir),
            Row('Scan watch folder', GOOD, settings.scan_watch_dir),
            Row('Scan archive', GOOD, settings.scan_archive_dir),
            Row('Database', GOOD, settings.automation_database_url.split('///')[-1]),
        ])}</div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Settings", body))


_STYLE = """<style>
.set-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}
.set-head h2{margin:0}
.set-table{width:100%;border-collapse:collapse;font-size:13.5px}
.set-table th{text-align:left;font-weight:600;color:var(--text-dim);padding:9px 12px 9px 0;
  vertical-align:top;white-space:nowrap;width:1%}
.set-table td{padding:9px 0;border-bottom:1px solid var(--panel-border)}
.set-table tr:last-child td{border-bottom:none}
.set-pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600}
.set-pill.ok{background:var(--success-bg);color:var(--success)}
.set-pill.bad{background:var(--warning-bg);color:var(--warning)}
.set-pill.warn{background:var(--surface-sunken);color:var(--text-dim)}
.set-hint{display:block;margin-top:4px;color:var(--text-dim);font-size:12.5px;line-height:1.45}
</style>"""
