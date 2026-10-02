"""Inventory-backed Whatnot show preparation, settlement and CRM handoff."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .commerce import active_fee_rule
from .database import get_session
from .models import (
    CatalogItem, Customer, EbayListingLink, EbayListingLinkStatus, InventoryItem, InventoryStatus,
    Marketplace, Sale, SaleItem, ShopListing, ShopListingStatus, StorageLocation,
    WhatnotShow, WhatnotShowItem, WhatnotShowItemOutcome, WhatnotShowStatus,
)
from .ui import brand_header, page, pill

router = APIRouter(prefix="/whatnot", tags=["whatnot"])
CENT = Decimal("0.01")

OUTCOME_KIND = {
    WhatnotShowItemOutcome.SOLD: "ok", WhatnotShowItemOutcome.UNSOLD: "neutral",
    WhatnotShowItemOutcome.GIVEAWAY: "warn", WhatnotShowItemOutcome.PENDING: "neutral",
}
STATUS_KIND = {
    WhatnotShowStatus.DRAFT: "neutral", WhatnotShowStatus.LIVE: "warn",
    WhatnotShowStatus.COMPLETED: "ok", WhatnotShowStatus.CANCELLED: "bad",
}


def _decimal(value: str, label: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(value.strip() or "0")
    except (InvalidOperation, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a valid amount") from exc
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        qualifier = "greater than zero" if positive else "zero or more"
        raise HTTPException(status_code=400, detail=f"{label} must be {qualifier}")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def _nonnegative_int(value: str, label: str) -> int:
    try:
        number = int(value.strip() or "0")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a whole number") from exc
    if number < 0:
        raise HTTPException(status_code=400, detail=f"{label} cannot be negative")
    return number


def _whatnot_marketplace(session: Session) -> Marketplace | None:
    return session.scalar(select(Marketplace).where(func.lower(Marketplace.name) == "whatnot"))


def _show_stats(session: Session, show_id: str) -> dict:
    items = session.scalars(
        select(WhatnotShowItem)
        .where(WhatnotShowItem.whatnot_show_id == show_id)
        .order_by(WhatnotShowItem.id)
    ).all()
    sold = [item for item in items if item.outcome == WhatnotShowItemOutcome.SOLD]
    revenue = sum(((item.final_price or Decimal("0")) * item.quantity for item in sold), Decimal("0"))
    settled = [item for item in items if item.outcome != WhatnotShowItemOutcome.PENDING]
    sell_through = (len(sold) / len(settled) * 100) if settled else None
    return {
        "items": items, "sold_count": len(sold), "total_count": len(items),
        "revenue": revenue, "sell_through": sell_through,
        "pending_count": len(items) - len(settled),
    }


def _active_fee_notice(session: Session, show: WhatnotShow) -> tuple[object | None, str]:
    marketplace = _whatnot_marketplace(session)
    rule = active_fee_rule(session, marketplace.id, show.category) if marketplace else None
    if rule is None:
        return None, (
            "<div class='notice warn'><strong>No active Whatnot fee rule.</strong> Enter the exact fee from "
            "Seller Hub when settling each sale, or <a href='/marketplaces'>add a dated fee rule</a> for estimates. "
            "A sale cannot be recorded with an invented zero fee.</div>"
        )
    return rule, (
        "<div class='notice'>Estimated fee rule: "
        f"{rule.commission_pct}% commission + {rule.processing_pct}% processing + ${rule.fixed_fee}. "
        "Enter Seller Hub's exact fee whenever available; GST and promotions can change the actual amount.</div>"
    )


def _normalise_handle(value: str) -> str:
    return value.strip().lstrip("@").lower()[:128]


def _customer_for_handle(session: Session, handle: str) -> Customer:
    for customer in session.scalars(select(Customer).where(Customer.platform_handles.is_not(None))):
        handles = customer.platform_handles or {}
        if _normalise_handle(str(handles.get("whatnot", ""))) == handle:
            return customer
    customer = Customer(
        id=str(uuid.uuid4()), display_name=f"@{handle}",
        platform_handles={"whatnot": handle}, segment="Whatnot",
    )
    session.add(customer)
    session.flush()
    return customer


def _channel_blocker(session: Session, inventory_id: str) -> str | None:
    listing = session.scalar(
        select(ShopListing).where(
            ShopListing.inventory_item_id == inventory_id,
            ShopListing.quantity > 0,
            ShopListing.status != ShopListingStatus.WITHDRAWN,
        ).limit(1)
    )
    if listing is not None:
        return "This holding still has an NL Vault/eBay listing. Withdraw it before allocating stock to Whatnot."
    ebay = session.scalar(
        select(EbayListingLink)
        .join(ShopListing, ShopListing.id == EbayListingLink.shop_listing_id)
        .where(
            ShopListing.inventory_item_id == inventory_id,
            EbayListingLink.status.in_([
                EbayListingLinkStatus.PUBLISHING, EbayListingLinkStatus.ACTIVE, EbayListingLinkStatus.ENDING,
            ]),
        ).limit(1)
    )
    if ebay is not None:
        return "This holding still has a live/in-flight eBay offer. End it before allocating stock to Whatnot."
    return None


def _eligible_inventory(session: Session, query: str = "") -> list[tuple[InventoryItem, CatalogItem, StorageLocation | None]]:
    listing_exists = exists(
        select(ShopListing.id).where(
            ShopListing.inventory_item_id == InventoryItem.id,
            ShopListing.quantity > 0,
            ShopListing.status != ShopListingStatus.WITHDRAWN,
        )
    )
    ebay_exists = exists(
        select(EbayListingLink.id)
        .join(ShopListing, ShopListing.id == EbayListingLink.shop_listing_id)
        .where(
            ShopListing.inventory_item_id == InventoryItem.id,
            EbayListingLink.status.in_([
                EbayListingLinkStatus.PUBLISHING, EbayListingLinkStatus.ACTIVE, EbayListingLinkStatus.ENDING,
            ]),
        )
    )
    statement = (
        select(InventoryItem, CatalogItem, StorageLocation)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .outerjoin(StorageLocation, StorageLocation.id == InventoryItem.storage_location_id)
        .where(
            InventoryItem.status == InventoryStatus.AVAILABLE,
            InventoryItem.quantity > 0,
            ~listing_exists,
            ~ebay_exists,
        )
        .order_by(CatalogItem.name, InventoryItem.condition)
        .limit(100)
    )
    if query:
        statement = statement.where(CatalogItem.name.ilike(f"%{query}%"))
    return session.execute(statement).all()


def _inventory_description(inventory: InventoryItem, catalog: CatalogItem) -> str:
    variant = inventory.variant.value.replace("_", " ").title() if inventory.variant else ""
    details = " · ".join(part for part in (variant, inventory.condition) if part)
    return f"{catalog.name} — {details}" if details else catalog.name


def _reserve_inventory(session: Session, inventory_id: str) -> tuple[InventoryItem, str]:
    inventory = session.get(InventoryItem, inventory_id)
    if inventory is None:
        raise HTTPException(status_code=404, detail="inventory item not found")
    result = session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == inventory_id,
            InventoryItem.status == InventoryStatus.AVAILABLE,
            InventoryItem.quantity >= 1,
        )
        .values(quantity=InventoryItem.quantity - 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="this inventory is no longer available")
    blocker = _channel_blocker(session, inventory_id)
    if blocker:
        session.rollback()
        raise HTTPException(status_code=409, detail=blocker)
    session.expire(inventory)
    if inventory.quantity == 0:
        inventory.status = InventoryStatus.RESERVED
    catalog = session.get(CatalogItem, inventory.catalog_item_id)
    if catalog is None:
        session.rollback()
        raise HTTPException(status_code=409, detail="inventory has no canonical catalogue item")
    return inventory, _inventory_description(inventory, catalog)


def _release_inventory(session: Session, item: WhatnotShowItem) -> None:
    if item.stock_reserved_at is None or item.stock_released_at is not None or not item.inventory_item_id:
        return
    inventory = session.get(InventoryItem, item.inventory_item_id)
    if inventory is not None:
        inventory.quantity += item.quantity
        if inventory.status == InventoryStatus.RESERVED:
            inventory.status = InventoryStatus.AVAILABLE
    item.stock_released_at = datetime.now(UTC)


def _fee_for_sale(
    session: Session, show: WhatnotShow, item_total: Decimal,
    buyer_shipping: Decimal, buyer_tax: Decimal, actual_fees: str,
) -> tuple[Decimal, str]:
    marketplace = _whatnot_marketplace(session)
    if marketplace is None:
        raise HTTPException(
            status_code=409,
            detail="configure the Whatnot marketplace before recording a sale",
        )
    if actual_fees.strip():
        return _decimal(actual_fees, "actual fee"), "actual fee entered from Whatnot Seller Hub"
    rule = active_fee_rule(session, marketplace.id, show.category)
    if rule is None:
        raise HTTPException(
            status_code=409,
            detail="enter the exact Whatnot fee or configure an active fee rule before recording this sale",
        )
    commission = item_total * rule.commission_pct / 100
    processing = (item_total + buyer_shipping + buyer_tax) * rule.processing_pct / 100 + rule.fixed_fee
    return (commission + processing).quantize(CENT, rounding=ROUND_HALF_UP), "estimated from active fee rule"


@router.get("", response_class=HTMLResponse)
def whatnot_shows_page(
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    shows = session.scalars(select(WhatnotShow).order_by(WhatnotShow.show_date.desc())).all()
    rows = []
    for show in shows:
        stats = _show_stats(session, show.id)
        sell_through = f"{stats['sell_through']:.0f}%" if stats["sell_through"] is not None else "—"
        rows.append(
            "<tr>"
            f"<td><a href='/whatnot/{escape(show.id)}'>{escape(show.title)}</a></td>"
            f"<td>{show.show_date:%Y-%m-%d}</td><td>{escape(show.category)}</td>"
            f"<td>{pill(show.status.value, STATUS_KIND[show.status])}</td>"
            f"<td>{stats['sold_count']} / {stats['total_count']}</td>"
            f"<td>${stats['revenue']:,.2f}</td><td>{sell_through}</td></tr>"
        )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Show</th><th>Date</th><th>Category</th><th>Status</th>"
        "<th>Sold / total</th><th>Revenue</th><th>Sell-through</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=7>No shows yet.</td></tr>'}</tbody></table></div></div>"
    )
    form = (
        "<div class='panel'><h2>Plan a show</h2>"
        "<form method='post' action='/whatnot' class='calc-form'>"
        "<label>Title<input name='title' required maxlength='256' placeholder='Friday One Piece Night'></label>"
        "<label>Show date<input name='show_date' type='date'></label>"
        "<label>Whatnot category<input name='category' value='TCG' maxlength='64' required></label>"
        "<button type='submit'>Create draft show</button></form></div>"
    )
    body = brand_header("Whatnot shows") + table + form + _STYLE
    return HTMLResponse(page("EzBay — Whatnot", body))


@router.post("")
def create_show(
    title: str = Form(...), show_date: str = Form(""), category: str = Form("TCG"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    clean_title, clean_category = title.strip()[:256], category.strip()[:64]
    if not clean_title or not clean_category:
        raise HTTPException(status_code=400, detail="title and category are required")
    try:
        date = datetime.fromisoformat(show_date).replace(tzinfo=UTC) if show_date else datetime.now(UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="show date is invalid") from exc
    show = WhatnotShow(id=str(uuid.uuid4()), title=clean_title, category=clean_category, show_date=date)
    session.add(show)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="whatnot.show.create",
                 resource_type="whatnot_show", resource_id=show.id, outcome="success",
                 correlation_id=show.id, details={"category": clean_category})
    return RedirectResponse(f"/whatnot/{show.id}", status_code=303)


@router.get("/{show_id}", response_class=HTMLResponse)
def show_detail(
    show_id: str, q: str = Query(""), user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    stats = _show_stats(session, show_id)
    _rule, fee_notice = _active_fee_notice(session, show)

    item_rows = []
    for item in stats["items"]:
        settle = "—"
        if item.outcome == WhatnotShowItemOutcome.PENDING and show.status == WhatnotShowStatus.LIVE:
            settle = (
                f"<form method='post' action='/whatnot/{escape(show_id)}/items/{escape(item.id)}/outcome' "
                "class='outcome-form'>"
                "<input name='final_price' type='number' min='0.01' step='0.01' placeholder='sale price'>"
                "<input name='actual_fees' type='number' min='0' step='0.01' placeholder='exact fee'>"
                "<input name='buyer_handle' maxlength='128' placeholder='buyer @handle'>"
                "<input name='whatnot_order_id' maxlength='128' placeholder='order ID'>"
                "<input name='buyer_shipping' type='number' min='0' step='0.01' placeholder='buyer shipping'>"
                "<input name='buyer_tax' type='number' min='0' step='0.01' placeholder='buyer tax'>"
                "<div class='outcome-buttons'>"
                "<button name='outcome' value='sold' class='outcome-btn sold'>Sold</button>"
                "<button name='outcome' value='unsold' class='outcome-btn'>Unsold</button>"
                "<button name='outcome' value='giveaway' class='outcome-btn'>Giveaway</button>"
                "</div></form>"
            )
        fee = f"${item.fees_amount:,.2f}" if item.fees_amount is not None else "—"
        buyer = f"@{escape(item.buyer_handle)}" if item.buyer_handle else "—"
        item_rows.append(
            "<tr>"
            f"<td>{escape(item.description)}</td><td>{item.quantity}</td>"
            f"<td>{f'${item.starting_price:,.2f}' if item.starting_price is not None else '—'}</td>"
            f"<td>{f'${item.final_price:,.2f}' if item.final_price is not None else '—'}</td>"
            f"<td>{fee}</td><td>{buyer}</td>"
            f"<td>{pill(item.outcome.value, OUTCOME_KIND[item.outcome])}</td><td>{settle}</td></tr>"
        )
    items_table = (
        "<div class='panel'><h2>Show queue</h2><div class='table-wrap'><table>"
        "<thead><tr><th>Inventory</th><th>Qty</th><th>Start</th><th>Final</th><th>Fee</th>"
        "<th>Buyer</th><th>Outcome</th><th>Settle</th></tr></thead>"
        f"<tbody>{''.join(item_rows) or '<tr><td colspan=8>No inventory queued yet.</td></tr>'}</tbody>"
        "</table></div></div>"
    )

    queue_panel = ""
    if show.status in (WhatnotShowStatus.DRAFT, WhatnotShowStatus.LIVE):
        eligible = _eligible_inventory(session, q.strip())
        options = "".join(
            f"<option value='{escape(inv.id)}'>{escape(_inventory_description(inv, catalog))} "
            f"· qty {inv.quantity} · {escape(location.name if location else 'not located')}</option>"
            for inv, catalog, location in eligible
        )
        queue_panel = (
            "<div class='panel'><h2>Add owned inventory</h2>"
            "<p class='muted'>Only available stock with no active NL Vault/eBay allocation is shown. "
            "Adding it reserves one unit immediately.</p>"
            f"<form method='get' action='/whatnot/{escape(show_id)}' class='inline-search'>"
            f"<input name='q' value='{escape(q)}' placeholder='Filter up to 100 eligible items'><button>Find</button></form>"
            + (
                f"<form method='post' action='/whatnot/{escape(show_id)}/items' class='calc-form'>"
                f"<label>Inventory<select name='inventory_item_id' required>{options}</select></label>"
                "<label>Starting price<input name='starting_price' type='number' min='0' step='0.01'></label>"
                "<button type='submit'>Reserve &amp; add to queue</button></form>"
                if options else "<p class='muted'>No eligible inventory matches.</p>"
            ) + "</div>"
        )

    actions = ""
    if show.status == WhatnotShowStatus.DRAFT:
        actions = (
            f"<form method='post' action='/whatnot/{show.id}/start'><button>Start show</button></form>"
            f"<form method='post' action='/whatnot/{show.id}/cancel'><button class='danger'>Cancel draft</button></form>"
        )
    elif show.status == WhatnotShowStatus.LIVE:
        actions = (
            f"<form method='post' action='/whatnot/{show.id}/complete' class='completion-form'>"
            "<input name='viewer_count' type='number' min='0' placeholder='Peak viewers'>"
            "<input name='follower_count' type='number' min='0' placeholder='Followers after show'>"
            "<button>Complete show</button></form>"
            f"<form method='post' action='/whatnot/{show.id}/cancel'><button class='danger'>Cancel &amp; release pending</button></form>"
        )

    sell_through = f"{stats['sell_through']:.0f}%" if stats["sell_through"] is not None else "—"
    stat_grid = (
        "<div class='panel'><div class='show-head'>"
        f"{pill(show.status.value, STATUS_KIND[show.status])}<div class='show-actions'>{actions}</div></div>"
        "<div class='calc-grid'>"
        f"<div><div class='label'>Sold / queued</div><div class='value'>{stats['sold_count']} / {stats['total_count']}</div></div>"
        f"<div><div class='label'>Pending</div><div class='value'>{stats['pending_count']}</div></div>"
        f"<div><div class='label'>Revenue</div><div class='value'>${stats['revenue']:,.2f}</div></div>"
        f"<div><div class='label'>Sell-through</div><div class='value'>{sell_through}</div></div>"
        "</div></div>"
    )
    body = (
        brand_header(show.title)
        + f"<p class='subtitle'>{show.show_date:%Y-%m-%d} · {escape(show.category)}</p>"
        + fee_notice + stat_grid + items_table + queue_panel + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {show.title}", body))


@router.post("/{show_id}/items")
def add_show_item(
    show_id: str, inventory_item_id: str = Form(...), starting_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    if show.status not in (WhatnotShowStatus.DRAFT, WhatnotShowStatus.LIVE):
        raise HTTPException(status_code=409, detail="this show no longer accepts inventory")
    price = _decimal(starting_price, "starting price") if starting_price.strip() else None
    inventory, description = _reserve_inventory(session, inventory_item_id)
    item = WhatnotShowItem(
        id=str(uuid.uuid4()), whatnot_show_id=show_id, inventory_item_id=inventory.id,
        description=description, quantity=1, starting_price=price,
        stock_reserved_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="whatnot.item.reserve",
                 resource_type="whatnot_show_item", resource_id=item.id, outcome="success",
                 correlation_id=show.id, details={"inventory_item_id": inventory.id})
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


@router.post("/{show_id}/start")
def start_show(
    show_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    count = session.scalar(select(func.count(WhatnotShowItem.id)).where(WhatnotShowItem.whatnot_show_id == show_id))
    if show.status != WhatnotShowStatus.DRAFT:
        raise HTTPException(status_code=409, detail="only a draft show can start")
    if not count:
        raise HTTPException(status_code=409, detail="queue at least one inventory item before starting")
    show.status = WhatnotShowStatus.LIVE
    show.started_at = datetime.now(UTC)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="whatnot.show.start",
                 resource_type="whatnot_show", resource_id=show.id, outcome="success",
                 correlation_id=show.id, details={"queued_items": count})
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


@router.post("/{show_id}/items/{item_id}/outcome")
def mark_outcome(
    show_id: str, item_id: str, outcome: str = Form(...), final_price: str = Form(""),
    actual_fees: str = Form(""), buyer_handle: str = Form(""), whatnot_order_id: str = Form(""),
    buyer_shipping: str = Form(""), buyer_tax: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    show = session.get(WhatnotShow, show_id)
    item = session.get(WhatnotShowItem, item_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    if item is None or item.whatnot_show_id != show_id:
        raise HTTPException(status_code=404, detail="show item not found")
    if show.status != WhatnotShowStatus.LIVE:
        raise HTTPException(status_code=409, detail="items can only be settled while the show is live")
    try:
        selected = WhatnotShowItemOutcome(outcome)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid outcome") from exc
    if selected == WhatnotShowItemOutcome.PENDING:
        raise HTTPException(status_code=400, detail="choose a final outcome")
    if item.outcome != WhatnotShowItemOutcome.PENDING:
        return RedirectResponse(f"/whatnot/{show_id}", status_code=303)

    if selected == WhatnotShowItemOutcome.SOLD:
        price = _decimal(final_price, "final price", positive=True)
        shipping = _decimal(buyer_shipping, "buyer shipping")
        tax = _decimal(buyer_tax, "buyer tax")
        handle = _normalise_handle(buyer_handle)
        if not handle:
            raise HTTPException(status_code=400, detail="buyer handle is required for a sale")
        external_id = whatnot_order_id.strip()[:128] or None
        fees, fee_source = _fee_for_sale(session, show, price * item.quantity, shipping, tax, actual_fees)

        try:
            claim = session.execute(
                update(WhatnotShowItem)
                .where(WhatnotShowItem.id == item.id, WhatnotShowItem.outcome == WhatnotShowItemOutcome.PENDING)
                .values(
                    outcome=selected, final_price=price, fees_amount=fees,
                    buyer_shipping=shipping, buyer_tax=tax, buyer_handle=handle,
                    whatnot_order_id=external_id,
                )
                .execution_options(synchronize_session=False)
            )
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="that Whatnot order ID is already recorded") from exc
        if claim.rowcount != 1:
            session.rollback()
            return RedirectResponse(f"/whatnot/{show_id}", status_code=303)
        customer = _customer_for_handle(session, handle)
        marketplace = _whatnot_marketplace(session)
        # _fee_for_sale has already required this row in the same transaction.
        assert marketplace is not None
        inventory = session.get(InventoryItem, item.inventory_item_id) if item.inventory_item_id else None
        if inventory is not None and inventory.quantity == 0:
            inventory.status = InventoryStatus.SOLD
        sale = Sale(
            id=str(uuid.uuid4()), marketplace_id=marketplace.id, source="whatnot",
            customer_id=customer.id, gross_amount=price * item.quantity, fees_amount=fees,
            notes=(f"Whatnot show: {show.title}; {fee_source}"
                   + (f"; order {external_id}" if external_id else "")
                   + (f"; buyer shipping ${shipping}; buyer tax ${tax}" if shipping or tax else "")),
        )
        session.add(sale)
        session.flush()
        session.add(SaleItem(
            id=str(uuid.uuid4()), sale_id=sale.id, inventory_item_id=item.inventory_item_id,
            description=item.description, quantity=item.quantity, unit_price=price,
            cost_basis=(inventory.allocated_cost_basis if inventory and inventory.allocated_cost_basis else Decimal("0")),
        ))
        session.expire(item)
        item.sale_id = sale.id
    else:
        claim = session.execute(
            update(WhatnotShowItem)
            .where(WhatnotShowItem.id == item.id, WhatnotShowItem.outcome == WhatnotShowItemOutcome.PENDING)
            .values(outcome=selected)
            .execution_options(synchronize_session=False)
        )
        if claim.rowcount != 1:
            session.rollback()
            return RedirectResponse(f"/whatnot/{show_id}", status_code=303)
        session.expire(item)
        if selected == WhatnotShowItemOutcome.UNSOLD:
            _release_inventory(session, item)
        elif selected == WhatnotShowItemOutcome.GIVEAWAY and item.inventory_item_id:
            inventory = session.get(InventoryItem, item.inventory_item_id)
            if inventory is not None and inventory.quantity == 0:
                inventory.status = InventoryStatus.GIVEAWAY

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="that Whatnot order ID is already recorded") from exc
    record_event(session, actor_type="user", actor_id=user, action="whatnot.item.settle",
                 resource_type="whatnot_show_item", resource_id=item.id, outcome="success",
                 correlation_id=show.id, details={"outcome": selected.value})
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


@router.post("/{show_id}/complete")
def complete_show(
    show_id: str, viewer_count: str = Form(""), follower_count: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    if show.status != WhatnotShowStatus.LIVE:
        raise HTTPException(status_code=409, detail="only a live show can be completed")
    pending = session.scalar(select(func.count(WhatnotShowItem.id)).where(
        WhatnotShowItem.whatnot_show_id == show_id,
        WhatnotShowItem.outcome == WhatnotShowItemOutcome.PENDING,
    ))
    if pending:
        raise HTTPException(status_code=409, detail=f"settle the remaining {pending} item(s) first")
    show.status = WhatnotShowStatus.COMPLETED
    show.viewer_count = _nonnegative_int(viewer_count, "viewer count") if viewer_count.strip() else None
    show.follower_count = _nonnegative_int(follower_count, "follower count") if follower_count.strip() else None
    show.completed_at = datetime.now(UTC)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="whatnot.show.complete",
                 resource_type="whatnot_show", resource_id=show.id, outcome="success",
                 correlation_id=show.id, details={"viewer_count": show.viewer_count})
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


@router.post("/{show_id}/cancel")
def cancel_show(
    show_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    show = session.get(WhatnotShow, show_id)
    if show is None:
        raise HTTPException(status_code=404, detail="show not found")
    claim = session.execute(
        update(WhatnotShow)
        .where(
            WhatnotShow.id == show_id,
            WhatnotShow.status.in_([WhatnotShowStatus.DRAFT, WhatnotShowStatus.LIVE]),
        )
        .values(status=WhatnotShowStatus.CANCELLED, cancelled_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="this show cannot be cancelled")
    pending = session.scalars(select(WhatnotShowItem).where(
        WhatnotShowItem.whatnot_show_id == show_id,
        WhatnotShowItem.outcome == WhatnotShowItemOutcome.PENDING,
    )).all()
    for item in pending:
        item.outcome = WhatnotShowItemOutcome.UNSOLD
        _release_inventory(session, item)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="whatnot.show.cancel",
                 resource_type="whatnot_show", resource_id=show.id, outcome="success",
                 correlation_id=show.id, details={"released_items": len(pending)})
    return RedirectResponse(f"/whatnot/{show_id}", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:620px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select,.inline-search input,.completion-form input{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button,.show-actions button,.inline-search button,.completion-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:10px 13px;border-radius:8px;color:#fff;font-weight:700;cursor:pointer}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;margin-top:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
.show-head,.show-actions,.inline-search,.completion-form,.outcome-buttons{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.show-head{justify-content:space-between}.show-actions form{margin:0}.show-actions button.danger{background:#8f3333}
.outcome-form{display:grid;grid-template-columns:repeat(2,minmax(105px,1fr));gap:6px;min-width:260px}
.outcome-form input{min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:6px;padding:6px;color:var(--text);font-size:12px}
.outcome-buttons{grid-column:1/-1}.outcome-btn{padding:6px 10px;border-radius:6px;border:1px solid var(--panel-border);background:none;color:var(--text-dim);font-size:11px;cursor:pointer}
.outcome-btn.sold{border-color:var(--accent);color:var(--accent)}
.notice.warn{border-color:#9b7028}.inline-search{margin-bottom:14px}.completion-form input{width:145px}
@media(max-width:720px){.outcome-form{grid-template-columns:1fr}.outcome-buttons{grid-column:1}.table-wrap{overflow-x:auto}}
</style>"""
