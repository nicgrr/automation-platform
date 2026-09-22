"""Review-then-export for the eBay File Exchange bulk upload -- Module 28.

Was a single "Build & download" button that ran export_ebay_bulk_listing.py
then convert_to_ebay_template.py blind and handed back a CSV with no way to
see what was in it first, or leave anything out. This is the same two
scripts' own row-building functions (build_set_grouped_rows/build_lot_rows,
convert_row/build_variation_group), called directly in-process against the
live database on every page load -- not a cached file, so what you see is
always exactly what's in ShopListing/BulkLot right now -- with a checkbox
per item so a listing can be left out of one export without touching its
status in the database.

Three populations, one export:
  - Singles       every active ShopListing whose set has fewer than
                  MIN_SET_SIZE_FOR_VARIATION_LISTING active listings (see
                  export_ebay_bulk_listing.py's build_set_grouped_rows)
  - Bulk Master lots   read-only here, not selectable -- eBay's condition
                  ID for category 183455 (Mixed Card Lots) is still
                  unverified (4000 confirmed *rejected* by a real upload,
                  see convert_to_ebay_template.py); shipping a lot row with
                  a placeholder condition ID would produce a file eBay
                  rejects, so this stays informational until that's found.
  - Variation groups   every set that reached MIN_SET_SIZE_FOR_VARIATION_LISTING,
                  grouped into one "pick your card" listing per set at each
                  card's own real price, exactly as build_variation_group
                  produces (parent + child rows). Nic's explicit request
                  2026-09-22 ("every set becomes one listing", full market
                  price, not just the cheap commons a Bulk Master lot
                  already covers) -- replaced this page's earlier use of
                  export_ebay_bulk_listing.build_variation_rows, which is
                  still there unchanged for whatever else calls it (that
                  population is price-capped and lot-gated on purpose, a
                  different, earlier request -- see its own docstring).

Selection is by SKU (ShopListing.id), submitted as repeated `sku=` form
fields and re-applied against a *fresh* rebuild at submit time -- never
trusting hidden state from the page load, so a listing that changed price
or sold out between opening this page and clicking Export can't ship stale.

The final CSV is assembled in memory (io.StringIO) rather than round-
tripping through the two scripts' own file-writing CLI paths, since
combining three populations that main() treats as mutually exclusive
(--variations vs. not) into one download needs their row-building
functions directly rather than main()'s argv handling.
"""

import csv
import io
import uuid
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import ShopListingStatus
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

# The real template downloaded from Seller Hub -> Reports -> Uploads ->
# Get template -- same file convert_to_ebay_template.py defaults to.
TEMPLATE_PATH = "ebay-category-listing-template.csv"


def _missing_settings(settings) -> list[str]:
    return [label for field, label in REQUIRED_SETTINGS if not getattr(settings, field, None)]


def _convert_args(settings) -> SimpleNamespace:
    from scripts.convert_to_ebay_template import DEFAULT_CONDITION_ID, PLACEHOLDER

    return SimpleNamespace(
        location=settings.ebay_ship_from_location,
        dispatch_days=str(settings.ebay_dispatch_days),
        payment_policy=settings.ebay_listing_payment_policy_name,
        return_policy=settings.ebay_listing_return_policy_name,
        condition_id=DEFAULT_CONDITION_ID,
        # No verified value exists yet -- see the module docstring. Lots
        # are display-only below and never reach convert_row with this,
        # but it's needed for the SimpleNamespace to look like real argparse
        # output if that ever changes.
        lot_condition_id=PLACEHOLDER,
    )


# Nic's own choice, 2026-09-22 (asked for "45 listings, one per set" then
# picked this cutoff from 40/36/33-set options at 2/3/5 cards): a set with
# at least this many active listings gets one "pick your card" variation
# listing; anything smaller stays an individual single. See
# build_set_grouped_rows -- this replaced the page's earlier use of
# build_variation_rows (cheap-commons-in-an-active-lot only, unlimited
# price ceiling here instead, at Nic's explicit choice of full market
# price over discounting expensive cards the way Normals already were).
MIN_SET_SIZE_FOR_VARIATION_LISTING = 2


def _build_populations(session: Session, settings):
    """Fresh from the database every call -- see module docstring on why
    this never reads a cached file. Returns (singles, lots, variation_groups,
    skipped) where variation_groups is {set_name: [row, ...]} and skipped is
    every skip reason from both builders combined."""
    from scripts.export_ebay_bulk_listing import build_lot_rows, build_set_grouped_rows

    status = ShopListingStatus.ACTIVE
    letter = settings.ebay_letter_shipping_policy_name
    parcel = settings.ebay_parcel_shipping_policy_name

    singles, groups, skipped_s = build_set_grouped_rows(
        session, status=status, letter_policy=letter, min_set_size=MIN_SET_SIZE_FOR_VARIATION_LISTING,
    )
    lots, skipped_l = build_lot_rows(session, status=status, lot_max_cards=20,
                                     letter_policy=letter, parcel_policy=parcel)

    return singles, lots, groups, skipped_s + skipped_l


def _group_by_set(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["set_name"] or "(no set)", []).append(row)
    return dict(sorted(groups.items()))


def _row_html(row: dict, *, checked: bool, extra_label: str = "") -> str:
    price = Decimal(row["price"])
    return (
        "<label class='xrow'>"
        f"<input type='checkbox' name='sku' value='{escape(row['sku'])}' {'checked' if checked else ''}>"
        f"<img class='xthumb' src='{escape(row['image_url'])}' loading='lazy' alt=''>"
        "<span class='xinfo'>"
        f"<span class='xtitle'>{escape(row['title'])}</span>"
        f"<span class='xmeta'>{row['condition'] and escape(row['condition']) + ' &middot; ' or ''}"
        f"qty {escape(str(row['quantity']))}"
        f"{extra_label}</span>"
        "</span>"
        f"<span class='xprice'>{escape(row['currency'])} ${price:,.2f}</span>"
        "</label>"
    )


def _set_group_html(set_name: str, rows: list[dict], *, group_id: str) -> str:
    total = sum(Decimal(r["price"]) for r in rows)
    items = "".join(_row_html(r, checked=True) for r in rows)
    return (
        f"<details class='xgroup'{' open' if len(rows) <= 5 else ''}>"
        f"<summary><label class='xgroup-all'>"
        f"<input type='checkbox' checked onchange=\"toggleGroup('{group_id}', this.checked)\">"
        f"<b>{escape(set_name)}</b></label>"
        f"<span class='xgroup-stats'>{len(rows)} card{'s' if len(rows) != 1 else ''} &middot; ${total:,.2f}</span>"
        "</summary>"
        f"<div id='{group_id}' class='xrows'>{items}</div>"
        "</details>"
    )


def _lot_row_html(row: dict) -> str:
    price = Decimal(row["price"])
    return (
        "<div class='xrow xrow-static'>"
        f"<img class='xthumb' src='{escape(row['image_url'])}' loading='lazy' alt=''>"
        "<span class='xinfo'>"
        f"<span class='xtitle'>{escape(row['title'])}</span>"
        f"<span class='xmeta'>qty {escape(str(row['quantity']))} &middot; {escape(row['shipping_policy'])}</span>"
        "</span>"
        f"<span class='xprice'>{escape(row['currency'])} ${price:,.2f}</span>"
        "</div>"
    )


def _skipped_html(skipped: list[str]) -> str:
    """`skipped` mixes two very different things and the wording has to
    keep them apart: a "covered by an active bulk lot" reason from
    build_single_rows isn't a card dropped from the export -- it's exactly
    why that same card shows up in the Variation groups section instead
    (see build_variation_rows' docstring, "the exact inverse population").
    Only a missing-image reason is a card genuinely left out of everything."""
    if not skipped:
        return ""
    moved = [s for s in skipped if "already covered by an active bulk lot" in s]
    missing_image = [s for s in skipped if s not in moved]
    parts = []
    if moved:
        items = "".join(f"<li>{escape(reason)}</li>" for reason in moved)
        parts.append(
            f"<details class='panel'><summary>{len(moved)} single{'s' if len(moved) != 1 else ''} moved to "
            "Variation groups instead</summary>"
            f"<p class='subtitle'>Already swept into an active Bulk Master lot for their set, so they're offered "
            f"below as a pick-your-card variation instead of a duplicate single listing.</p>"
            f"<ul class='xskip'>{items}</ul></details>"
        )
    if missing_image:
        items = "".join(f"<li>{escape(reason)}</li>" for reason in missing_image)
        parts.append(
            f"<details class='panel'><summary><b>{len(missing_image)} left out entirely</b> "
            "&mdash; no publicly-hosted photo on file</summary>"
            f"<ul class='xskip'>{items}</ul></details>"
        )
    return "".join(parts)


@router.get("/ebay-export", response_class=HTMLResponse)
def ebay_export_page(request: Request, error: str = "",
                     user: str = Depends(require_dashboard_user),
                     session: Session = Depends(get_session)) -> HTMLResponse:
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
        return HTMLResponse(page("EzBay — eBay bulk upload file", brand_header("eBay bulk upload file") + gate))

    singles, lots, variation_groups, skipped = _build_populations(session, settings)
    single_groups = _group_by_set(singles)

    error_html = f"<p class='linkfill-note bad'>{escape(error)}</p>" if error else ""

    singles_total = sum(Decimal(r["price"]) for r in singles)
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Singles</div><div class='value'>{len(singles)}</div>"
        f"<div class='value-sub'>${singles_total:,.2f} if all included</div></div>"
        f"<div class='stat-card'><div class='label'>Bulk Master lots</div><div class='value'>{len(lots)}</div>"
        "<div class='value-sub'>not exportable yet, see below</div></div>"
        f"<div class='stat-card'><div class='label'>Variation candidates</div>"
        f"<div class='value'>{sum(len(v) for v in variation_groups.values())}</div>"
        f"<div class='value-sub'>across {len(variation_groups)} set{'s' if len(variation_groups) != 1 else ''}</div></div>"
        "</div>"
    )

    singles_html = (
        "".join(_set_group_html(name, rows, group_id=f"s-{i}") for i, (name, rows) in enumerate(single_groups.items()))
        if singles else "<p class='muted'>No active singles ready to export.</p>"
    )
    variation_html = (
        "".join(_set_group_html(name, rows, group_id=f"v-{i}") for i, (name, rows) in enumerate(variation_groups.items()))
        if variation_groups else "<p class='muted'>No variation-group candidates right now.</p>"
    )
    lots_html = (
        "<div class='panel-note'>eBay's condition ID for category 183455 (Mixed Card Lots) is still unverified -- "
        "4000 was confirmed <b>rejected</b> for lots by a real upload, and no replacement value has been found. "
        "Listing one lot by hand through Seller Hub's normal flow is the way to find the real value; until then, "
        "lots stay visible here for review but are left out of every export so this can't ship a broken row.</div>"
        + ("".join(_lot_row_html(r) for r in lots) if lots else "<p class='muted'>No active Bulk Master lots.</p>")
    )

    body = (
        brand_header("eBay bulk upload file")
        + error_html + stats
        + "<form method='post' action='/ebay-export/build' data-no-progress>"
        + f"<div class='panel'><h3>Singles <button type='button' class='xlink' onclick=\"toggleAll('s', true)\">select all</button>"
        + f" &middot; <button type='button' class='xlink' onclick=\"toggleAll('s', false)\">none</button></h3>{singles_html}</div>"
        + f"<div class='panel'><h3>Variation groups <button type='button' class='xlink' onclick=\"toggleAll('v', true)\">select all</button>"
        + f" &middot; <button type='button' class='xlink' onclick=\"toggleAll('v', false)\">none</button></h3>"
          f"<p class='subtitle'>Every set with {MIN_SET_SIZE_FOR_VARIATION_LISTING}+ active listings, offered as one "
          "&quot;pick your card&quot; listing so a buyer can choose exactly which copy they want, each at its own "
          "real price. A set with fewer cards stays as individual Singles above instead.</p>"
        + f"{variation_html}</div>"
        + f"<div class='panel'><h3>Bulk Master lots</h3>{lots_html}</div>"
        + _skipped_html(skipped)
        + "<div class='panel xsubmit'><button type='submit'>Export selected &amp; download</button></div>"
        + "</form>"
        + _SCRIPT + _STYLE
    )
    return HTMLResponse(page("EzBay — eBay bulk upload file", body))


def _record(session: Session, user: str, correlation_id: str, outcome: str, details: dict) -> None:
    record_event(session, actor_type="user", actor_id=user, action="ebay.bulk_export",
                resource_type="ebay_export", resource_id=None, outcome=outcome,
                correlation_id=correlation_id, details=details)


@router.post("/ebay-export/build")
def ebay_export_build(request: Request, sku: list[str] = Form(default=[]),
                      user: str = Depends(require_dashboard_user),
                      session: Session = Depends(get_session)) -> Response:
    from scripts.convert_to_ebay_template import build_variation_group, convert_row, read_ebay_header

    settings = request.app.state.settings
    missing = _missing_settings(settings)
    correlation_id = str(uuid.uuid4())
    if missing:
        _record(session, user, correlation_id, "failed", {"stage": "config", "missing": missing})
        return RedirectResponse(f"/ebay-export?error={quote('Missing config: ' + ', '.join(missing))}", status_code=303)

    selected = set(sku)
    if not selected:
        return RedirectResponse(f"/ebay-export?error={quote('Nothing selected -- tick at least one listing first.')}", status_code=303)

    args = _convert_args(settings)
    singles, _lots, variation_groups, _skipped = _build_populations(session, settings)

    out_rows = [convert_row(r, args) for r in singles if r["sku"] in selected]

    variation_skipped: list[str] = []
    for set_name, cards in variation_groups.items():
        chosen = [c for c in cards if c["sku"] in selected]
        if not chosen:
            continue
        group_rows, group_skipped = build_variation_group(set_name, chosen, args)
        out_rows.extend(group_rows)
        variation_skipped.extend(group_skipped)

    if not out_rows:
        _record(session, user, correlation_id, "failed", {"stage": "build", "reason": "no rows survived selection/grouping"})
        return RedirectResponse(
            f"/ebay-export?error={quote('Nothing exportable came out of that selection (every selected variation card was a duplicate number in its set).')}",
            status_code=303,
        )

    try:
        info_row, header = read_ebay_header(TEMPLATE_PATH)
    except SystemExit as exc:
        _record(session, user, correlation_id, "failed", {"stage": "template", "error": str(exc)})
        return RedirectResponse(f"/ebay-export?error={quote(str(exc))}", status_code=303)

    header_set = set(header)
    out_rows = [{k: v for k, v in row.items() if k in header_set} for row in out_rows]

    buf = io.StringIO()
    buf.write(info_row + "\r\n")
    writer = csv.DictWriter(buf, fieldnames=header, restval="")
    writer.writeheader()
    writer.writerows(out_rows)
    content = buf.getvalue().encode("utf-8-sig")

    _record(session, user, correlation_id, "success", {
        "rows": len(out_rows), "selected": len(selected), "bytes": len(content),
        "variation_skipped": len(variation_skipped),
    })

    return Response(
        content=content, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ebay_bulk_listing_ready.csv"},
    )


_SCRIPT = """<script>
function toggleGroup(id, checked) {
  document.getElementById(id).querySelectorAll("input[type=checkbox]").forEach(cb => cb.checked = checked);
}
function toggleAll(prefix, checked) {
  document.querySelectorAll("details.xgroup").forEach(d => {
    if (d.querySelector(".xrows").id.startsWith(prefix + "-")) {
      d.querySelectorAll("input[type=checkbox]").forEach(cb => cb.checked = checked);
    }
  });
}
</script>"""

_STYLE = """<style>
.xgroup{border:1px solid var(--panel-border);border-radius:10px;margin-bottom:8px;overflow:hidden}
.xgroup summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;
  cursor:pointer;background:var(--surface-sunken);list-style:none}
.xgroup summary::-webkit-details-marker{display:none}
.xgroup-all{display:flex;align-items:center;gap:8px;font-size:14px}
.xgroup-stats{font-size:12.5px;color:var(--text-dim);white-space:nowrap}
.xrows{display:flex;flex-direction:column}
.xrow{display:flex;align-items:center;gap:10px;padding:8px 12px;border-top:1px solid var(--panel-border);
  cursor:pointer}
.xrow-static{cursor:default}
.xrow:hover{background:var(--surface-sunken)}
.xthumb{width:36px;height:50px;object-fit:contain;border-radius:4px;background:var(--surface-sunken);flex:none}
.xinfo{display:flex;flex-direction:column;flex:1;min-width:0}
.xtitle{font-size:13.5px;font-weight:600;word-break:break-word}
.xmeta{font-size:11.5px;color:var(--text-dim)}
.xprice{font-size:13.5px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.xlink{background:none;border:none;color:var(--accent);font-size:12.5px;cursor:pointer;padding:0}
.xskip{margin:8px 0 0;padding-left:20px;font-size:12.5px;color:var(--text-dim)}
.panel-note{background:rgba(212,165,39,.10);border:1px solid rgba(212,165,39,.35);border-radius:10px;
  padding:10px 12px;font-size:13px;line-height:1.45;margin-bottom:10px}
.xsubmit{text-align:right}
@media (max-width:560px){
  .xtitle{font-size:13px}
}
</style>"""
