"""One button for the eBay File Exchange export -- Module 28.

Was two manual runs for one outcome: `scripts/export_ebay_bulk_listing.py`
builds `ebay_bulk_listing.csv` from active shop listings, then
`scripts/convert_to_ebay_template.py` reshapes it against eBay's own
downloaded template into `ebay_bulk_listing_ready.csv`, the file Seller Hub
actually accepts. This runs both in sequence and hands back the finished
file as a download -- one click instead of two commands.

Calls each script's own `main(argv)` rather than reimplementing either --
the same reason `price_review.py`'s refresh buttons call
`backfill_pokemonpricetracker_prices.main()` and `import_ebay_prices.main()`
instead of a second copy of that logic. Both scripts still work exactly as
before from a terminal; nothing here changes their CLI behaviour.

The four File Exchange fields (ship-from location, dispatch days, payment
and return policy *names*) are a second, separate set of values from the
policy *ids* /settings already reports on -- eBay gives no API to
translate one into the other, so they live in their own config fields
(config.py) rather than being derived. Missing ones are named specifically,
the same pattern /settings uses for its own "listing prerequisites" row,
rather than the export silently running with a REPLACE_ME placeholder
baked into the file eBay would then reject.
"""

import uuid
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .ui import brand_header, page

router = APIRouter()

REQUIRED_SETTINGS = (
    ("ebay_ship_from_location", "ship-from location"),
    ("ebay_dispatch_days", "dispatch days"),
    ("ebay_listing_payment_policy_name", "payment policy name"),
    ("ebay_listing_return_policy_name", "return policy name"),
    ("ebay_letter_shipping_policy_name", "letter shipping policy name"),
    ("ebay_parcel_shipping_policy_name", "parcel shipping policy name"),
)


def _missing_settings(settings) -> list[str]:
    return [label for field, label in REQUIRED_SETTINGS if not getattr(settings, field, None)]


@router.get("/ebay-export", response_class=HTMLResponse)
def ebay_export_page(request: Request, error: str = "",
                     user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    settings = request.app.state.settings
    missing = _missing_settings(settings)

    if missing:
        gate = (
            f"<div class='panel'><p class='linkfill-note bad'>Can't build the export -- "
            f"missing from .env.local: {escape(', '.join(missing))}.</p>"
            "<p class='subtitle'>These are the human-readable Business Policy names Seller Hub shows you "
            "(not the numeric ids /settings already checks) -- eBay has no API to look one up from the other, "
            "so they're typed once into config rather than asked for on every export.</p></div>"
        )
        return HTMLResponse(page("EzBay — eBay export", brand_header("eBay export") + gate))

    error_html = f"<p class='linkfill-note bad'>{escape(error)}</p>" if error else ""
    body = (
        brand_header("eBay export")
        + "<p class='subtitle'>Builds the File Exchange CSV Seller Hub accepts, in one click -- "
          "active shop listings, priced, categorised, and ready to upload.</p>"
        + error_html
        + "<div class='panel'><form method='post' action='/ebay-export/build' class='calc-form'>"
        + "<label>What to include"
          "<select name='mode'>"
          "<option value='both'>Singles and Bulk Master lots</option>"
          "<option value='singles-only'>Singles only</option>"
          "<option value='lots-only'>Bulk Master lots only</option>"
          "</select></label>"
        + "<button type='submit'>Build &amp; download</button>"
        + "</form></div>"
    )
    return HTMLResponse(page("EzBay — eBay export", body))


def _record(session: Session, user: str, correlation_id: str, outcome: str, details: dict) -> None:
    record_event(session, actor_type="user", actor_id=user, action="ebay.bulk_export",
                resource_type="ebay_export", resource_id=None, outcome=outcome,
                correlation_id=correlation_id, details=details)


@router.post("/ebay-export/build")
def ebay_export_build(request: Request, mode: str = Form("both"),
                      user: str = Depends(require_dashboard_user),
                      session: Session = Depends(get_session)) -> Response:
    from scripts.convert_to_ebay_template import main as convert_main
    from scripts.export_ebay_bulk_listing import main as export_main

    settings = request.app.state.settings
    missing = _missing_settings(settings)
    correlation_id = str(uuid.uuid4())
    if missing:
        _record(session, user, correlation_id, "failed", {"stage": "config", "missing": missing})
        return RedirectResponse(f"/ebay-export?error={quote('Missing config: ' + ', '.join(missing))}", status_code=303)

    # --letter-policy/--parcel-policy were being left out here entirely,
    # so every export silently fell back to the script's own placeholder
    # names -- caught by actually running this end to end rather than
    # trusting the settings wiring looked complete.
    export_args = [
        "--letter-policy", settings.ebay_letter_shipping_policy_name,
        "--parcel-policy", settings.ebay_parcel_shipping_policy_name,
    ]
    if mode == "singles-only":
        export_args.append("--singles-only")
    elif mode == "lots-only":
        export_args.append("--lots-only")

    export_code = export_main(export_args)
    if export_code != 0:
        _record(session, user, correlation_id, "failed", {"stage": "export", "exit_code": export_code})
        return RedirectResponse(f"/ebay-export?error={quote(f'Export step failed (exit code {export_code})')}", status_code=303)

    convert_code = convert_main([
        "--location", settings.ebay_ship_from_location,
        "--dispatch-days", str(settings.ebay_dispatch_days),
        "--payment-policy", settings.ebay_listing_payment_policy_name,
        "--return-policy", settings.ebay_listing_return_policy_name,
    ])
    if convert_code != 0:
        _record(session, user, correlation_id, "failed", {"stage": "convert", "exit_code": convert_code})
        return RedirectResponse(f"/ebay-export?error={quote(f'Template conversion failed (exit code {convert_code})')}", status_code=303)

    ready_path = Path("ebay_bulk_listing_ready.csv")
    content = ready_path.read_bytes() if ready_path.exists() else b""
    _record(session, user, correlation_id, "success", {"mode": mode, "bytes": len(content)})

    return Response(
        content=content, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ebay_bulk_listing_ready.csv"},
    )
