"""eBay sales -- where the cards that already sold on eBay actually are.

Nothing in the admin app read EbayImportedOrder at all until now: it's
written by scripts/import_ebay_orders.py (Phase 5 of the two-way
storefront <-> eBay stock sync) and never displayed anywhere -- the only
way to see what had actually sold on eBay was a direct database query.
This is the fix: for every real eBay order line that's been applied
against a ShopListing, show a photo, the set/number/rarity, and wherever
it's currently marked as living, the same pack-assistant shape
shop_orders.py already has for website checkout sales (see
card_locate.py, shared by both).

Still no packed/shipped state machine here -- eBay tracks its own
fulfillment state on eBay's side (lineItemFulfillmentStatus in
raw_snapshot), and this app doesn't write tracking back to eBay. What
this page *does* now track is purely local: dismissed_at, the operator's
own "I'm done looking at this" marker (2026-09-28, real complaint: every
order stayed expanded forever with no way to collapse or clear one out
of the active view). It never touches order or listing status, and it's
a completely separate field from applied_at -- the sync poller's own
idempotency marker -- so dismissing an order here can never affect
whether its stock got decremented.
"""

import re
import uuid
from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .adapters.ebay import EbayApiError
from .adapters.ebay_trading_write import (
    MAX_REVISIONS_PER_REQUEST,
    EbayTradingQuantityAdapter,
    InventoryRevision,
)
from .audit import record_event
from .auth import require_dashboard_user
from .card_locate import (
    LOCATE_STYLE,
    PACK_STYLE,
    line_detail,
    location_pill_html,
    pack_card_html,
    thumb_src,
)
from .database import get_session
from .ebay_oauth import OAuthError, ebay_services, valid_user_access_token
from .labels import label_buttons_html
from .models import (
    CardSet,
    CardVariant,
    CatalogItem,
    EbayBulkPickItem,
    EbayBulkPickManifest,
    EbayExportItem,
    EbayExportItemStatus,
    EbayImportedOrder,
    EbayListing,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from .stock import decrement_listing_quantity
from .ui import brand_header, page

router = APIRouter(tags=["ebay-sales"])

FILTERS = {
    "action_needed": "Needs action",
    "all": "All orders",
    "completed": "Completed",
}

# This is intentionally a picking policy, not a catalogue rule.  Keeping the
# five groups explicit makes the manifest predictable to pack on a phone and
# avoids silently changing a live order whenever another set is scanned.
BULK_PICK_SET_NAMES = (
    "Prismatic Evolutions",
    "White Flare",
    "Phantasmal Flames",
    "Perfect Order",
    "Black Bolt",
)
BULK_PICK_PER_SET = 30
_BULK_QUANTITY = re.compile(r"\b(\d+)\+?\s*cards?\b", re.IGNORECASE)


def _ship_to(snapshot: dict) -> dict:
    """Buyer name/address/email out of eBay's own order JSON -- every
    field guarded, since this is a live third-party payload shape, not
    something this app controls."""
    steps = snapshot.get("fulfillmentStartInstructions") or []
    ship_to = (steps[0].get("shippingStep") or {}).get("shipTo") if steps else {}
    ship_to = ship_to or {}
    address = ship_to.get("contactAddress") or {}
    buyer = snapshot.get("buyer") or {}
    street = ", ".join(v for v in (address.get("addressLine1"), address.get("addressLine2")) if v)
    locality = ", ".join(
        v for v in (address.get("city"), address.get("stateOrProvince"), address.get("postalCode")) if v
    )
    return {
        "name": ship_to.get("fullName") or buyer.get("username") or "Unknown buyer",
        "street": street,
        "locality": locality,
        "country": address.get("countryCode") or "",
    }


# eBay returns no SKU for a listing created by hand on eBay rather than
# through EZBay (2026-10-29: a One Piece bulk lot order never reached this
# page because the importer dropped its only line). ebay_imported_orders.sku
# is NOT NULL and unique per order, so the eBay item number stands in.
UNLISTED_SKU_PREFIX = "item:"
_UNLISTED_NOTE = ("Listed directly on eBay, not from EZBay stock -- pack it by hand from the "
                  "eBay listing. Nothing was decremented here.")


def line_item_sku(line_item: dict) -> str | None:
    """The key an order line is stored under: its real SKU, or the
    `item:<eBay item number>` stand-in when eBay returned none."""
    if line_item.get("sku"):
        return line_item["sku"]
    item_id = line_item.get("legacyItemId") or line_item.get("lineItemId")
    return f"{UNLISTED_SKU_PREFIX}{item_id}" if item_id else None


def _line_items_by_sku(snapshot: dict) -> dict[str, dict]:
    return {key: li for li in snapshot.get("lineItems", []) if (key := line_item_sku(li))}


def _line_item_key(line_item: dict, index: int) -> str:
    return str(
        line_item.get("lineItemId")
        or f"{line_item.get('legacyItemId') or line_item.get('itemId') or 'unknown'}:{index}"
    )


def _bulk_quantity(line_item: dict) -> int | None:
    if line_item.get("sku"):
        return None
    match = _BULK_QUANTITY.search(line_item.get("title") or "")
    if not match:
        return None
    quantity = int(match.group(1))
    return quantity if 1 <= quantity <= 500 else None


def _bulk_lines(snapshot: dict) -> list[tuple[str, dict, int]]:
    result = []
    for index, line_item in enumerate(snapshot.get("lineItems") or []):
        quantity = _bulk_quantity(line_item)
        if quantity is not None:
            result.append((_line_item_key(line_item, index), line_item, quantity))
    return result


def _manifests_for_order(session: Session, ebay_order_id: str) -> dict[str, EbayBulkPickManifest]:
    rows = session.scalars(
        select(EbayBulkPickManifest).where(EbayBulkPickManifest.ebay_order_id == ebay_order_id)
    ).all()
    return {row.line_item_id: row for row in rows}


def _bulk_is_complete(session: Session, rows: list[EbayImportedOrder]) -> bool:
    lines = _bulk_lines(rows[0].raw_snapshot or {})
    if not lines:
        return True
    manifests = _manifests_for_order(session, rows[0].ebay_order_id)
    return all(manifests.get(key) is not None and manifests[key].status == "completed" for key, _, _ in lines)


def _price_html(line_item: dict | None) -> str:
    if not line_item:
        return "—"
    cost = line_item.get("lineItemCost") or {}
    value, currency = cost.get("value"), cost.get("currency", "")
    return f"${value} {currency}" if value else "—"


def _order_total_html(snapshot: dict) -> str:
    total = (snapshot.get("pricingSummary") or {}).get("total") or {}
    value, currency = total.get("value"), total.get("currency", "")
    return f"${value} {currency}" if value else "—"


def _order_card_html(session: Session, ebay_order_id: str, rows: list[EbayImportedOrder]) -> str:
    snapshot = rows[0].raw_snapshot or {}
    ship_to = _ship_to(snapshot)
    line_items = _line_items_by_sku(snapshot)
    bulk_lines = _bulk_lines(snapshot)
    manifests = _manifests_for_order(session, ebay_order_id)
    bulk_complete = _bulk_is_complete(session, rows)
    completed = all(row.dismissed_at is not None for row in rows) and bulk_complete
    # A SKU-less bulk line is drawn by the bulk checklist below; its stand-in
    # row must not also render as a plain card.
    bulk_skus = {line_item_sku(line_item) for _, line_item, _ in bulk_lines}

    cards = "".join(
        pack_card_html(
            session,
            session.get(ShopListing, row.shop_listing_id).inventory_item_id if row.shop_listing_id else None,
            title=(line_items.get(row.sku) or {}).get("title", row.sku),
            qty=row.quantity,
            price_html=_price_html(line_items.get(row.sku)),
            back_url="/ebay-sales",
            empty_note=_UNLISTED_NOTE if row.sku.startswith(UNLISTED_SKU_PREFIX) else
                       "Couldn't match this sku to a specific held card -- check "
                       "/ebay-listings for what this SKU maps to.",
        )
        for row in rows if row.sku not in bulk_skus
    )

    bulk_cards = "".join(
        _bulk_order_line_html(ebay_order_id, key, line_item, quantity, manifests.get(key))
        for key, line_item, quantity in bulk_lines
    )

    label = label_buttons_html('ebay', ebay_order_id, 'btn small')
    actions = label + (
        "<div class='muted'>Completed</div>" if completed else
        "<div class='bulk-warning'>Finish the bulk-card checklist before completing this order.</div>"
        if not bulk_complete else
        f"<form method='post' action='/ebay-sales/{escape(ebay_order_id)}/complete'>"
        "<button class='btn small'>Mark complete</button></form>"
        f"<form method='post' action='/ebay-sales/{escape(ebay_order_id)}/discard' "
        "onsubmit=\"return confirm('Discard this order from the active view?');\">"
        "<button class='btn small' style='background:none;color:var(--text-dim);"
        "border:1px solid var(--panel-border);box-shadow:none'>Discard</button></form>"
    )

    # Collapsed by default once completed -- an order still needing
    # action opens straight up, since that's the one a person actually
    # came here to work on.
    return (
        f"<details class='panel order-block'{'' if completed else ' open'}>"
        "<summary class='order-head'>"
        f"<div><strong>Order {escape(ebay_order_id)}</strong>"
        f"<span class='muted'> · {rows[0].observed_at:%Y-%m-%d %H:%M}</span></div>"
        f"<div class='detail-price'>{_order_total_html(snapshot)}</div>"
        "</summary>"
        f"<div class='muted ship-to'>{escape(ship_to['name'])} &mdash; {escape(ship_to['street'])}, "
        f"{escape(ship_to['locality'])}, {escape(ship_to['country'])}</div>"
        f"<div class='pack-grid'>{cards}{bulk_cards}</div>"
        f"<div class='order-block-actions'>{actions}</div>"
        "</details>"
    )


def _bulk_order_line_html(ebay_order_id: str, line_key: str, line_item: dict, quantity: int,
                          manifest: EbayBulkPickManifest | None) -> str:
    title = escape(line_item.get("title") or f"Bulk lot — {quantity} cards")
    price = _price_html(line_item)
    if manifest is None:
        action = (
            f"<form method='post' action='/ebay-sales/{escape(ebay_order_id)}/bulk-picks'>"
            f"<input type='hidden' name='line_item_id' value='{escape(line_key)}'>"
            f"<button class='btn small'>Build {quantity}-card pick list</button></form>"
        )
        state = "Not allocated yet"
    else:
        action = f"<a class='btn small' href='/ebay-sales/bulk-picks/{escape(manifest.id)}'>Open checklist</a>"
        state = "Completed" if manifest.status == "completed" else "Picking in progress"
    return (
        "<div class='panel pack-card bulk-line'>"
        "<div class='bulk-icon'>150</div><div class='pack-card-body'>"
        f"<div class='pack-card-title'>{title}</div>"
        f"<div class='muted'>{quantity} individual cards · {price}</div>"
        f"<div class='bulk-state'>{escape(state)}</div>{action}</div></div>"
    )


def _applied_orders(session: Session) -> dict[str, list[EbayImportedOrder]]:
    rows = session.scalars(
        select(EbayImportedOrder)
        .where(EbayImportedOrder.applied_at.isnot(None))
        .order_by(EbayImportedOrder.observed_at.desc())
    ).all()
    by_order: dict[str, list[EbayImportedOrder]] = {}
    for row in rows:
        by_order.setdefault(row.ebay_order_id, []).append(row)
    return by_order


def order_is_completed(session: Session, rows: list[EbayImportedOrder]) -> bool:
    return all(r.dismissed_at is not None for r in rows) and _bulk_is_complete(session, rows)


def open_orders(session: Session) -> dict[str, list[EbayImportedOrder]]:
    """Orders still waiting to be packed, newest first -- the one definition
    the Pack Assistant, the pick run, label printing and the nav badge share."""
    return {oid: g for oid, g in _applied_orders(session).items() if not order_is_completed(session, g)}


@router.get("/ebay-sales", response_class=HTMLResponse)
def ebay_sales_page(
    status: str = "action_needed", error: str | None = None,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    status = status if status in FILTERS else "action_needed"
    by_order = _applied_orders(session)

    if status == "action_needed":
        shown = {oid: g for oid, g in by_order.items() if not order_is_completed(session, g)}
    elif status == "completed":
        shown = {oid: g for oid, g in by_order.items() if order_is_completed(session, g)}
    else:
        shown = by_order

    orders_html = "".join(
        _order_card_html(session, ebay_order_id, group)
        for ebay_order_id, group in sorted(shown.items(), key=lambda kv: kv[1][0].observed_at, reverse=True)
    ) or "<div class='panel'><p class='muted'>Nothing here.</p></div>"

    filter_bar = "".join(
        f"<a class='filter-tab{' active' if key == status else ''}' href='/ebay-sales?status={key}'>{escape(label)}</a>"
        for key, label in FILTERS.items()
    )

    body = (
        brand_header("eBay sales")
        + f"<p class='subtitle'>{len(shown)} order(s), {sum(len(g) for g in shown.values())} card(s). "
          "<a class='btn small' href='/ebay-sales/pick-run'>Pick run: all open orders by drawer</a> "
          "<a class='btn small ghost' href='/labels/postage'>Print postage label</a></p>"
        + (f"<div class='notice error'>{escape(error)}</div>" if error else "")
        + f"<div class='filter-tabs'>{filter_bar}</div>"
        + orders_html
        + _STYLE + PACK_STYLE + LOCATE_STYLE
    )
    return HTMLResponse(page("EzBay — eBay sales", body))


@router.post("/ebay-sales/{ebay_order_id}/complete")
def complete_order(
    ebay_order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    rows = session.scalars(
        select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id)
    ).all()
    if rows and not _bulk_is_complete(session, rows):
        return RedirectResponse("/ebay-sales?error=Finish+the+bulk-card+checklist+first", status_code=303)
    return _dismiss(session, ebay_order_id, user, action="ebay_sale.complete")


@router.post("/ebay-sales/{ebay_order_id}/discard")
def discard_order(
    ebay_order_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    return _dismiss(session, ebay_order_id, user, action="ebay_sale.discard")


def _dismiss(session: Session, ebay_order_id: str, user: str, *, action: str) -> RedirectResponse:
    rows = session.scalars(
        select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == ebay_order_id)
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.dismissed_at = now
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action=action,
                resource_type="ebay_imported_order", resource_id=ebay_order_id, outcome="success",
                correlation_id=str(uuid.uuid4()), details={"line_count": len(rows)})
    return RedirectResponse("/ebay-sales", status_code=303)


def _raw_order_line(session: Session, ebay_order_id: str, line_item_id: str) -> tuple[dict, int] | None:
    row = session.scalar(
        select(EbayImportedOrder)
        .where(EbayImportedOrder.ebay_order_id == ebay_order_id)
        .order_by(EbayImportedOrder.observed_at.desc())
    )
    if row is None:
        return None
    for key, line_item, quantity in _bulk_lines(row.raw_snapshot or {}):
        if key == line_item_id:
            return line_item, quantity
    return None


def _candidate_rows(session: Session) -> dict[str, list[tuple[InventoryItem, ShopListing, CatalogItem, TcgCard, str]]]:
    """Eligible duplicate commons/uncommons, grouped by the chosen set.

    Each returned row has a confirmed live variation mapping, because the
    completion step must be able to reduce the exact eBay child SKU.  One
    physical copy is selected per identity and at least one is left behind.
    """
    reserved = set(session.scalars(
        select(EbayBulkPickItem.inventory_item_id)
        .join(EbayBulkPickManifest, EbayBulkPickManifest.id == EbayBulkPickItem.manifest_id)
        .where(EbayBulkPickManifest.status != "completed")
    ).all())
    rows = session.execute(
        select(InventoryItem, ShopListing, CatalogItem, TcgCard, CardSet.name, EbayExportItem.ebay_listing_id)
        .join(ShopListing, ShopListing.inventory_item_id == InventoryItem.id)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .join(CardSet, CardSet.id == TcgCard.set_id)
        .join(EbayExportItem, EbayExportItem.sku == ShopListing.id)
        .join(EbayListing, EbayListing.ebay_listing_id == EbayExportItem.ebay_listing_id)
        .where(
            func.lower(TcgCard.game) == "pokemon",
            func.lower(TcgCard.language) == "english",
            func.lower(TcgCard.rarity).in_(("common", "uncommon")),
            CardSet.name.in_(BULK_PICK_SET_NAMES),
            InventoryItem.variant == CardVariant.NORMAL,
            InventoryItem.status == InventoryStatus.AVAILABLE,
            InventoryItem.quantity > 1,
            ShopListing.status == ShopListingStatus.ACTIVE,
            ShopListing.quantity == InventoryItem.quantity,
            EbayExportItem.status == EbayExportItemStatus.CONFIRMED,
            EbayExportItem.kind == "variation_child",
            EbayExportItem.ebay_listing_id.isnot(None),
            func.lower(EbayListing.listing_status) == "active",
        )
        .order_by(CardSet.name, ShopListing.price, CatalogItem.name, TcgCard.number)
    ).all()
    grouped: dict[str, list[tuple[InventoryItem, ShopListing, CatalogItem, TcgCard, str]]] = {
        name: [] for name in BULK_PICK_SET_NAMES
    }
    seen: set[str] = set()
    for inventory, listing, catalog, tcg, set_name, ebay_listing_id in rows:
        if inventory.id in seen or inventory.id in reserved:
            continue
        seen.add(inventory.id)
        grouped[set_name].append((inventory, listing, catalog, tcg, ebay_listing_id))
    return grouped


@router.post("/ebay-sales/{ebay_order_id}/bulk-picks")
async def create_bulk_pick(
    ebay_order_id: str, request: Request, user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    form = await request.form()
    line_item_id = str(form.get("line_item_id") or "")
    existing = session.scalar(
        select(EbayBulkPickManifest).where(
            EbayBulkPickManifest.ebay_order_id == ebay_order_id,
            EbayBulkPickManifest.line_item_id == line_item_id,
        )
    )
    if existing:
        return RedirectResponse(f"/ebay-sales/bulk-picks/{existing.id}", status_code=303)
    raw = _raw_order_line(session, ebay_order_id, line_item_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="bulk order line not found")
    line_item, requested_quantity = raw
    expected = BULK_PICK_PER_SET * len(BULK_PICK_SET_NAMES)
    if requested_quantity != expected:
        raise HTTPException(status_code=400, detail=f"this picker is configured for exactly {expected} cards")
    grouped = _candidate_rows(session)
    shortages = {name: BULK_PICK_PER_SET - len(rows) for name, rows in grouped.items()
                 if len(rows) < BULK_PICK_PER_SET}
    if shortages:
        message = ", ".join(f"{name}: {count} more needed" for name, count in shortages.items())
        return RedirectResponse(f"/ebay-sales?error={quote(message)}", status_code=303)

    manifest = EbayBulkPickManifest(
        ebay_order_id=ebay_order_id,
        line_item_id=line_item_id,
        ebay_listing_id=str(line_item.get("legacyItemId") or line_item.get("itemId") or "") or None,
        title=line_item.get("title") or f"Bulk lot — {requested_quantity} cards",
        requested_quantity=requested_quantity,
        status="picking",
    )
    session.add(manifest)
    session.flush()
    for set_name in BULK_PICK_SET_NAMES:
        for inventory, listing, _catalog, _tcg, ebay_listing_id in grouped[set_name][:BULK_PICK_PER_SET]:
            session.add(EbayBulkPickItem(
                manifest_id=manifest.id,
                inventory_item_id=inventory.id,
                shop_listing_id=listing.id,
                ebay_listing_id=ebay_listing_id,
                quantity=1,
                reported_variant=inventory.variant.value,
            ))
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="ebay_bulk_pick.create",
        resource_type="ebay_bulk_pick_manifest", resource_id=manifest.id, outcome="success",
        correlation_id=str(uuid.uuid4()), details={"order_id": ebay_order_id, "card_count": expected},
    )
    return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest.id}", status_code=303)


def _manifest_rows(session: Session, manifest_id: str):
    return session.execute(
        select(EbayBulkPickItem, InventoryItem, ShopListing, CatalogItem, TcgCard, CardSet)
        .join(InventoryItem, InventoryItem.id == EbayBulkPickItem.inventory_item_id)
        .join(ShopListing, ShopListing.id == EbayBulkPickItem.shop_listing_id)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .join(CardSet, CardSet.id == TcgCard.set_id)
        .where(EbayBulkPickItem.manifest_id == manifest_id)
        .order_by(CardSet.name, CatalogItem.name, TcgCard.number)
    ).all()


@router.get("/ebay-sales/bulk-picks/{manifest_id}", response_class=HTMLResponse)
def bulk_pick_page(
    manifest_id: str, error: str | None = None,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    manifest = session.get(EbayBulkPickManifest, manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="bulk pick list not found")
    rows = _manifest_rows(session, manifest_id)
    picked = sum(item.quantity for item, *_ in rows if item.picked_at is not None)
    groups = []
    for set_name in BULK_PICK_SET_NAMES:
        set_rows = [row for row in rows if row[-1].name == set_name]
        cards = "".join(_pick_card_html(session, manifest, *row) for row in set_rows)
        set_picked = sum(item.quantity for item, *_ in set_rows if item.picked_at is not None)
        set_total = sum(item.quantity for item, *_ in set_rows)
        groups.append(
            f"<section><h2>{escape(set_name)} <span class='muted'>{set_picked}/{set_total}</span></h2>"
            f"<div class='pick-grid'>{cards}</div></section>"
        )
    percent = round((picked / manifest.requested_quantity) * 100) if manifest.requested_quantity else 0
    notice = f"<div class='notice error'>{escape(error)}</div>" if error else ""
    if manifest.error:
        notice += f"<div class='notice error'>{escape(manifest.error)}</div>"
    if manifest.status == "completed":
        confirm = "<div class='notice success'><strong>Complete.</strong> Inventory and live eBay quantities have been reduced.</div>"
    elif picked == manifest.requested_quantity:
        confirm = (
            f"<form class='confirm-pick panel' method='post' action='/ebay-sales/bulk-picks/{escape(manifest.id)}/confirm' "
            "onsubmit=\"return confirm('Reduce all 150 inventory and live eBay quantities now?');\">"
            "<label><input type='checkbox' name='confirmed' value='yes' required> "
            "I have physically found and packed all 150 cards.</label>"
            "<button class='btn'>Confirm pack and update stock</button></form>"
        )
    else:
        confirm = "<div class='notice'>Find every card before stock can be changed.</div>"
    body = (
        brand_header("150-card eBay order")
        + "<p><a href='/ebay-sales'>← Back to eBay sales</a></p>"
        + f"<div class='panel pick-summary'><h1>{escape(manifest.title)}</h1>"
          f"<div class='pick-count'>{picked} / {manifest.requested_quantity} found</div>"
          f"<div class='progress-track'><span style='width:{percent}%'></span></div>"
          "<p class='muted'>One duplicate copy of each card; at least one copy remains in inventory.</p></div>"
        + notice + "".join(groups) + confirm + _PICK_STYLE
    )
    return HTMLResponse(page("EzBay — bulk order checklist", body))


def _pick_card_html(session: Session, manifest: EbayBulkPickManifest, item: EbayBulkPickItem, inventory: InventoryItem,
                    listing: ShopListing, catalog: CatalogItem, tcg: TcgCard, card_set: CardSet) -> str:
    # Use the shared lookup so confirmed per-copy tray placements win over the
    # legacy aggregate location, exactly as they do for ordinary order lines.
    detail = line_detail(session, inventory.id) or line_detail_from_models(
        inventory, catalog, tcg, card_set
    )
    src = thumb_src(detail)
    image = f"<img src='{src}' alt='' loading='lazy'>" if src else "<div class='pick-noart'>No photo</div>"
    found = item.picked_at is not None
    action = "unpick" if found else "pick"
    label = "✓ Found" if found else "Mark found"
    reported = item.reported_variant or inventory.variant.value
    reported_label = reported.replace("_", " ").title()
    stored_label = inventory.variant.value.replace("_", " ").title()
    mismatch = (
        f"<div class='variant-warning'>Selected as {escape(reported_label)}; inventory is labelled {escape(stored_label)}</div>"
        if reported != inventory.variant.value else ""
    )
    quantity_label = f" × {item.quantity}" if item.quantity > 1 else ""
    return (
        f"<article class='pick-card{' found' if found else ''}'>{image}<div class='pick-info'>"
        f"<strong>{escape(catalog.name)}{quantity_label}</strong><div class='muted'>#{escape(tcg.number or '')} · "
        f"{escape(tcg.rarity or '')} · {escape(reported_label)} · Qty before: {inventory.quantity}</div>{mismatch}"
        f"<div class='pick-location'><span>Pick location</span>{location_pill_html(detail)}</div>"
        f"<form method='post' action='/ebay-sales/bulk-picks/{escape(manifest.id)}/items/{escape(item.id)}/toggle'>"
        f"<input type='hidden' name='action' value='{action}'>"
        f"<button class='pick-button{' picked' if found else ''}'>{label}</button></form>"
        "</div></article>"
    )


def line_detail_from_models(inventory: InventoryItem, catalog: CatalogItem, tcg: TcgCard,
                            card_set: CardSet) -> dict:
    return {
        "inventory_item_id": inventory.id,
        "card_id": inventory.catalog_item_id,
        "name": catalog.name,
        "variant": inventory.variant.value,
        "condition": inventory.condition,
        "set_name": card_set.name,
        "number": tcg.number or "",
        "rarity": tcg.rarity or "",
        "location_name": inventory.storage_location.name if inventory.storage_location else None,
        "has_scan": bool(inventory.scan_image_path),
    }


@router.post("/ebay-sales/bulk-picks/{manifest_id}/items/{item_id}/toggle")
async def toggle_bulk_pick_item(
    manifest_id: str, item_id: str, request: Request,
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> RedirectResponse:
    manifest = session.get(EbayBulkPickManifest, manifest_id)
    item = session.get(EbayBulkPickItem, item_id)
    if manifest is None or item is None or item.manifest_id != manifest_id:
        raise HTTPException(status_code=404, detail="pick-list card not found")
    if manifest.status == "completed":
        return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}", status_code=303)
    form = await request.form()
    item.picked_at = None if form.get("action") == "unpick" else datetime.now(UTC)
    manifest.status = "picking"
    manifest.error = None
    session.commit()
    return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}", status_code=303)


@router.post("/ebay-sales/bulk-picks/{manifest_id}/confirm")
async def confirm_bulk_pick(
    manifest_id: str, request: Request, user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    manifest = session.get(EbayBulkPickManifest, manifest_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="bulk pick list not found")
    if manifest.status == "completed":
        return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}", status_code=303)
    form = await request.form()
    if form.get("confirmed") != "yes":
        return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}?error=Confirmation+is+required", status_code=303)
    rows = _manifest_rows(session, manifest_id)
    if sum(item.quantity for item, *_ in rows) != manifest.requested_quantity or any(
        item.picked_at is None for item, *_ in rows
    ):
        return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}?error=All+cards+must+be+found+first", status_code=303)
    for item, inventory, listing, *_ in rows:
        # The generated picker initially chose duplicates and left one copy,
        # but an operator-entered physical selection may legitimately use the
        # final copy (or two final copies) of a variant.  That is safe: the
        # remote quantity becomes zero and decrement_listing_quantity marks
        # the local listing sold out.  Only reject genuinely insufficient or
        # already-drifted stock.
        if (inventory.quantity < item.quantity or listing.quantity != inventory.quantity
                or listing.status != ShopListingStatus.ACTIVE):
            message = f"Stock changed for {listing.title}; review the order before retrying."
            return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}?error={quote(message)}", status_code=303)

    settings = request.app.state.settings
    try:
        cipher, oauth = ebay_services(settings)
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayTradingQuantityAdapter(
            token, settings.ebay_client_id, environment=settings.ebay_env,
            marketplace_id=settings.ebay_marketplace_id,
        )
        manifest.status = "syncing"
        manifest.error = None
        session.commit()
        unsynced = [row for row in rows if row[0].remote_quantity_synced_at is None]
        for start in range(0, len(unsynced), MAX_REVISIONS_PER_REQUEST):
            batch = unsynced[start:start + MAX_REVISIONS_PER_REQUEST]
            revisions = [
                InventoryRevision(item.ebay_listing_id, listing.id, listing.quantity - item.quantity)
                for item, _inventory, listing, *_ in batch
            ]
            await adapter.revise_inventory_status(revisions)
            now = datetime.now(UTC)
            for item, *_ in batch:
                item.remote_quantity_synced_at = now
            session.commit()
    except (OAuthError, EbayApiError, HTTPException, ValueError) as exc:
        manifest.error = str(getattr(exc, "detail", exc))
        session.commit()
        return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}?error={quote(manifest.error)}", status_code=303)

    # Remote quantities are absolute and saved batch-by-batch above.  Local
    # stock changes only after every remote child succeeded, making a retry
    # safe if eBay fails halfway through.
    for item, _inventory, listing, *_ in rows:
        decrement_listing_quantity(session, listing, item.quantity)
    manifest.status = "completed"
    manifest.error = None
    manifest.completed_at = datetime.now(UTC)
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="ebay_bulk_pick.complete",
        resource_type="ebay_bulk_pick_manifest", resource_id=manifest.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"order_id": manifest.ebay_order_id, "card_count": manifest.requested_quantity},
    )
    return RedirectResponse(f"/ebay-sales/bulk-picks/{manifest_id}", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 18px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.order-block{margin-bottom:14px}
.order-block[open]{padding-bottom:16px}
.order-head{display:flex;justify-content:space-between;align-items:center;cursor:pointer;list-style:none}
.order-head::-webkit-details-marker{display:none}
.order-head::before{content:'▸';margin-right:8px;color:var(--text-dim);font-size:12px}
details[open]>.order-head::before{content:'▾'}
.ship-to{margin:10px 0;font-size:13.5px}
.order-block-actions{display:flex;gap:8px;margin-top:12px}
.btn.small{padding:6px 12px;font-size:13px}
.bulk-line{display:flex;flex-direction:row;align-items:center}
.bulk-icon{width:82px;height:112px;flex:none;border-radius:8px;background:var(--surface-sunken);
  display:flex;align-items:center;justify-content:center;font-size:25px;font-weight:700;color:var(--accent)}
.bulk-state{font-size:13px;font-weight:600;margin:8px 0}
.bulk-warning{color:#9a6700;background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;padding:7px 10px;font-size:13px}
@media (max-width: 680px) {
  .order-head{flex-wrap:wrap;gap:6px 12px}
  .order-block-actions{flex-wrap:wrap}
}
</style>"""

_PICK_STYLE = """<style>
.pick-summary h1{font-size:20px;margin:0 0 8px}.pick-count{font-size:26px;font-weight:750}
.progress-track{height:12px;background:var(--surface-sunken);border-radius:999px;overflow:hidden;margin:10px 0}
.progress-track span{display:block;height:100%;background:var(--accent);transition:width .2s}
.pick-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px}
.pick-card{border:1px solid var(--panel-border);border-radius:12px;background:var(--panel);overflow:hidden}
.pick-card.found{border-color:#1a7f37;background:#f0fff4}.pick-card img,.pick-noart{width:100%;aspect-ratio:5/7;
  object-fit:cover;background:var(--surface-sunken)}.pick-noart{display:flex;align-items:center;justify-content:center;color:var(--text-dim)}
.pick-info{padding:10px}.pick-info strong{display:block;line-height:1.25;margin-bottom:4px}
.pick-location{margin-top:8px;padding:7px 8px;border-radius:7px;background:var(--surface-sunken)}
.pick-location>span{display:block;margin-bottom:4px;font-size:10px;font-weight:750;letter-spacing:.05em;
  text-transform:uppercase;color:var(--text-dim)}
.pick-location .loc-pill{font-size:13px;font-weight:750;background:var(--panel);color:var(--text)}
.variant-warning{margin-top:6px;padding:5px 7px;border-radius:6px;background:#fff8c5;color:#7a4d00;font-size:11px}
.pick-button{width:100%;margin-top:9px;padding:10px;border-radius:8px;border:1px solid var(--accent);
  background:#fff;color:var(--accent);font-weight:700}.pick-button.picked{background:#1a7f37;color:#fff;border-color:#1a7f37}
.confirm-pick{position:sticky;bottom:10px;display:flex;gap:12px;align-items:center;justify-content:space-between;
  border:2px solid var(--accent);z-index:2}.confirm-pick label{font-weight:600}.notice.success{border-color:#1a7f37;color:#116329}
@media(max-width:680px){.pick-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.pick-info{padding:8px}
  .pick-info .muted{font-size:11px}.confirm-pick{align-items:stretch;flex-direction:column}.confirm-pick .btn{width:100%}}
</style>"""
