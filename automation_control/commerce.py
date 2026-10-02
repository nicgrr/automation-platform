"""Marketplace fee rules, sales, and the customer CRM -- Modules 7-9.

Fee rules are time-boxed rows (see MarketplaceFeeRule's own docstring) so a
"0% commission weekend" is a new row with its own effective dates, never a
code change. A sale's fee amount is still entered explicitly at record
time rather than solely computed from the active rule, because the sale
record should keep saying what was actually true even after a rule is
later superseded -- the active rule is offered as a prefill/reference, not
forced.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    CatalogItem, Customer, EbayListingLink, EbayListingLinkStatus, InventoryItem,
    InventoryStatus, Marketplace, MarketplaceFeeRule, Sale, SaleItem, SaleStatus,
    ShopListing, ShopListingStatus, StorageLocation,
)
from .ui import brand_header, page, pill

router = APIRouter(tags=["commerce"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


def _money(value: str, label: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(value.strip() or "0")
    except (InvalidOperation, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a valid amount") from exc
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        qualifier = "greater than zero" if positive else "zero or more"
        raise HTTPException(status_code=400, detail=f"{label} must be {qualifier}")
    return number.quantize(Decimal("0.01"))


def _positive_int(value: str, label: str = "quantity") -> int:
    try:
        number = int(value.strip())
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a whole number") from exc
    if number < 1:
        raise HTTPException(status_code=400, detail=f"{label} must be at least one")
    return number


def active_fee_rule(session: Session, marketplace_id: str, category: str | None = None, at: datetime | None = None) -> MarketplaceFeeRule | None:
    """The fee rule in effect for a sale -- narrowest category match first,
    then whichever row's [effective_from, effective_to) window contains
    `at`. A category-specific rule always wins over a category=None
    catch-all, even if the catch-all is more recently added.

    The effective_to/effective_from comparison is done in the SQL WHERE
    clause rather than in Python -- SQLite has no native timezone-aware
    datetime type, so a DateTime(timezone=True) column can round-trip back
    as a naive datetime, and comparing that in Python against an
    aware `datetime.now(UTC)` raises TypeError (caught by
    test_an_expired_promotion_is_not_active). Comparing inside the query
    lets the DB driver's own bind-parameter handling normalize both sides
    instead.
    """
    at = at or datetime.now(UTC)
    candidates = session.scalars(
        select(MarketplaceFeeRule).where(
            MarketplaceFeeRule.marketplace_id == marketplace_id,
            MarketplaceFeeRule.effective_from <= at,
            or_(MarketplaceFeeRule.effective_to.is_(None), MarketplaceFeeRule.effective_to > at),
        )
    ).all()
    category_match = [r for r in candidates if r.category == category] if category else []
    pool = category_match or [r for r in candidates if r.category is None]
    return max(pool, key=lambda r: r.effective_from) if pool else None


# --- Module 7: marketplaces & fee rules -------------------------------------

@router.get("/marketplaces", response_class=HTMLResponse)
def marketplaces_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    marketplaces = session.scalars(select(Marketplace).order_by(Marketplace.name)).all()
    rule_rows = []
    for m in marketplaces:
        rules = session.scalars(select(MarketplaceFeeRule).where(MarketplaceFeeRule.marketplace_id == m.id).order_by(MarketplaceFeeRule.effective_from.desc())).all()
        for r in rules:
            active = active_fee_rule(session, m.id, r.category)
            is_active = active is not None and active.id == r.id
            window = f"{r.effective_from:%Y-%m-%d} → {r.effective_to:%Y-%m-%d}" if r.effective_to else f"{r.effective_from:%Y-%m-%d} → ongoing"
            rule_rows.append(
                "<tr>"
                f"<td>{escape(m.name)}</td>"
                f"<td>{escape(r.category or 'all categories')}{' — ' + escape(r.promotion_label) if r.promotion_label else ''}</td>"
                f"<td>{r.commission_pct}% + {r.processing_pct}% + ${r.fixed_fee}</td>"
                f"<td>{escape(window)}</td>"
                f"<td>{pill('active', 'ok') if is_active else pill('inactive', 'neutral')}</td>"
                "</tr>"
            )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Marketplace</th><th>Category / promo</th><th>Fees</th><th>Window</th><th></th></tr></thead>"
        f"<tbody>{''.join(rule_rows) or '<tr><td colspan=5>No fee rules yet.</td></tr>'}</tbody></table></div></div>"
    )
    marketplace_options = "".join(f"<option value='{escape(m.id)}'>{escape(m.name)}</option>" for m in marketplaces)
    form = (
        "<div class='panel'><h2>Add a fee rule</h2>"
        "<form method='post' action='/marketplaces/fee-rules' class='calc-form'>"
        f"<label>Marketplace<select name='marketplace_id'>{marketplace_options}</select></label>"
        "<label>Category (blank = all)<input name='category'></label>"
        "<label>Commission %<input name='commission_pct' type='number' step='0.01' value='0'></label>"
        "<label>Payment processing %<input name='processing_pct' type='number' step='0.01' value='0'></label>"
        "<label>Fixed fee<input name='fixed_fee' type='number' step='0.01' value='0'></label>"
        "<label>Promotion label (optional)<input name='promotion_label' placeholder='0% TCG weekend'></label>"
        "<label>Effective to (optional)<input name='effective_to' type='date'></label>"
        "<button type='submit'>Add rule</button>"
        "</form></div>"
    )
    body = brand_header("Marketplaces & fees") + table + form + _STYLE
    return HTMLResponse(page("EzBay — Marketplaces", body))


@router.post("/marketplaces/fee-rules")
def create_fee_rule(
    marketplace_id: str = Form(...), category: str = Form(""), commission_pct: str = Form("0"),
    processing_pct: str = Form("0"), fixed_fee: str = Form("0"), promotion_label: str = Form(""),
    effective_to: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    if session.get(Marketplace, marketplace_id) is None:
        raise HTTPException(status_code=404, detail="marketplace not found")
    session.add(MarketplaceFeeRule(
        id=str(uuid.uuid4()), marketplace_id=marketplace_id, category=category or None,
        commission_pct=_dec(commission_pct), processing_pct=_dec(processing_pct), fixed_fee=_dec(fixed_fee),
        promotion_label=promotion_label or None,
        effective_to=datetime.fromisoformat(effective_to).replace(tzinfo=UTC) if effective_to else None,
    ))
    session.commit()
    return RedirectResponse("/marketplaces", status_code=303)


# --- Module 8: sales ---------------------------------------------------------

SYSTEM_MANAGED_CHANNELS = {"whatnot", "ebay", "website"}


def _direct_inventory(session: Session) -> list[tuple[InventoryItem, CatalogItem, StorageLocation | None]]:
    listing_exists = exists(select(ShopListing.id).where(
        ShopListing.inventory_item_id == InventoryItem.id,
        ShopListing.quantity > 0,
        ShopListing.status != ShopListingStatus.WITHDRAWN,
    ))
    ebay_exists = exists(
        select(EbayListingLink.id)
        .join(ShopListing, ShopListing.id == EbayListingLink.shop_listing_id)
        .where(
            ShopListing.inventory_item_id == InventoryItem.id,
            EbayListingLink.status.in_([
                EbayListingLinkStatus.PUBLISHING, EbayListingLinkStatus.ACTIVE,
                EbayListingLinkStatus.ENDING,
            ]),
        )
    )
    return session.execute(
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
        .limit(500)
    ).all()


def _direct_sale_blocker(session: Session, inventory_id: str) -> str | None:
    listing = session.scalar(select(ShopListing.id).where(
        ShopListing.inventory_item_id == inventory_id,
        ShopListing.quantity > 0,
        ShopListing.status != ShopListingStatus.WITHDRAWN,
    ).limit(1))
    if listing is not None:
        return "This holding is allocated to NL Vault/eBay. Withdraw it before recording a direct sale."
    return None


def _customer_for_sale(
    session: Session, name: str, email: str = "", phone: str = "",
) -> Customer | None:
    clean_name = name.strip()[:256]
    clean_email = email.strip().lower()[:256]
    clean_phone = phone.strip()[:64]
    if not clean_name and not clean_email:
        return None
    customer = None
    if clean_email:
        customer = session.scalar(select(Customer).where(func.lower(Customer.email) == clean_email))
    if customer is None and clean_name:
        customer = session.scalar(select(Customer).where(func.lower(Customer.display_name) == clean_name.lower()))
    if customer is None:
        customer = Customer(
            id=str(uuid.uuid4()), display_name=clean_name or clean_email,
            email=clean_email or None, phone=clean_phone or None,
        )
        session.add(customer)
        session.flush()
    else:
        if clean_email and not customer.email:
            customer.email = clean_email
        if clean_phone and not customer.phone:
            customer.phone = clean_phone
    return customer

@router.get("/sales", response_class=HTMLResponse)
def sales_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    sales = session.scalars(select(Sale).order_by(Sale.sold_at.desc()).limit(100)).all()
    marketplaces = {m.id: m.name for m in session.scalars(select(Marketplace)).all()}
    customers = {c.id: c.display_name for c in session.scalars(select(Customer)).all()}

    rows = []
    total_profit = Decimal("0")
    for s in sales:
        items = session.scalars(select(SaleItem).where(SaleItem.sale_id == s.id)).all()
        cost_basis_total = sum((i.cost_basis or Decimal("0")) * i.quantity for i in items)
        net_profit = s.gross_amount + s.shipping_revenue - s.fees_amount - s.shipping_cost - s.packaging_cost - s.tax_amount - cost_basis_total
        if s.status == SaleStatus.COMPLETED:
            total_profit += net_profit
        item_desc = "; ".join(i.description for i in items) or "—"
        rows.append(
            "<tr>"
            f"<td>{s.sold_at:%Y-%m-%d}<br>{pill(s.status.value, 'ok' if s.status == SaleStatus.COMPLETED else 'neutral')}</td>"
            f"<td>{escape(item_desc)}</td>"
            f"<td>{escape(marketplaces.get(s.marketplace_id, '—'))}</td>"
            f"<td>{escape(customers.get(s.customer_id, '—'))}</td>"
            f"<td>${s.gross_amount:,.2f}</td>"
            f"<td>${net_profit:,.2f}</td>"
            f"<td>{('<form method=\'post\' action=\'/sales/' + escape(s.id) + '/void\' onsubmit=\"return confirm(\'Void this direct sale and restore its stock?\')\"><button class=\'text-button\'>Void</button></form>') if s.source == 'direct' and s.status == SaleStatus.COMPLETED else '—'}</td>"
            "</tr>"
        )
    table = (
        f"<div class='panel'><div class='calc-grid' style='margin-bottom:16px'>"
        f"<div><div class='label'>Sales shown</div><div class='value'>{len(sales)}</div></div>"
        f"<div><div class='label'>Total net profit</div><div class='value'>${total_profit:,.2f}</div></div>"
        f"</div><div class='table-wrap'><table>"
        f"<thead><tr><th>Date</th><th>Item(s)</th><th>Channel</th><th>Customer</th><th>Gross</th><th>Net profit</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=7>No sales recorded yet.</td></tr>'}</tbody></table></div></div>"
    )

    marketplaces_for_direct = [
        m for m in session.scalars(select(Marketplace).order_by(Marketplace.name)).all()
        if m.name.lower() not in SYSTEM_MANAGED_CHANNELS
    ]
    marketplace_options = "".join(
        f"<option value='{escape(m.id)}'>{escape(m.name)}</option>" for m in marketplaces_for_direct
    )
    inventory_options = "".join(
        f"<option value='{escape(inventory.id)}'>{escape(catalog.name)} · {escape(inventory.condition)} "
        f"· qty {inventory.quantity} · {escape(location.name if location else 'not located')}</option>"
        for inventory, catalog, location in _direct_inventory(session)
    )
    form = (
        "<div class='panel'><h2>Record a direct sale</h2>"
        "<p class='muted'>Choose owned stock. Whatnot, eBay and NL Vault sales are recorded by their own workflows.</p>"
        "<form method='post' action='/sales' class='calc-form'>"
        f"<input type='hidden' name='submission_key' value='{uuid.uuid4()}'>"
        f"<label>Channel<select name='marketplace_id' required>{marketplace_options}</select></label>"
        f"<label>Owned inventory<select name='inventory_item_id' required>{inventory_options}</select></label>"
        "<label>Customer name (optional)<input name='customer_name'></label>"
        "<label>Customer email (optional)<input name='customer_email' type='email'></label>"
        "<label>Customer phone (optional)<input name='customer_phone'></label>"
        "<label>Quantity<input name='quantity' type='number' value='1' min='1'></label>"
        "<label>Sale price (each)<input name='unit_price' type='number' step='0.01' required></label>"
        "<label>Fees<input name='fees_amount' type='number' step='0.01' value='0'></label>"
        "<label>Shipping revenue<input name='shipping_revenue' type='number' step='0.01' value='0'></label>"
        "<label>Shipping cost<input name='shipping_cost' type='number' step='0.01' value='0'></label>"
        "<label>Packaging cost<input name='packaging_cost' type='number' step='0.01' value='0'></label>"
        "<label>Tax collected/remitted<input name='tax_amount' type='number' step='0.01' value='0'></label>"
        "<label>Notes<input name='notes'></label>"
        "<button type='submit'>Record sale</button>"
        "</form>" + ("</div>" if inventory_options else "<div class='notice warn'>No unallocated inventory is available for a direct sale.</div></div>")
    )
    body = brand_header("Sales") + table + form + _STYLE
    return HTMLResponse(page("EzBay — Sales", body))


@router.post("/sales")
def create_sale(
    marketplace_id: str = Form(...), inventory_item_id: str = Form(...),
    customer_name: str = Form(""), customer_email: str = Form(""), customer_phone: str = Form(""),
    quantity: str = Form("1"), unit_price: str = Form(...),
    fees_amount: str = Form("0"), shipping_revenue: str = Form(""), shipping_cost: str = Form(""),
    packaging_cost: str = Form(""), tax_amount: str = Form(""), notes: str = Form(""),
    submission_key: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    key = submission_key.strip()[:36] or None
    if key:
        existing = session.scalar(select(Sale).where(Sale.submission_key == key))
        if existing is not None:
            return RedirectResponse("/sales", status_code=303)
    marketplace = session.get(Marketplace, marketplace_id)
    if marketplace is None:
        raise HTTPException(status_code=404, detail="sale channel not found")
    if marketplace.name.lower() in SYSTEM_MANAGED_CHANNELS:
        raise HTTPException(status_code=409, detail="use that channel's own order workflow")
    qty = _positive_int(quantity)
    price = _money(unit_price, "sale price", positive=True)
    fees = _money(fees_amount, "fees")
    shipping_in = _money(shipping_revenue, "shipping revenue")
    shipping_out = _money(shipping_cost, "shipping cost")
    packaging = _money(packaging_cost, "packaging cost")
    tax = _money(tax_amount, "tax")
    inventory = session.get(InventoryItem, inventory_item_id)
    if inventory is None:
        raise HTTPException(status_code=404, detail="inventory item not found")
    claim = session.execute(
        update(InventoryItem)
        .where(
            InventoryItem.id == inventory_item_id,
            InventoryItem.status == InventoryStatus.AVAILABLE,
            InventoryItem.quantity >= qty,
        )
        .values(quantity=InventoryItem.quantity - qty)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        raise HTTPException(status_code=409, detail="that inventory is no longer available in the requested quantity")
    blocker = _direct_sale_blocker(session, inventory_item_id)
    if blocker:
        session.rollback()
        raise HTTPException(status_code=409, detail=blocker)
    session.expire(inventory)
    catalog = session.get(CatalogItem, inventory.catalog_item_id)
    if catalog is None:
        session.rollback()
        raise HTTPException(status_code=409, detail="inventory has no canonical catalogue item")
    if inventory.quantity == 0:
        inventory.status = InventoryStatus.SOLD
    customer = _customer_for_sale(session, customer_name, customer_email, customer_phone)

    sale = Sale(
        id=str(uuid.uuid4()), submission_key=key, source="direct",
        marketplace_id=marketplace.id, customer_id=customer.id if customer else None,
        gross_amount=price * qty, fees_amount=fees, shipping_revenue=shipping_in,
        shipping_cost=shipping_out, packaging_cost=packaging, tax_amount=tax,
        notes=notes.strip() or None,
    )
    session.add(sale)
    session.flush()
    session.add(SaleItem(
        id=str(uuid.uuid4()), sale_id=sale.id, inventory_item_id=inventory.id,
        description=catalog.name, quantity=qty, unit_price=price,
        cost_basis=inventory.allocated_cost_basis or Decimal("0"),
    ))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if key and session.scalar(select(Sale.id).where(Sale.submission_key == key)):
            return RedirectResponse("/sales", status_code=303)
        raise HTTPException(status_code=409, detail="this sale could not be recorded uniquely") from exc
    record_event(
        session, actor_type="user", actor_id=user, action="sale.direct.record",
        resource_type="sale", resource_id=sale.id, outcome="success",
        correlation_id=sale.id,
        details={"inventory_item_id": inventory.id, "quantity": qty, "marketplace_id": marketplace.id},
    )
    return RedirectResponse("/sales", status_code=303)


@router.post("/sales/{sale_id}/void")
def void_direct_sale(
    sale_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    sale = session.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="sale not found")
    if sale.source != "direct" or sale.status != SaleStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="only a completed direct sale can be voided here")
    items = session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).all()
    inventories = []
    for item in items:
        inventory = session.get(InventoryItem, item.inventory_item_id) if item.inventory_item_id else None
        if inventory is None:
            raise HTTPException(status_code=409, detail="the sale's inventory link is missing")
        if inventory.status not in (InventoryStatus.AVAILABLE, InventoryStatus.SOLD):
            raise HTTPException(status_code=409, detail="resolve the inventory's current allocation before voiding")
        inventories.append((item, inventory))
    claim = session.execute(
        update(Sale)
        .where(Sale.id == sale.id, Sale.status == SaleStatus.COMPLETED, Sale.source == "direct")
        .values(status=SaleStatus.VOIDED, voided_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="this sale was already changed")
    for item, inventory in inventories:
        inventory.quantity += item.quantity
        if inventory.status == InventoryStatus.SOLD:
            inventory.status = InventoryStatus.AVAILABLE
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="sale.direct.void",
        resource_type="sale", resource_id=sale.id, outcome="success",
        correlation_id=sale.id, details={"restored_items": len(items)},
    )
    return RedirectResponse("/sales", status_code=303)


# --- Module 9: customer CRM --------------------------------------------------

@router.get("/customers", response_class=HTMLResponse)
def customers_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    customers = session.scalars(select(Customer).order_by(Customer.display_name)).all()
    rows = []
    for c in customers:
        stats = session.execute(
            select(func.count(Sale.id), func.coalesce(func.sum(Sale.gross_amount), 0), func.max(Sale.sold_at))
            .where(Sale.customer_id == c.id, Sale.status == SaleStatus.COMPLETED)
        ).first()
        order_count, lifetime_value, last_purchase = stats
        rows.append(
            "<tr>"
            f"<td>{escape(c.display_name)}</td>"
            f"<td>{escape(c.email or '—')}<br><span class='muted'>{escape(c.phone or '')}</span></td>"
            f"<td>{pill(c.segment, 'neutral') if c.segment else '—'}</td>"
            f"<td>{order_count}</td>"
            f"<td>${lifetime_value:,.2f}</td>"
            f"<td>{last_purchase.strftime('%Y-%m-%d') if last_purchase else '—'}</td>"
            "</tr>"
        )
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Customer</th><th>Contact</th><th>Segment</th><th>Orders</th><th>Lifetime value</th><th>Last purchase</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=6>No customers yet -- they appear automatically when you record a sale.</td></tr>'}</tbody></table></div></div>"
    )
    form = (
        "<div class='panel'><h2>Add a customer</h2>"
        "<form method='post' action='/customers' class='calc-form'>"
        "<label>Name<input name='display_name' required></label>"
        "<label>Email<input name='email' type='email'></label>"
        "<label>Phone<input name='phone'></label>"
        "<label>Segment<input name='segment' placeholder='VIP, One Piece, ...'></label>"
        "<label>Notes<input name='notes'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = brand_header("Customers") + table + form + _STYLE
    return HTMLResponse(page("EzBay — Customers", body))


@router.post("/customers")
def create_customer(
    display_name: str = Form(...), email: str = Form(""), phone: str = Form(""),
    segment: str = Form(""), notes: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    clean_name = display_name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="customer name is required")
    customer = _customer_for_sale(session, clean_name, email, phone)
    if segment.strip():
        customer.segment = segment.strip()[:64]
    if notes.strip():
        customer.notes = notes.strip()
    session.commit()
    return RedirectResponse("/customers", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input,.calc-form select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#fff;font-weight:700;cursor:pointer;margin-top:4px}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:20px;font-weight:650}
</style>"""
