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
  - Bulk Master lots   selectable since 2026-09-28. eBay's condition ID
                  for category 183455 (Mixed Card Lots) was unverified for
                  a while (4000, the singles value, confirmed *rejected*
                  by a real upload) -- confirmed instead by asking eBay
                  directly (GetItem against 3 real active lot listings
                  already on this account) rather than listing a test one:
                  all three came back ConditionID=3000 ("Used"), see
                  convert_to_ebay_template.DEFAULT_LOT_CONDITION_ID.
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
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CardSet,
    EbayExportBatch,
    EbayExportItem,
    EbayExportItemStatus,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from .pricing_safety import assess_listing_price
from .listing_quality import australian_active_listings_url, australian_sold_listings_url
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

# One `sku` form field per checkbox on the page -- comfortably above the
# real inventory this export handles (a few thousand active listings),
# comfortably below "someone crafted a request to exhaust the server".
MAX_EXPORT_FIELDS = 20000

# A real eBay result file is only a few hundred KB for thousands of rows.
# This ceiling catches an accidental/wrong upload without buffering an
# arbitrary file into the web worker.
MAX_RESULT_FILE_BYTES = 10 * 1024 * 1024


def _missing_settings(settings) -> list[str]:
    return [label for field, label in REQUIRED_SETTINGS if not getattr(settings, field, None)]


def _convert_args(settings) -> SimpleNamespace:
    from scripts.convert_to_ebay_template import DEFAULT_CONDITION_ID, DEFAULT_LOT_CONDITION_ID

    return SimpleNamespace(
        location=settings.ebay_ship_from_location,
        dispatch_days=str(settings.ebay_dispatch_days),
        payment_policy=settings.ebay_listing_payment_policy_name,
        return_policy=settings.ebay_listing_return_policy_name,
        condition_id=DEFAULT_CONDITION_ID,
        # Confirmed 2026-09-28 -- see DEFAULT_LOT_CONDITION_ID's own
        # comment. Lots are selectable below now that this is real.
        lot_condition_id=DEFAULT_LOT_CONDITION_ID,
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
VARIATION_TITLE_SUFFIX = " - Complete Your Master Set"


def _latest_export_items(session: Session) -> dict[str, EbayExportItem]:
    """The newest ledger entry per SKU.

    A SKU can legitimately have several historical rows (failed upload,
    then a later retry), so eligibility is based on the newest state while
    the older batch history remains intact for diagnosis.
    """
    rows = session.scalars(
        select(EbayExportItem)
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
    ).all()
    latest: dict[str, EbayExportItem] = {}
    for row in rows:
        latest.setdefault(row.sku, row)
    return latest


def _blocks_export(item: EbayExportItem | None) -> bool:
    return bool(item and item.status in (EbayExportItemStatus.PENDING, EbayExportItemStatus.CONFIRMED))


_DIRECT_LINK_BLOCKING_STATUSES = (
    EbayListingLinkStatus.PUBLISHING,
    EbayListingLinkStatus.ACTIVE,
    EbayListingLinkStatus.ENDING,
    # A failed direct run may still have created an Inventory API offer.
    # Its own retry path knows how to discover and reuse that offer; sending
    # the same SKU through File Exchange instead could create a duplicate.
    EbayListingLinkStatus.FAILED,
)


def _direct_links_blocking_export(session: Session) -> dict[str, EbayListingLink]:
    return {
        link.shop_listing_id: link
        for link in session.scalars(
            select(EbayListingLink).where(EbayListingLink.status.in_(_DIRECT_LINK_BLOCKING_STATUSES))
        ).all()
    }


def _active_variation_parents(session: Session) -> dict[str, EbayListing]:
    """Active eBay variation parents keyed by EzBay set name.

    The read-mirror API does not return a CustomLabel/SKU for the existing
    parent listings, so the export ledger cannot identify them by the
    synthetic ``set-variation-*`` SKU.  Their title is deterministic,
    however: every parent created by this exporter is ``SET - Complete Your
    Master Set``.  Matching that exact suffix closes the duplicate-parent
    hole without guessing from ordinary listing titles.
    """
    parents: dict[str, EbayListing] = {}
    for listing in session.scalars(select(EbayListing)).all():
        if (listing.listing_status or "").casefold() != "active":
            continue
        if not listing.title.endswith(VARIATION_TITLE_SUFFIX):
            continue
        set_name = listing.title[:-len(VARIATION_TITLE_SUFFIX)].strip()
        if not set_name:
            continue
        current = parents.get(set_name)
        if current is None or listing.observed_at > current.observed_at:
            parents[set_name] = listing
    return parents


def _parent_started_at(listing: EbayListing) -> datetime | None:
    raw = (listing.raw_snapshot or {}).get("start_time")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _created_after_parent(created_at: datetime | None, parent: EbayListing) -> bool:
    """Whether a local listing is genuinely newer than the eBay parent.

    Missing timestamps are treated conservatively as already listed: an
    active parent is hard evidence, while re-adding an uncertain old child
    can create a duplicate.  SQLite returns the local timestamp naive even
    though the model is timezone-aware, so both sides are normalised first.
    """
    parent_started = _parent_started_at(parent)
    if created_at is None or parent_started is None:
        return False
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(UTC).replace(tzinfo=None)
    return created_at > parent_started


def _build_populations(session: Session, settings):
    """Fresh from the database every call -- see module docstring on why
    this never reads a cached file. Returns (singles, lots, variation_groups,
    skipped, tracked) where variation_groups is {set_name: [row, ...]},
    new_for_existing contains cards scanned/listed locally after an already
    active eBay variation parent was created.  listed_variations contains the
    older cards already represented by those active parents.  skipped is
    every skip reason from both builders combined, and tracked is inventory
    deliberately omitted because its newest export-ledger state is pending
    or confirmed."""
    from scripts.convert_to_ebay_template import _parent_sku
    from scripts.export_ebay_bulk_listing import _enforce_ebay_min_price, build_lot_rows, build_set_grouped_rows

    status = ShopListingStatus.ACTIVE
    letter = settings.ebay_letter_shipping_policy_name
    parcel = settings.ebay_parcel_shipping_policy_name

    singles, groups, skipped_s = build_set_grouped_rows(
        session, status=status, letter_policy=letter, min_set_size=MIN_SET_SIZE_FOR_VARIATION_LISTING,
    )
    lots, skipped_l = build_lot_rows(session, status=status, lot_max_cards=20,
                                     letter_policy=letter, parcel_policy=parcel)
    # eBay rejects a FixedPrice listing under $1 outright -- a real,
    # correctly-priced-by-every-other-rule card can still land under that
    # after a legitimate discount/markup (confirmed live 2026-09-26, two
    # freshly hand-priced alt-arts at $0.26/$0.54 AUD both bounced with
    # ErrorCode 73). Applied here, before any of this function's own
    # downstream logic reads row["price"], so what a human reviews on
    # this page already matches what would actually be sent.
    _enforce_ebay_min_price(singles)
    _enforce_ebay_min_price(lots)
    for cards in groups.values():
        _enforce_ebay_min_price(cards)

    latest = _latest_export_items(session)
    direct_links = _direct_links_blocking_export(session)
    active_parents = _active_variation_parents(session)
    tracked: list[dict] = []
    new_for_existing: list[dict] = []
    listed_variations: dict[str, dict] = {}

    group_skus = [card["sku"] for cards in groups.values() for card in cards]
    created_by_sku = dict(
        session.execute(
            select(ShopListing.id, ShopListing.created_at)
            .where(ShopListing.id.in_(group_skus))
        ).all()
    ) if group_skus else {}

    eligible_singles = []
    for row in singles:
        item = latest.get(row["sku"])
        direct_link = direct_links.get(row["sku"])
        if direct_link is not None:
            tracked.append({"row": row, "status": f"direct_{direct_link.status.value}", "via": "direct"})
        elif _blocks_export(item):
            tracked.append({"row": row, "status": item.status.value, "via": "sku"})
        else:
            eligible_singles.append(row)

    # Lots became selectable 2026-09-28 (DEFAULT_LOT_CONDITION_ID is now a
    # real, confirmed value) -- they need the exact same ledger gate every
    # other selectable population already has, or a lot already exported
    # (pending/confirmed) could be re-selected and submitted a second time.
    eligible_lots = []
    for row in lots:
        item = latest.get(row["sku"])
        if _blocks_export(item):
            tracked.append({"row": row, "status": item.status.value, "via": "sku"})
        else:
            eligible_lots.append(row)
    lots = eligible_lots

    eligible_groups: dict[str, list[dict]] = {}
    for set_name, cards in groups.items():
        active_parent = active_parents.get(set_name)
        if active_parent is not None:
            already_listed = []
            for card in cards:
                child = latest.get(card["sku"])
                direct_link = direct_links.get(card["sku"])
                if direct_link is not None:
                    tracked.append({"row": card, "status": f"direct_{direct_link.status.value}", "via": "direct"})
                elif _blocks_export(child):
                    tracked.append({"row": card, "status": child.status.value, "via": "sku"})
                elif _created_after_parent(created_by_sku.get(card["sku"]), active_parent):
                    new_for_existing.append(card)
                else:
                    already_listed.append(card)
            listed_variations[set_name] = {"listing": active_parent, "rows": already_listed}
            continue

        parent = latest.get(_parent_sku(set_name))
        if _blocks_export(parent):
            # Once eBay has (or may have) created the variation parent,
            # another Add parent would be a duplicate. Children that were
            # actually in that export have their own ledger rows and remain
            # withheld; genuinely new/unselected children are still safe to
            # offer, but as standalone singles until a future Revise workflow
            # can add them to the existing parent.
            for card in cards:
                child = latest.get(card["sku"])
                direct_link = direct_links.get(card["sku"])
                if direct_link is not None:
                    tracked.append({"row": card, "status": f"direct_{direct_link.status.value}", "via": "direct"})
                elif _blocks_export(child):
                    tracked.append({"row": card, "status": child.status.value, "via": "sku"})
                else:
                    eligible_singles.append(card)
            continue

        available = []
        for card in cards:
            item = latest.get(card["sku"])
            direct_link = direct_links.get(card["sku"])
            if direct_link is not None:
                tracked.append({"row": card, "status": f"direct_{direct_link.status.value}", "via": "direct"})
            elif _blocks_export(item):
                tracked.append({"row": card, "status": item.status.value, "via": "sku"})
            else:
                available.append(card)
        if len(available) >= MIN_SET_SIZE_FOR_VARIATION_LISTING:
            eligible_groups[set_name] = available
        elif available:
            # After already-listed cards are removed, a one-card "pick your
            # card" parent no longer makes sense. Export the survivor as a
            # normal single instead.
            eligible_singles.extend(available)

    return (
        eligible_singles,
        lots,
        eligible_groups,
        new_for_existing,
        listed_variations,
        skipped_s + skipped_l,
        tracked,
    )


def _group_by_set(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["set_name"] or "(no set)", []).append(row)
    return dict(sorted(groups.items()))


def _row_html(row: dict, *, checked: bool, extra_label: str = "", pricing_hold=None) -> str:
    price = Decimal(row["price"])
    # English is the overwhelming default and gets no badge; a set-level
    # (Japanese) suffix on the group heading already covers a card that's
    # part of a split group (see export_ebay_bulk_listing.py's
    # _set_group_key), but a lone non-English card that fell out to a
    # single (too few of its language to form its own group) has no
    # group heading to carry that at all -- exactly the gap 2026-09-27's
    # incident was about, so it's flagged here too, at the row itself.
    language = row.get("language") or "English"
    language_badge = f" &middot; <b class='xlang'>{escape(language)}</b>" if language != "English" else ""
    held = bool(pricing_hold and pricing_hold.blocked)
    hold_label = (
        f" &middot; <b class='xhold'>PRICING HOLD &mdash; review floor ${pricing_hold.review_floor:,.2f}</b>"
        f"<span class='xproof'>Proof: {escape(pricing_hold.strongest.label)} AU ${pricing_hold.strongest.value_aud:.2f}, "
        f"observed {pricing_hold.strongest.observed_at.astimezone(UTC):%-d %b %Y} UTC. "
        f"<a href='{escape(australian_active_listings_url(row['title']), quote=True)}' target='_blank' "
        "rel='noopener noreferrer' onclick='event.stopPropagation()'>Active eBay</a> &middot; "
        f"<a href='{escape(australian_sold_listings_url(row['title']), quote=True)}' target='_blank' "
        "rel='noopener noreferrer' onclick='event.stopPropagation()'>Sold eBay</a></span>"
        "<span class='xoverride-note'>Tick to override the hold and include this listing</span>"
        if held else ""
    )
    checkbox_name = "pricing_override" if held else "sku"
    return (
        f"<label class='xrow{' xrow-held' if held else ''}'>"
        f"<input type='checkbox' name='{checkbox_name}' value='{escape(row['sku'])}' "
        f"{'checked' if checked and not held else ''} aria-label='"
        f"{'Override pricing hold and include listing' if held else 'Include listing'}'>"
        f"<img class='xthumb' src='{escape(row['image_url'])}' loading='lazy' alt=''>"
        "<span class='xinfo'>"
        f"<span class='xtitle'>{escape(row['title'])}</span>"
        f"<span class='xmeta'>{row['condition'] and escape(row['condition']) + ' &middot; ' or ''}"
        f"qty {escape(str(row['quantity']))}"
        f"{language_badge}{extra_label}{hold_label}</span>"
        "</span>"
        f"<span class='xprice'>{escape(row['currency'])} ${price:,.2f}</span>"
        "</label>"
    )


def _set_group_html(set_name: str, rows: list[dict], *, group_id: str, pricing_holds=None) -> str:
    total = sum(Decimal(r["price"]) for r in rows)
    pricing_holds = pricing_holds or {}
    items = "".join(
        _row_html(r, checked=True, pricing_hold=pricing_holds.get(r["sku"])) for r in rows
    )
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
    """Selectable now that DEFAULT_LOT_CONDITION_ID is a real, confirmed
    value (2026-09-28) -- same checkbox shape as _row_html, just without
    the language badge a lot (many cards, potentially mixed) can't
    honestly carry as one value."""
    price = Decimal(row["price"])
    return (
        "<label class='xrow'>"
        f"<input type='checkbox' name='sku' value='{escape(row['sku'])}' checked>"
        f"<img class='xthumb' src='{escape(row['image_url'])}' loading='lazy' alt=''>"
        "<span class='xinfo'>"
        f"<span class='xtitle'>{escape(row['title'])}</span>"
        f"<span class='xmeta'>qty {escape(str(row['quantity']))} &middot; {escape(row['shipping_policy'])}</span>"
        "</span>"
        f"<span class='xprice'>{escape(row['currency'])} ${price:,.2f}</span>"
        "</label>"
    )


def _listed_row_html(row: dict) -> str:
    price = Decimal(row["price"])
    return (
        "<div class='xrow xrow-static'>"
        f"<img class='xthumb' src='{escape(row['image_url'])}' loading='lazy' alt=''>"
        "<span class='xinfo'>"
        f"<span class='xtitle'>{escape(row['title'])}</span>"
        f"<span class='xmeta'>{row['condition'] and escape(row['condition']) + ' &middot; ' or ''}"
        f"qty {escape(str(row['quantity']))}</span>"
        "</span>"
        f"<span class='xprice'>{escape(row['currency'])} ${price:,.2f}</span>"
        "</div>"
    )


def _listed_variations_html(groups: dict[str, dict]) -> str:
    if not groups:
        return "<p class='muted'>No active eBay variation listings were found.</p>"
    blocks = []
    for set_name, payload in sorted(groups.items()):
        listing: EbayListing = payload["listing"]
        rows: list[dict] = payload["rows"]
        url = str((listing.raw_snapshot or {}).get("view_item_url") or "").strip()
        link = (
            f"<a class='xexisting-link' href='{escape(url)}' target='_blank' rel='noopener'>"
            f"View eBay #{escape(listing.ebay_listing_id)}</a>"
            if url else f"eBay #{escape(listing.ebay_listing_id)}"
        )
        items = "".join(_listed_row_html(row) for row in rows)
        blocks.append(
            "<details class='xgroup xgroup-existing'>"
            f"<summary><b>{escape(set_name)}</b>"
            f"<span class='xgroup-stats'>{len(rows)} card{'s' if len(rows) != 1 else ''} &middot; {link}</span>"
            "</summary>"
            f"<div class='xrows'>{items}</div>"
            "</details>"
        )
    return "".join(blocks)


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


def _ledger_html(session: Session) -> str:
    batches = session.scalars(
        select(EbayExportBatch).order_by(EbayExportBatch.created_at.desc()).limit(10)
    ).all()
    batch_ids = [batch.id for batch in batches]
    items = session.scalars(
        select(EbayExportItem).where(EbayExportItem.batch_id.in_(batch_ids))
    ).all() if batch_ids else []
    by_batch: dict[str, Counter] = {batch_id: Counter() for batch_id in batch_ids}
    for item in items:
        by_batch[item.batch_id][item.status.value] += 1

    rows = []
    for batch in batches:
        counts = by_batch[batch.id]
        status_bits = [
            f"{counts[name]} {name}"
            for name in ("confirmed", "pending", "failed", "cancelled")
            if counts[name]
        ]
        retry = ""
        if counts["pending"]:
            retry = (
                f"<form method='post' action='/ebay-export/batch/{escape(batch.id)}/retry' "
                "onsubmit=\"return confirm('Make every still-pending row in this file eligible for export again? "
                "Only do this if the file was not uploaded to eBay.');\">"
                "<button type='submit' class='secondary'>File was not uploaded &mdash; allow retry</button></form>"
            )
        result = (
            f"<span class='ledger-result'>result: {escape(batch.result_filename)}</span>"
            if batch.result_filename else ""
        )
        rows.append(
            "<div class='ledger-row'>"
            "<div><b>" + escape(batch.filename) + "</b>"
            f"<div class='xmeta'>{batch.created_at:%Y-%m-%d %H:%M} UTC &middot; "
            f"{escape(', '.join(status_bits) or 'no tracked rows')}{result}</div></div>"
            + retry + "</div>"
        )
    history = "".join(rows) if rows else "<p class='muted'>No exports recorded yet.</p>"
    return (
        "<div class='panel'><h3>Confirm what eBay accepted</h3>"
        "<p class='subtitle'>After uploading a bulk CSV to eBay, download its result file and import it here. "
        "Rows with an eBay Item ID become <b>confirmed</b>; rejected rows become retryable. Until then they remain "
        "<b>pending</b> and are withheld from the next export to prevent duplicates.</p>"
        "<form class='result-upload' method='post' action='/ebay-export/results' enctype='multipart/form-data'>"
        # No `accept` filter: mobile browsers (confirmed on iOS Safari,
        # 2026-09-28) apply it to the file picker itself, not just a hint
        # -- a real eBay result CSV forwarded through another app often
        # loses its recognisable CSV type along the way and silently
        # disappears from the picker's file list with any accept filter
        # at all. The server already validates real content on submit
        # (UTF-8, required columns) with a clear error either way, so
        # there's no correctness lost by not filtering client-side.
        "<input type='file' name='results' required>"
        "<button type='submit'>Import eBay result</button></form>"
        "<h4>Recent export files</h4>" + history + "</div>"
    )


@router.get("/ebay-export", response_class=HTMLResponse)
def ebay_export_page(request: Request, error: str = "", notice: str = "",
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

    from scripts.export_ebay_bulk_listing import EBAY_FEE_MARKUP_PCT

    (
        singles,
        lots,
        variation_groups,
        new_for_existing,
        listed_variations,
        skipped,
        tracked,
    ) = _build_populations(session, settings)
    single_groups = _group_by_set(singles)
    new_for_existing_groups = _group_by_set(new_for_existing)

    candidate_rows = [*singles, *new_for_existing]
    for rows in variation_groups.values():
        candidate_rows.extend(rows)
    candidate_skus = {row["sku"] for row in candidate_rows}
    candidate_listings = session.scalars(
        select(ShopListing).where(ShopListing.id.in_(candidate_skus))
    ).all() if candidate_skus else []
    pricing_holds = {
        listing.id: assessment
        for listing in candidate_listings
        if (assessment := assess_listing_price(session, listing)).blocked
    }

    error_html = f"<p class='linkfill-note bad'>{escape(error)}</p>" if error else ""
    notice_html = f"<p class='linkfill-note ok'>{escape(notice)}</p>" if notice else ""

    singles_total = sum(Decimal(r["price"]) for r in singles)
    tracked_counts = Counter(row["status"] for row in tracked)
    listed_variation_count = sum(len(payload["rows"]) for payload in listed_variations.values())
    stats = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>New singles</div><div class='value'>{len(singles)}</div>"
        f"<div class='value-sub'>${singles_total:,.2f} if all included</div></div>"
        f"<div class='stat-card'><div class='label'>New variation cards</div>"
        f"<div class='value'>{sum(len(v) for v in variation_groups.values())}</div>"
        f"<div class='value-sub'>across {len(variation_groups)} set{'s' if len(variation_groups) != 1 else ''}</div></div>"
        f"<div class='stat-card'><div class='label'>Already listed variations</div>"
        f"<div class='value'>{listed_variation_count}</div>"
        f"<div class='value-sub'>across {len(listed_variations)} active eBay listing{'s' if len(listed_variations) != 1 else ''}</div></div>"
        f"<div class='stat-card'><div class='label'>New for existing variations</div>"
        f"<div class='value'>{len(new_for_existing)}</div>"
        "<div class='value-sub'>shown separately below</div></div>"
        f"<div class='stat-card'><div class='label'>Export ledger</div><div class='value'>{len(tracked)}</div>"
        f"<div class='value-sub'>{tracked_counts['pending']} awaiting eBay &middot; "
        f"{tracked_counts['confirmed']} confirmed</div></div>"
        f"<div class='stat-card'><div class='label'>Bulk Master lots</div><div class='value'>{len(lots)}</div>"
        "<div class='value-sub'>not exportable yet, see below</div></div>"
        f"<div class='stat-card'><div class='label'>Pricing safety holds</div>"
        f"<div class='value'>{len(pricing_holds)}</div>"
        "<div class='value-sub'>review or override individually</div></div>"
        "</div>"
        f"<p class='subtitle'>Every Dragon Ball card, and every Pokémon card rarer than Uncommon, is priced "
        f"{EBAY_FEE_MARKUP_PCT}% over its reviewed inventory price for eBay -- "
        "singles and variation groups both, wherever the card lands below.</p>"
        + (
            f"<p class='linkfill-note bad'><b>{len(pricing_holds)} card{'s are' if len(pricing_holds) != 1 else ' is'} "
            "on a pricing safety hold.</b> The proposed price is materially below recent market evidence. "
            "Verify the exact card and price, then tick that card's override checkbox if you still want to "
            "include it. Overrides are recorded in the export audit.</p>"
            if pricing_holds else ""
        )
    )

    singles_html = (
        "".join(_set_group_html(name, rows, group_id=f"s-{i}", pricing_holds=pricing_holds) for i, (name, rows) in enumerate(single_groups.items()))
        if singles else "<p class='muted'>No active singles ready to export.</p>"
    )
    variation_html = (
        "".join(_set_group_html(name, rows, group_id=f"v-{i}", pricing_holds=pricing_holds) for i, (name, rows) in enumerate(variation_groups.items()))
        if variation_groups else "<p class='muted'>No new variation listings are ready right now.</p>"
    )
    new_for_existing_html = (
        "".join(
            _set_group_html(name, rows, group_id=f"u-{i}", pricing_holds=pricing_holds)
            for i, (name, rows) in enumerate(new_for_existing_groups.items())
        )
        if new_for_existing else "<p class='muted'>No newly scanned cards need attention inside an existing variation.</p>"
    )
    listed_variations_html = _listed_variations_html(listed_variations)
    # eBay's condition ID for category 183455 (Mixed Card Lots) was
    # confirmed 2026-09-28 by asking eBay directly (GetItem against 3 real
    # active lot listings already on this account, see
    # convert_to_ebay_template.DEFAULT_LOT_CONDITION_ID) -- lots are
    # selectable now, no longer read-only.
    lots_html = (
        "".join(_lot_row_html(r) for r in lots) if lots else "<p class='muted'>No active Bulk Master lots.</p>"
    )

    body = (
        brand_header("eBay bulk upload file")
        # Sold items on this page still need packing -- the assistant for
        # that already exists (shop_orders.py's pack-sheet, one order at
        # a time), just wasn't reachable from anywhere in the eBay
        # workflow itself until now.
        + "<p class='subtitle'><a href='/shop-orders'>&#128230; Pack orders that have sold &rarr;</a></p>"
        + error_html + notice_html + stats
        # target keeps the EzBay page alive even if JavaScript is disabled
        # or fails; the script below normally intercepts this and uses a
        # background request plus the iPhone share/save sheet instead.
        + "<form method='post' action='/ebay-export/build' target='_blank' data-no-progress data-export-form>"
        + "<div class='panel'><h2>New cards ready to upload</h2>"
          "<p class='subtitle'>Only cards not already represented by an active eBay listing or the export ledger appear in these selectable sections.</p></div>"
        + f"<div class='panel'><h3>New individual cards <button type='button' class='xlink' onclick=\"toggleAll('s', true)\">select all</button>"
        + f" &middot; <button type='button' class='xlink' onclick=\"toggleAll('s', false)\">none</button></h3>{singles_html}</div>"
        + f"<div class='panel'><h3>New variation listings <button type='button' class='xlink' onclick=\"toggleAll('v', true)\">select all</button>"
        + f" &middot; <button type='button' class='xlink' onclick=\"toggleAll('v', false)\">none</button></h3>"
          f"<p class='subtitle'>New sets with {MIN_SET_SIZE_FOR_VARIATION_LISTING}+ active listings, offered as one "
          "&quot;pick your card&quot; listing so a buyer can choose exactly which copy they want, each at its own "
          "real price. Sets already active on eBay are excluded and shown separately below.</p>"
        + f"{variation_html}</div>"
        # Added locally after their set's eBay parent already went live.
        # No variation-revision export exists yet to add a card into an
        # already-live listing, so selecting one here exports it as its
        # own individual listing instead of creating a duplicate parent.
        + f"<div class='panel'><h3>New cards for existing eBay variations "
          "<button type='button' class='xlink' onclick=\"toggleAll('u', true)\">select all</button>"
        + f" &middot; <button type='button' class='xlink' onclick=\"toggleAll('u', false)\">none</button></h3>"
          "<p class='subtitle'>Exports as an individual listing, not added to the existing variation.</p>"
        + f"{new_for_existing_html}</div>"
        # Collapsed by default so a page with 30+ lots and 40+ already-live
        # sets doesn't force scrolling past all of it to reach the actual
        # submit button (2026-09-27) -- selectable since 2026-09-28 (see
        # lots_html above), so this is also an `xgroup` for toggleAll('l', ...)
        # to find, same as every other selectable section on this page.
        + "<div class='panel'><details class='xpanel-fold xgroup'><summary>"
        + f"<h3>Bulk Master lots<span class='foldcount'>{len(lots)}</span> "
          "<button type='button' class='xlink' onclick=\"toggleAll('l', true); event.preventDefault()\">select all</button>"
        + " &middot; <button type='button' class='xlink' onclick=\"toggleAll('l', false); event.preventDefault()\">none</button></h3>"
          "</summary>"
        + f"<div id='l-0' class='xrows'>{lots_html}</div></details></div>"
        + _skipped_html(skipped)
        + "<div class='panel xsubmit'>"
          "<span class='xdownload-status' data-export-status hidden></span>"
          "<button type='button' class='secondary' data-save-export hidden>Save CSV</button>"
          "<button type='submit' data-build-export>Export selected &amp; download</button></div>"
        + "</form>"
        + "<div class='panel'>"
          "<p class='subtitle'>Read-only -- already live on eBay.</p>"
        + "<details class='xpanel-fold'><summary>"
        + f"<h2>Already listed variations<span class='foldcount'>{len(listed_variations)}</span></h2></summary>"
        + f"{listed_variations_html}</details>"
        + "<form method='post' action='/ebay-export/refresh-listings' class='xrefresh-form'>"
          "<button type='submit' class='secondary'>&#8635; Refresh from eBay</button>"
          "<span class='subtitle'>Pulls the current Active list from eBay so this section (and duplicate "
          "detection above) reflects anything you've ended or relisted since the last check.</span>"
          "</form>"
        + "</div>"
        + _ledger_html(session)
        + _SCRIPT + _STYLE
        + "<style>.xrow-held{background:#fff4f4}.xhold{color:var(--bad,#b42318)}</style>"
    )
    return HTMLResponse(page("EzBay — eBay bulk upload file", body))


def _record(session: Session, user: str, correlation_id: str, outcome: str, details: dict) -> None:
    record_event(session, actor_type="user", actor_id=user, action="ebay.bulk_export",
                resource_type="ebay_export", resource_id=None, outcome=outcome,
                correlation_id=correlation_id, details=details)


@router.post("/ebay-export/refresh-listings")
async def ebay_export_refresh_listings(
    request: Request, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> RedirectResponse:
    """The same live eBay sync /ebay-listings/refresh runs, called from
    here too so a stale "already listed" section (or a stale duplicate
    check above it) doesn't need a trip to a different page to fix --
    added 2026-09-27 right after that staleness caused 7 ended listings
    to keep blocking re-export until someone thought to refresh
    elsewhere. Same route, same reconciliation, just a different place
    to press the button and a redirect back to this page instead."""
    from .adapters.ebay import EbayApiError
    from .ebay_listings import refresh_from_ebay
    from .ebay_oauth import OAuthError

    settings = request.app.state.settings
    try:
        await refresh_from_ebay(session, settings, user)
    except (OAuthError, EbayApiError) as exc:
        return RedirectResponse(f"/ebay-export?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/ebay-export?notice=" + quote("eBay listings refreshed."), status_code=303)


@router.post("/ebay-export/build")
async def ebay_export_build(request: Request,
                            user: str = Depends(require_dashboard_user),
                            session: Session = Depends(get_session)) -> Response:
    from scripts.convert_to_ebay_template import build_variation_group, convert_row, read_ebay_header

    settings = request.app.state.settings
    missing = _missing_settings(settings)
    correlation_id = str(uuid.uuid4())
    if missing:
        _record(session, user, correlation_id, "failed", {"stage": "config", "missing": missing})
        return RedirectResponse(f"/ebay-export?error={quote('Missing config: ' + ', '.join(missing))}", status_code=303)

    # Not FastAPI's usual `sku: list[str] = Form(default=[])` -- that always
    # calls request.form() with Starlette's hard default max_fields=1000
    # (fastapi/routing.py, no way to raise it through the Form(...) marker),
    # and one checkbox per card across 40 set listings is nowhere near a
    # generous margin above that (1,954 cards hit "Too many fields" on a
    # real submit, 2026-09-22). MAX_EXPORT_FIELDS is a real ceiling, not
    # "unlimited", so a runaway form still fails loudly instead of the
    # request body growing without bound.
    form = await request.form(max_fields=MAX_EXPORT_FIELDS, max_files=0)
    pricing_overrides = set(form.getlist("pricing_override"))
    selected = set(form.getlist("sku")) | pricing_overrides
    if not selected:
        return RedirectResponse(f"/ebay-export?error={quote('Nothing selected -- tick at least one listing first.')}", status_code=303)

    # Re-run the same pricing gate used by direct Inventory API publication
    # at submit time. The page may have been open while prices refreshed,
    # and a hand-crafted POST must not bypass the review UI. Bulk-lot SKUs
    # are ignored here: this guard is for individually valued cards.
    selected_listings = session.scalars(
        select(ShopListing).where(ShopListing.id.in_(selected))
    ).all()
    unsafe = [
        (listing, assessment)
        for listing in selected_listings
        if (assessment := assess_listing_price(session, listing)).blocked
        and listing.id not in pricing_overrides
    ]
    if unsafe:
        examples = "; ".join(
            f"{listing.title}: ${assessment.proposed_price:.2f} (review floor ${assessment.review_floor:.2f})"
            for listing, assessment in unsafe[:5]
        )
        more = f"; plus {len(unsafe) - 5} more" if len(unsafe) > 5 else ""
        reason = (
            f"Pricing safety stopped {len(unsafe)} selected card(s). {examples}{more}. "
            "Review their prices before exporting."
        )
        _record(session, user, correlation_id, "failed", {
            "stage": "pricing_safety", "blocked": len(unsafe),
            "skus": [listing.id for listing, _assessment in unsafe],
            "requested_overrides": sorted(pricing_overrides),
        })
        return RedirectResponse(f"/ebay-export?error={quote(reason)}", status_code=303)

    args = _convert_args(settings)
    (
        singles,
        lots,
        variation_groups,
        new_for_existing,
        _listed_variations,
        _skipped,
        tracked,
    ) = _build_populations(session, settings)

    selected_lot_skus = {r["sku"] for r in lots if r["sku"] in selected}
    out_rows = [
        convert_row(r, args) for r in singles + new_for_existing + lots if r["sku"] in selected
    ]

    variation_skipped: list[str] = []
    for set_name, cards in variation_groups.items():
        chosen = [c for c in cards if c["sku"] in selected]
        if not chosen:
            continue
        group_rows, group_skipped = build_variation_group(set_name, chosen, args)
        out_rows.extend(group_rows)
        variation_skipped.extend(group_skipped)

    if not out_rows:
        # The generic "duplicate number" message below is only actually
        # true for one specific cause (build_variation_group's own dedup).
        # A selection whose every SKU is already sitting in the export
        # ledger from an earlier build -- pending or confirmed, so
        # deliberately excluded from every population above, see
        # _build_populations -- produces the exact same empty out_rows,
        # and blaming "duplicate number" there is just wrong: nothing
        # about the card was a duplicate, it was already exported.
        # Real case, 2026-09-27: a 46-card re-selection right after a
        # 1293-card batch that had already swept those same 46 up.
        tracked_selected = [t for t in tracked if t["row"].get("sku") in selected]
        if tracked_selected and len(tracked_selected) == len(selected):
            statuses = sorted({t["status"] for t in tracked_selected})
            reason = (
                f"Every card in that selection is already in the export ledger ({', '.join(statuses)}) -- "
                "it was included in an earlier build and won't be offered again until that batch is "
                "confirmed, cancelled, or fails. Check the export ledger below for its status."
            )
        else:
            reason = "Nothing exportable came out of that selection (every selected variation card was a duplicate number in its set)."
        _record(session, user, correlation_id, "failed", {"stage": "build", "reason": "no rows survived selection/grouping"})
        return RedirectResponse(f"/ebay-export?error={quote(reason)}", status_code=303)

    try:
        info_row, header = read_ebay_header(TEMPLATE_PATH)
    except (OSError, SystemExit) as exc:
        # Missing/unreadable templates are deployment/configuration errors,
        # not malformed user selections. Surface them on the export page
        # instead of leaking an unhandled PermissionError as a generic 500.
        message = f"Could not read the eBay upload template: {exc}"
        _record(session, user, correlation_id, "failed", {"stage": "template", "error": str(exc)})
        return RedirectResponse(f"/ebay-export?error={quote(message)}", status_code=303)

    header_set = set(header)
    out_rows = [{k: v for k, v in row.items() if k in header_set} for row in out_rows]

    labels = [str(row.get("CustomLabel") or "").strip() for row in out_rows]
    duplicate_labels = sorted(label for label, count in Counter(labels).items() if label and count > 1)
    if not all(labels) or duplicate_labels:
        reason = (
            "The export produced duplicate SKUs: " + ", ".join(duplicate_labels[:5])
            if duplicate_labels else "The export produced a row without a SKU."
        )
        _record(session, user, correlation_id, "failed", {"stage": "ledger", "reason": reason})
        return RedirectResponse(f"/ebay-export?error={quote(reason)}", status_code=303)

    buf = io.StringIO()
    buf.write(info_row + "\r\n")
    writer = csv.DictWriter(buf, fieldnames=header, restval="")
    writer.writeheader()
    writer.writerows(out_rows)
    content = buf.getvalue().encode("utf-8-sig")

    filename = "ebay_bulk_listing_ready.csv"
    now = datetime.now(UTC)
    batch = EbayExportBatch(
        id=str(uuid.uuid4()), filename=filename, row_count=len(out_rows),
        selected_count=len(selected), created_by=user, created_at=now,
    )
    session.add(batch)
    for row, sku in zip(out_rows, labels, strict=True):
        relationship = str(row.get("Relationship") or "").strip().casefold()
        kind = "variation_child" if relationship == "variation" else (
            "variation_parent" if sku.startswith("set-variation-") else (
                "lot" if sku in selected_lot_skus else "single"
            )
        )
        session.add(EbayExportItem(
            batch_id=batch.id, sku=sku, title=str(row.get("*Title") or "")[:512] or None,
            kind=kind, status=EbayExportItemStatus.PENDING, exported_at=now,
        ))
    session.commit()

    _record(session, user, correlation_id, "success", {
        "rows": len(out_rows), "selected": len(selected), "bytes": len(content),
        "variation_skipped": len(variation_skipped), "batch_id": batch.id,
        "pricing_safety_overrides": sorted(
            listing.id for listing in selected_listings
            if listing.id in pricing_overrides and assess_listing_price(session, listing).blocked
        ),
    })

    return Response(
        content=content, media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _result_row_succeeded(row: dict[str, str]) -> bool:
    status = (row.get("Status") or "").strip().casefold()
    item_id = (row.get("ItemID") or "").strip()
    error_code = (row.get("ErrorCode") or "").strip()
    # eBay uses Warning for a listing that was created with a non-blocking
    # warning (the account's payment-hold notice is a real example). An
    # ItemID is the decisive proof that the Add operation created a listing.
    return bool(item_id and not error_code and status in {"success", "warning"})


def _result_row_error(row: dict[str, str]) -> str:
    code = (row.get("ErrorCode") or row.get("Code") or "").strip()
    message = (row.get("ErrorMessage") or row.get("Message") or "").strip()
    status = (row.get("Status") or "failed").strip()
    detail = " — ".join(value for value in (code, message) if value)
    return f"{status}: {detail}"[:4000] if detail else status[:4000]


def _variation_group_children(session: Session, parent: EbayExportItem) -> list[EbayExportItem]:
    """Resolve the child ledger rows represented by one variation parent.

    eBay's upload result contains one outcome for the variation parent, not
    one outcome for every child CSV row.  The ledger stores the real shop SKU
    on each child, so its card set lets us deterministically map it back to
    the synthetic ``set-variation-*`` parent SKU.

    Matched on `_set_group_key(set_name, language)`, not the bare CardSet
    name -- a set that exists in more than one language builds its parent
    SKU from the language-qualified key (export_ebay_bulk_listing.py's
    `_set_group_key`, e.g. "set-variation-saiyan-s-pride-japanese"), so
    comparing against the bare name can never match a single one of that
    parent's real children. Confirmed live 2026-09-28: this silently
    returned zero children for every Japanese-suffixed parent, so a real
    eBay result for one only ever confirmed the parent row itself, leaving
    every one of its children stuck pending indefinitely.
    """
    if parent.kind != "variation_parent":
        return []

    from scripts.convert_to_ebay_template import _parent_sku
    from scripts.export_ebay_bulk_listing import _set_group_key

    candidates = session.execute(
        select(EbayExportItem, CardSet.name, TcgCard.language)
        .join(ShopListing, ShopListing.id == EbayExportItem.sku)
        .join(TcgCard, TcgCard.catalog_item_id == ShopListing.catalog_item_id)
        .join(CardSet, CardSet.id == TcgCard.set_id)
        .where(
            EbayExportItem.batch_id == parent.batch_id,
            EbayExportItem.kind == "variation_child",
        )
    ).all()
    return [
        item for item, set_name, language in candidates
        if _parent_sku(_set_group_key(set_name, language or "English")) == parent.sku
    ]


def _import_result_rows(session: Session, rows: list[dict[str, str]], *, filename: str,
                        user: str) -> dict[str, int]:
    """Reconcile an eBay File Exchange result with the newest SKU ledger.

    Results from exports made before this ledger existed are accepted too:
    they get one synthetic import batch, which lets the user's existing
    successful listings seed duplicate prevention immediately.
    """
    now = datetime.now(UTC)
    by_sku: dict[str, dict[str, str]] = {}
    ignored = 0
    for row in rows:
        sku = (row.get("CustomLabel") or "").strip()
        if not sku:
            ignored += 1
            continue
        by_sku[sku[:128]] = row

    if not by_sku:
        return {
            "confirmed": 0, "failed": 0, "matched": 0, "imported": 0,
            "ignored": ignored, "group_confirmed": 0, "released": 0,
        }

    existing = session.scalars(
        select(EbayExportItem)
        .where(EbayExportItem.sku.in_(list(by_sku)))
        .order_by(EbayExportItem.exported_at.desc(), EbayExportItem.id.desc())
    ).all()
    latest: dict[str, EbayExportItem] = {}
    for item in existing:
        latest.setdefault(item.sku, item)

    clean_filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:256] or "ebay-results.csv"
    imported_batch: EbayExportBatch | None = None
    touched_batch_ids: set[str] = set()
    counts = Counter()

    for sku, row in by_sku.items():
        succeeded = _result_row_succeeded(row)
        item_id = (row.get("ItemID") or "").strip()[:128] or None
        item = latest.get(sku)
        if item is None:
            if imported_batch is None:
                imported_batch = EbayExportBatch(
                    id=str(uuid.uuid4()), filename=f"Imported history: {clean_filename}"[:256],
                    row_count=0, selected_count=0, created_by=user, created_at=now,
                    result_filename=clean_filename, result_imported_at=now,
                )
                session.add(imported_batch)
            item = EbayExportItem(
                batch_id=imported_batch.id, sku=sku, title=None, kind="imported",
                status=EbayExportItemStatus.PENDING, exported_at=now,
            )
            session.add(item)
            latest[sku] = item
            counts["imported"] += 1
        else:
            counts["matched"] += 1
            touched_batch_ids.add(item.batch_id)

        children = _variation_group_children(session, item)
        targets = [item, *children]
        if succeeded:
            for target in targets:
                target.status = EbayExportItemStatus.CONFIRMED
                target.ebay_listing_id = item_id
                target.error = None
                target.confirmed_at = now
            counts["confirmed"] += 1
            counts["group_confirmed"] += len(children)
        elif item.status != EbayExportItemStatus.CONFIRMED:
            # A duplicate/older result file must never downgrade a SKU that
            # has already been positively confirmed by an ItemID.
            error = _result_row_error(row)
            for target in targets:
                if target.status == EbayExportItemStatus.CONFIRMED:
                    continue
                if target is not item and target.status != EbayExportItemStatus.FAILED:
                    counts["released"] += 1
                target.status = EbayExportItemStatus.FAILED
                target.error = error
                target.confirmed_at = None
            counts["failed"] += 1

    if imported_batch is not None:
        imported_batch.row_count = counts["imported"]
        imported_batch.selected_count = counts["imported"]
    if touched_batch_ids:
        for batch in session.scalars(
            select(EbayExportBatch).where(EbayExportBatch.id.in_(touched_batch_ids))
        ).all():
            batch.result_filename = clean_filename
            batch.result_imported_at = now

    session.commit()
    counts["ignored"] = ignored
    return {
        name: counts[name]
        for name in ("confirmed", "failed", "matched", "imported", "ignored", "group_confirmed", "released")
    }


@router.post("/ebay-export/results")
async def ebay_export_results(results: UploadFile = File(...),
                              user: str = Depends(require_dashboard_user),
                              session: Session = Depends(get_session)) -> RedirectResponse:
    data = await results.read(MAX_RESULT_FILE_BYTES + 1)
    if len(data) > MAX_RESULT_FILE_BYTES:
        return RedirectResponse(
            f"/ebay-export?error={quote('That result file is larger than the 10 MB safety limit.')}", status_code=303
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return RedirectResponse(
            f"/ebay-export?error={quote('Could not read that result file as UTF-8 CSV.')}", status_code=303
        )
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fields = set(reader.fieldnames or [])
    required = {"Status", "CustomLabel", "ItemID"}
    if not required.issubset(fields):
        return RedirectResponse(
            f"/ebay-export?error={quote('That is not an eBay upload result CSV (Status, CustomLabel and ItemID columns are required).')}",
            status_code=303,
        )
    rows = list(reader)
    counts = _import_result_rows(session, rows, filename=results.filename or "ebay-results.csv", user=user)
    correlation_id = str(uuid.uuid4())
    _record(session, user, correlation_id, "success", {"stage": "result_import", **counts})
    ignored_note = f", {counts['ignored']} rows ignored without a SKU" if counts["ignored"] else ""
    group_note = (
        f", {counts['group_confirmed']} variation cards confirmed with their parent"
        if counts["group_confirmed"] else ""
    )
    retry_note = (
        f", {counts['released']} variation cards released for retry"
        if counts["released"] else ""
    )
    notice = (
        f"eBay result imported: {counts['confirmed']} confirmed, {counts['failed']} failed and retryable"
        f"{group_note}{retry_note}{ignored_note}."
    )
    return RedirectResponse(f"/ebay-export?notice={quote(notice)}", status_code=303)


@router.post("/ebay-export/batch/{batch_id}/retry")
def ebay_export_retry_batch(batch_id: str, user: str = Depends(require_dashboard_user),
                            session: Session = Depends(get_session)) -> RedirectResponse:
    batch = session.get(EbayExportBatch, batch_id)
    if batch is None:
        return RedirectResponse(f"/ebay-export?error={quote('Export batch not found.')}", status_code=303)
    pending = session.scalars(
        select(EbayExportItem).where(
            EbayExportItem.batch_id == batch_id,
            EbayExportItem.status == EbayExportItemStatus.PENDING,
        )
    ).all()
    for item in pending:
        item.status = EbayExportItemStatus.CANCELLED
        item.error = "Download marked as not uploaded; eligible for retry."
    session.commit()
    _record(session, user, str(uuid.uuid4()), "success", {
        "stage": "allow_retry", "batch_id": batch_id, "rows": len(pending),
    })
    notice = f"{len(pending)} pending row{'s are' if len(pending) != 1 else ' is'} eligible for export again."
    return RedirectResponse(f"/ebay-export?notice={quote(notice)}", status_code=303)


_SCRIPT = """<script>
function toggleGroup(id, checked) {
  document.getElementById(id).querySelectorAll("input[type=checkbox][name=sku]:not(:disabled)").forEach(cb => cb.checked = checked);
}
function toggleAll(prefix, checked) {
  document.querySelectorAll("details.xgroup").forEach(d => {
    if (d.querySelector(".xrows").id.startsWith(prefix + "-")) {
      d.querySelectorAll("input[type=checkbox][name=sku]:not(:disabled)").forEach(cb => cb.checked = checked);
    }
  });
}

(function () {
  var form = document.querySelector('[data-export-form]');
  if (!form || !window.fetch || !window.FormData || !window.Blob || !window.File || !window.URL) return;
  var buildButton = form.querySelector('[data-build-export]');
  var saveButton = form.querySelector('[data-save-export]');
  var status = form.querySelector('[data-export-status]');
  var readyFile = null;
  var readyUrl = null;

  function showStatus(message, bad) {
    status.hidden = false;
    status.textContent = message;
    status.classList.toggle('bad', !!bad);
  }

  function filenameFrom(response) {
    var disposition = response.headers.get('Content-Disposition') || '';
    var match = disposition.match(/filename=(?:\"([^\"]+)\"|([^;]+))/i);
    return ((match && (match[1] || match[2])) || 'ebay_bulk_listing_ready.csv').trim();
  }

  function browserDownload() {
    if (!readyUrl || !readyFile) return;
    var link = document.createElement('a');
    link.href = readyUrl;
    link.download = readyFile.name;
    // If an older iOS build ignores `download`, it opens a separate browser
    // context instead of replacing the standalone EzBay page.
    link.target = '_blank';
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  saveButton.addEventListener('click', function () {
    if (!readyFile) return;
    if (navigator.share && navigator.canShare && navigator.canShare({files: [readyFile]})) {
      navigator.share({files: [readyFile], title: readyFile.name})
        .then(function () { showStatus('CSV ready and shared. This page stayed open.', false); })
        .catch(function (error) {
          if (error && error.name === 'AbortError') {
            showStatus('Save cancelled. Tap Save CSV whenever you are ready.', false);
            return;
          }
          browserDownload();
          showStatus('CSV download started in a separate window.', false);
        });
    } else {
      browserDownload();
      showStatus('CSV download started. This page stayed open.', false);
    }
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    buildButton.disabled = true;
    saveButton.hidden = true;
    showStatus('Building CSV…', false);
    // data-no-progress keeps the shared bar's own generic submit listener
    // from also firing for this form -- this page already has a richer,
    // build-specific status line, so the shared bar is driven by hand
    // here instead, alongside it rather than in place of it.
    if (window.EzBayProgress) window.EzBayProgress.begin('POST /ebay-export/build');
    var built = false;
    fetch(form.action, {
      method: 'POST', body: new FormData(form), credentials: 'same-origin',
      headers: {'Accept': 'text/csv'}
    }).then(function (response) {
      var contentType = (response.headers.get('Content-Type') || '').toLowerCase();
      if (!response.ok || contentType.indexOf('text/csv') === -1) {
        // Validation failures are 303 redirects back to this page with a
        // readable error message. Follow that URL in the current EzBay page.
        window.location.href = response.url || '/ebay-export';
        return null;
      }
      var filename = filenameFrom(response);
      return response.blob().then(function (blob) {
        if (readyUrl) URL.revokeObjectURL(readyUrl);
        readyFile = new File([blob], filename, {type: 'text/csv'});
        readyUrl = URL.createObjectURL(readyFile);
        saveButton.hidden = false;
        built = true;
        if (navigator.share && navigator.canShare && navigator.canShare({files: [readyFile]})) {
          showStatus('CSV ready. Tap Save CSV, then choose Save to Files or your upload destination.', false);
        } else {
          browserDownload();
          showStatus('CSV download started. This page stayed open; Save CSV can download it again.', false);
        }
        return true;
      });
    }).catch(function (error) {
      showStatus((error && error.message) || 'Could not build the CSV.', true);
    }).finally(function () {
      buildButton.disabled = false;
      if (window.EzBayProgress) window.EzBayProgress.done(built ? 'POST /ebay-export/build' : null);
    });
  });

  window.addEventListener('pagehide', function () {
    if (readyUrl) URL.revokeObjectURL(readyUrl);
  });
})();
</script>"""

_STYLE = """<style>
.xgroup{border:1px solid var(--panel-border);border-radius:10px;margin-bottom:8px;overflow:hidden}
.xgroup summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 12px;
  cursor:pointer;background:var(--surface-sunken);list-style:none}
.xgroup summary::-webkit-details-marker{display:none}
.xgroup-all{display:flex;align-items:center;gap:8px;font-size:14px}
.xgroup-stats{font-size:12.5px;color:var(--text-dim);white-space:nowrap}
.xgroup-existing summary{background:rgba(42,157,143,.07)}
.xexisting-link{color:var(--accent);font-weight:600;text-decoration:none}
.xexisting-link:hover{text-decoration:underline}
.xrows{display:flex;flex-direction:column}
.xrow{display:flex;align-items:center;gap:10px;padding:8px 12px;border-top:1px solid var(--panel-border);
  cursor:pointer}
.xrow-static{cursor:default}
.xrow:hover{background:var(--surface-sunken)}
.xthumb{width:36px;height:50px;object-fit:contain;border-radius:4px;background:var(--surface-sunken);flex:none}
.xinfo{display:flex;flex-direction:column;flex:1;min-width:0}
.xtitle{font-size:13.5px;font-weight:600;word-break:break-word}
.xmeta{font-size:11.5px;color:var(--text-dim)}
.xlang{color:var(--accent);font-weight:700}
.xoverride-note{display:block;margin-top:3px;color:var(--bad,#b42318);font-weight:650}
.xproof{display:block;margin-top:3px;color:var(--text-dim)}
.xpanel-fold summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px}
.xpanel-fold summary::-webkit-details-marker{display:none}
.xpanel-fold summary::before{content:"\25B8";font-size:12px;color:var(--text-dim);transition:transform .15s ease}
.xpanel-fold[open] summary::before{transform:rotate(90deg)}
.xpanel-fold summary h2,.xpanel-fold summary h3{margin:0}
.foldcount{margin-left:2px;font-size:12px;font-weight:600;color:var(--text-dim);background:var(--surface-sunken);
  border-radius:999px;padding:1px 8px}
.xrefresh-form{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px;
  padding-top:14px;border-top:1px solid var(--panel-border)}
.xrefresh-form .subtitle{margin:0;flex:1 1 240px}
.xprice{font-size:13.5px;font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums}
.xlink{background:none;border:none;color:var(--accent);font-size:12.5px;cursor:pointer;padding:0}
.xskip{margin:8px 0 0;padding-left:20px;font-size:12.5px;color:var(--text-dim)}
.panel-note{background:rgba(212,165,39,.10);border:1px solid rgba(212,165,39,.35);border-radius:10px;
  padding:10px 12px;font-size:13px;line-height:1.45;margin-bottom:10px}
.result-upload{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 18px}
.result-upload input{max-width:100%;font-size:13px}
.ledger-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;
  border-top:1px solid var(--panel-border)}
.ledger-result{margin-left:8px}
.ledger-row form{margin:0;flex:none}
.ledger-row button{font-size:12px;padding:7px 9px}
.xsubmit{display:flex;justify-content:flex-end;align-items:center;gap:10px;flex-wrap:wrap}
.xdownload-status{font-size:12.5px;color:var(--text-dim);margin-right:auto;text-align:left}
.xdownload-status.bad{color:var(--danger)}
@media (max-width:560px){
  .xtitle{font-size:13px}
  .ledger-row{align-items:flex-start;flex-direction:column}
}
</style>"""
