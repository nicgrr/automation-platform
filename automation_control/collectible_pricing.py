"""Native EasyBay screens for collectible research and inventory valuation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .commerce import active_fee_rule
from .collectible_pricing_service import (
    CategoryRoutingError, CollectiblePricingError, add_manual_comparable,
    calculate_valuation, observations, provider_rules, refresh_ebay,
    require_collectible, save_valuation, search_link,
)
from .config import get_settings
from .database import get_session
from .models import (
    CatalogItem, CatalogItemType, CollectibleProduct, InventoryItem,
    Marketplace, PricingObservation, PricingSourceKind, PricingValuation,
)
from .market_watch_service import exchange_rate_to_aud
from .ui import brand_header, page, pill


router = APIRouter(prefix="/collectibles/pricing", tags=["collectible-pricing"])


def _money(value: Decimal | None) -> str:
    return "Unavailable" if value is None else f"A${value:,.2f}"


def _original(row: PricingObservation) -> str:
    return f"{escape(row.original_currency)} {row.original_price:,.2f}"


def _status(row: PricingObservation) -> tuple[str, str]:
    if row.source_kind == PricingSourceKind.SOLD_LISTING:
        return "Verified sold", "ok"
    if row.listing_type == "accepted_offer_unknown":
        return "Accepted offer — final price unknown", "warn"
    return "Active asking price", "neutral"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get("", response_class=HTMLResponse)
def research_page(
    q: str = Query(""), user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    stmt = (
        select(CatalogItem, CollectibleProduct)
        .join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
        .where(CatalogItem.item_type == CatalogItemType.COLLECTIBLE)
        .order_by(CatalogItem.name)
    )
    if q.strip():
        stmt = stmt.where(CatalogItem.name.ilike(f"%{q.strip()}%"))
    rows = session.execute(stmt.limit(100)).all()
    result_rows = []
    for item, product in rows:
        inv = session.scalar(select(InventoryItem).where(InventoryItem.catalog_item_id == item.id))
        latest = session.scalar(
            select(PricingValuation).where(PricingValuation.catalog_item_id == item.id)
            .order_by(PricingValuation.valued_at.desc()).limit(1)
        )
        href = f"/collectibles/pricing/item/{escape(inv.id)}" if inv else f"/collectibles/pricing/product/{escape(item.id)}"
        result_rows.append(
            "<tr>"
            f"<td><a href='{href}'>{escape(item.name)}</a></td>"
            f"<td>{escape(product.brand or '—')}</td><td>{escape(product.series or '—')}</td>"
            f"<td>{_money(latest.market_value_aud) if latest else 'Not valued'}</td>"
            f"<td>{pill(latest.confidence_label, 'ok' if latest and latest.confidence_label == 'high' else 'neutral') if latest else '—'}</td>"
            f"<td>{'Owned' if inv else 'Research only'}</td></tr>"
        )
    body = (
        brand_header("Collectibles pricing")
        + "<form class='panel filters' method='get'><label>Search collectibles"
          f"<input name='q' value='{escape(q)}' placeholder='Sonny Angel, Smiski, series or variant'></label>"
          "<button>Search</button></form>"
        + "<div class='panel'><div class='table-wrap'><table><thead><tr><th>Item</th><th>Brand</th>"
          "<th>Series</th><th>Realistic resale</th><th>Confidence</th><th>Inventory</th></tr></thead><tbody>"
        + ("".join(result_rows) or "<tr><td colspan='6'>No matching collectibles.</td></tr>")
        + "</tbody></table></div></div>"
    )
    return HTMLResponse(page("EzBay — Collectibles pricing", body))


@router.get("/item/{inventory_id}", response_class=HTMLResponse)
def inventory_pricing_detail(
    inventory_id: str, user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    inventory = session.get(InventoryItem, inventory_id)
    if inventory is None:
        raise HTTPException(status_code=404, detail="inventory item not found")
    return _pricing_detail(session, inventory.catalog_item_id, inventory)


@router.get("/product/{catalog_item_id}", response_class=HTMLResponse)
def product_pricing_detail(
    catalog_item_id: str, user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    return _pricing_detail(session, catalog_item_id, None)


def _pricing_detail(session: Session, catalog_item_id: str, inventory: InventoryItem | None) -> HTMLResponse:
    try:
        item = require_collectible(session, catalog_item_id)
    except CategoryRoutingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    product = session.get(CollectibleProduct, item.id)
    rows = observations(session, item.id)
    value = calculate_valuation(rows)
    rules = provider_rules(session, item)
    history = list(session.scalars(
        select(PricingValuation).where(PricingValuation.catalog_item_id == item.id)
        .order_by(PricingValuation.valued_at.desc()).limit(20)
    ))
    now = datetime.now(UTC)

    comp_rows = []
    for row in rows[:100]:
        label, kind = _status(row)
        age = (now - _aware(row.observed_at)).days
        stale = age > 30
        link = f"<a href='{escape(row.item_url)}' target='_blank' rel='noopener'>open</a>" if row.item_url else "—"
        comp_rows.append(
            f"<tr><td>{escape(row.provider_key.title())}</td><td>{pill(label, kind)}</td>"
            f"<td>{escape(row.title or item.name)}</td><td>{_original(row)}</td>"
            f"<td>{_money(row.aud_price)}</td><td>{pill('stale', 'warn') if stale else escape(_aware(row.observed_at).strftime('%d %b %Y'))}</td>"
            f"<td>{link}</td></tr>"
        )

    provider_cards = []
    for rule in rules:
        mode = "Automatic" if rule.access_mode == "automatic" else "Manual / search link"
        refresh = (
            f"<form method='post' action='/collectibles/pricing/product/{escape(item.id)}/refresh-ebay'>"
            "<button>Refresh eBay</button></form>"
            if rule.provider_key == "ebay" and rule.access_mode == "automatic" else ""
        )
        provider_cards.append(
            "<div class='panel'><h3>" + escape(rule.provider_key.title()) + "</h3>"
            f"<p>{escape(mode)} · <a href='{escape(search_link(rule.provider_key, item.name))}' target='_blank' rel='noopener'>search marketplace</a></p>"
            f"<p class='muted'>{escape(rule.limitation or '')}</p>{refresh}</div>"
        )

    cost = inventory.allocated_cost_basis if inventory else None
    purchase = inventory.purchase_cost_basis if inventory else None
    shipping = inventory.allocated_shipping_cost if inventory else None
    ebay_marketplace = session.scalar(select(Marketplace).where(func.lower(Marketplace.name) == "ebay"))
    fee_rule = active_fee_rule(session, ebay_marketplace.id, "Collectibles") if ebay_marketplace else None
    expected_fees = break_even = profit = None
    if fee_rule and value.realistic_resale is not None:
        fee_rate = (fee_rule.commission_pct + fee_rule.processing_pct) / Decimal("100")
        expected_fees = value.realistic_resale * fee_rate + fee_rule.fixed_fee
        if cost is not None:
            profit = value.realistic_resale - expected_fees - cost
            if fee_rate < 1:
                break_even = (cost + fee_rule.fixed_fee) / (Decimal("1") - fee_rate)
    img = ""
    if inventory and inventory.scan_image_path:
        img = f"<img src='/collectibles/thumb/{escape(inventory.id)}' alt='' style='max-width:220px;border-radius:12px'>"
    series = " · ".join(v for v in (product.brand, product.series, product.variant) if v) if product else ""
    summary = (
        "<div class='panel'><div style='display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap'>" + img
        + "<div><h2>" + escape(item.name) + "</h2><p>" + escape(series) + "</p>"
        + (pill("Secret / chase", "warn") if product and product.is_secret else "")
        + "</div></div><div class='calc-grid'>"
        + f"<div><div class='label'>Purchase price</div><div class='value'>{_money(purchase)}</div></div>"
        + f"<div><div class='label'>Allocated shipping</div><div class='value'>{_money(shipping)}</div></div>"
        + f"<div><div class='label'>Landed cost</div><div class='value'>{_money(cost)}</div></div>"
        + f"<div><div class='label'>Lowest relevant ask</div><div class='value'>{_money(value.lowest_ask)}</div></div>"
        + f"<div><div class='label'>Median asking</div><div class='value'>{_money(value.median_ask)}</div></div>"
        + f"<div><div class='label'>Verified sold range</div><div class='value'>{_money(value.sold_low)} – {_money(value.sold_high)}</div></div>"
        + f"<div><div class='label'>Realistic resale</div><div class='value'>{_money(value.realistic_resale)}</div></div>"
        + f"<div><div class='label'>Recommended listing</div><div class='value'>{_money(value.recommended_listing)}</div></div>"
        + f"<div><div class='label'>Quick sale</div><div class='value'>{_money(value.quick_sale)}</div></div>"
        + f"<div><div class='label'>Expected eBay fees</div><div class='value'>{_money(expected_fees)}</div></div>"
        + f"<div><div class='label'>Break-even price</div><div class='value'>{_money(break_even)}</div></div>"
        + f"<div><div class='label'>Potential profit after fees</div><div class='value'>{_money(profit)}</div></div>"
        + f"<div><div class='label'>Pricing confidence</div><div class='value'>{escape(value.confidence_label.title())} ({value.confidence_score}%)</div></div>"
        + f"<div><div class='label'>Last refresh</div><div class='value'>{escape(_aware(value.last_refresh).strftime('%d %b %Y %H:%M')) if value.last_refresh else 'Never'}</div></div>"
        + "</div><p class='muted'>Fee-dependent figures remain unavailable until an applicable eBay marketplace fee rule is configured. "
          "Accepted offers with unknown final prices never enter the sold-price range.</p></div>"
    )

    manual_form = (
        "<div class='panel'><h2>Save comparable listing</h2>"
        f"<form method='post' action='/collectibles/pricing/product/{escape(item.id)}/comparables' class='calc-form'>"
        "<label>Marketplace<select name='provider_key'><option value='ebay'>eBay</option><option value='depop'>Depop</option><option value='vinted'>Vinted</option></select></label>"
        "<label>Status<select name='status'><option value='active'>Active asking price</option><option value='sold_verified'>Verified sold price</option><option value='accepted_unknown'>Accepted offer — final price unknown</option></select></label>"
        "<label>Listing title<input name='title' required></label><label>Listing URL<input name='url' type='url' required></label>"
        "<label>Observed price<input name='price' type='number' min='0.01' step='0.01' required></label>"
        "<label>Currency<input name='currency' value='AUD' maxlength='3' required></label>"
        "<button>Save comparable</button></form></div>"
    )
    history_rows = "".join(
        f"<tr><td>{escape(_aware(row.valued_at).strftime('%d %b %Y %H:%M'))}</td>"
        f"<td>{_money(row.market_value_aud)}</td><td>{_money(row.active_median_aud)}</td>"
        f"<td>{_money(row.sold_median_aud)}</td><td>{escape(row.confidence_label.title())}</td></tr>"
        for row in history
    ) or "<tr><td colspan='5'>No valuation snapshots yet.</td></tr>"
    history_table = (
        "<div class='panel'><h2>Valuation history</h2><div class='table-wrap'><table><thead><tr>"
        "<th>Valued</th><th>Realistic resale</th><th>Median ask</th><th>Sold median</th><th>Confidence</th>"
        f"</tr></thead><tbody>{history_rows}</tbody></table></div></div>"
    )
    inventory_action = ""
    if inventory is None:
        inventory_action = (
            f"<div class='panel'><h2>Add to inventory</h2><form method='post' action='/collectibles/pricing/product/{escape(item.id)}/inventory' class='calc-form'>"
            "<label>Quantity<input name='quantity' type='number' min='1' value='1' required></label>"
            "<label>Condition<input name='condition' value='New' required></label>"
            "<button>Add researched product to inventory</button></form></div>"
        )
    alerts_link = "<p><a class='btn ghost' href='/market-watch'>Configure marketplace alerts and refresh watches</a></p>"
    table = (
        "<div class='panel'><h2>Marketplace comparisons</h2><div class='table-wrap'><table><thead><tr>"
        "<th>Provider</th><th>Evidence</th><th>Listing</th><th>Original</th><th>AUD</th><th>Observed</th><th></th>"
        "</tr></thead><tbody>" + ("".join(comp_rows) or "<tr><td colspan='7'>No comparisons saved yet.</td></tr>")
        + "</tbody></table></div></div>"
    )
    body = brand_header("Collectible pricing") + summary + alerts_link + "".join(provider_cards) + table + history_table + manual_form + inventory_action
    return HTMLResponse(page(f"EzBay — {item.name}", body))


@router.post("/product/{catalog_item_id}/comparables")
async def save_comparable(
    catalog_item_id: str, provider_key: str = Form(...), status: str = Form(...),
    title: str = Form(...), url: str = Form(...), price: str = Form(...), currency: str = Form("AUD"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        original = Decimal(price.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="price must be a valid amount") from exc
    try:
        code = currency.strip().upper()
        rate = await exchange_rate_to_aud(code)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="currency conversion is temporarily unavailable") from exc
    try:
        converted = original * rate
        row = add_manual_comparable(
            session, catalog_item_id=catalog_item_id, provider_key=provider_key,
            status=status, title=title, url=url, price=price, currency=code,
            aud_price=converted,
        )
        value = calculate_valuation(observations(session, catalog_item_id))
        save_valuation(session, require_collectible(session, catalog_item_id), value)
        session.commit()
    except CollectiblePricingError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_event(
        session, actor_type="user", actor_id=user, action="collectible.comparable.add",
        resource_type="pricing_observation", resource_id=row.id, outcome="success",
        details={"provider": provider_key, "status": status},
    )
    return RedirectResponse(f"/collectibles/pricing/product/{catalog_item_id}", status_code=303)


@router.post("/product/{catalog_item_id}/inventory")
def add_researched_product_to_inventory(
    catalog_item_id: str, quantity: int = Form(1), condition: str = Form("New"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        item = require_collectible(session, catalog_item_id)
    except CollectiblePricingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if quantity < 1 or quantity > 9999:
        raise HTTPException(status_code=400, detail="quantity must be between 1 and 9999")
    clean_condition = condition.strip()[:64]
    if not clean_condition:
        raise HTTPException(status_code=400, detail="condition is required")
    inventory = session.scalar(select(InventoryItem).where(
        InventoryItem.catalog_item_id == item.id,
        InventoryItem.condition == clean_condition,
    ))
    if inventory is None:
        from .models import CardVariant
        inventory = InventoryItem(
            catalog_item_id=item.id, card_id=None, variant=CardVariant.NORMAL,
            condition=clean_condition, quantity=quantity,
        )
        session.add(inventory)
    else:
        inventory.quantity += quantity
    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="collectible.inventory.add",
        resource_type="inventory_item", resource_id=inventory.id, outcome="success",
        details={"catalog_item_id": item.id, "quantity": quantity},
    )
    return RedirectResponse(f"/collectibles/pricing/item/{inventory.id}", status_code=303)


@router.post("/product/{catalog_item_id}/refresh-ebay")
async def refresh_collectible_ebay(
    catalog_item_id: str, user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    try:
        count = await refresh_ebay(session, get_settings(), catalog_item_id, force=True)
        value = calculate_valuation(observations(session, catalog_item_id))
        save_valuation(session, require_collectible(session, catalog_item_id), value)
        session.commit()
    except CollectiblePricingError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    record_event(
        session, actor_type="user", actor_id=user, action="collectible.pricing.refresh",
        resource_type="catalog_item", resource_id=catalog_item_id, outcome="success",
        details={"provider": "ebay", "observations": count},
    )
    return RedirectResponse(f"/collectibles/pricing/product/{catalog_item_id}", status_code=303)
