"""Retailer Goals UI -- Gameology Guild first, retailer-agnostic underneath."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .models import (
    Retailer,
    RetailerBudget,
    RetailerFundEntry,
    RetailerFundEntryKind,
    RetailerMilestone,
    RetailerNotificationPreference,
    RetailerPremiumConfig,
    RetailerPriceSnapshot,
    RetailerProduct,
    RetailerProductStatus,
    RetailerProgram,
    RetailerPurchase,
    RetailerPurchaseStatus,
    RetailerStockStatus,
    RetailerTrustStatus,
)
from .retailer_goals_service import (
    GAMEOLOGY_PROGRAM_SLUG,
    ZERO,
    accrue_weekly_fund,
    ensure_catalog_product,
    ensure_gameology_program,
    fund_totals,
    money,
    next_weekday,
    positive_int,
    program_state,
    record_purchase,
    refund_purchase,
    update_milestones,
)
from .ui import brand_header, page, pill

router = APIRouter(prefix="/retailer-goals", tags=["retailer-goals"])

TCG_OPTIONS = {"pokemon": "Pokémon", "one_piece": "One Piece", "other": "Other"}
PRODUCT_TYPES = {
    "booster_box": "Booster Box", "booster_bundle": "Booster Bundle", "etb": "ETB",
    "starter_deck": "Starter Deck", "special_collection": "Special Collection",
    "upc": "UPC", "tin": "Tin", "other": "Other",
}


def _program(session: Session, slug: str = GAMEOLOGY_PROGRAM_SLUG) -> RetailerProgram:
    row = session.scalar(select(RetailerProgram).where(RetailerProgram.slug == slug))
    if row is None and slug == GAMEOLOGY_PROGRAM_SLUG:
        row = ensure_gameology_program(session)
    if row is None:
        raise HTTPException(status_code=404, detail="retailer program not found")
    return row


def _date(value: str, *, default: date | None = None) -> date | None:
    if not value.strip():
        return default
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Enter dates as YYYY-MM-DD.") from exc


def _url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Product links must start with http:// or https://.")
    return value[:1024]


def _optional_money(value: str) -> Decimal | None:
    return money(value, allow_negative=False) if value.strip() else None


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _options(options: dict[str, str], selected: str) -> str:
    return "".join(
        f"<option value='{escape(value)}'{' selected' if value == selected else ''}>{escape(label)}</option>"
        for value, label in options.items()
    )


def _enum_options(enum_type, selected: str) -> str:
    return "".join(
        f"<option value='{item.value}'{' selected' if item.value == selected else ''}>"
        f"{escape(item.value.replace('_', ' ').title())}</option>"
        for item in enum_type
    )


def _purchase_dialog(product: RetailerProduct, gameology_price: Decimal | None) -> str:
    unit = str(gameology_price or "")
    qty = max(1, product.quantity_wanted)
    eligible = str(money((gameology_price or ZERO) * qty)) if gameology_price is not None else ""
    return (
        f"<dialog class='purchase-dialog' id='purchase-{escape(product.id)}'>"
        f"<form method='post' action='/retailer-goals/products/{escape(product.id)}/purchase' class='rg-form'>"
        f"<input type='hidden' name='submission_key' value='{uuid.uuid4()}'>"
        f"<div class='dialog-head'><h2>Record purchase</h2>"
        f"<button type='button' class='icon-button' onclick=\"this.closest('dialog').close()\" aria-label='Close'>&times;</button></div>"
        f"<p class='subtitle'>{escape(product.name)}</p>"
        f"<label>Purchase price each (AUD)<input name='purchase_price' type='number' min='0' step='0.01' value='{escape(unit)}' required data-unit-price></label>"
        f"<label>Quantity<input name='quantity' type='number' min='1' max='10000' value='{qty}' required data-purchase-qty></label>"
        f"<label>Purchase date<input name='purchase_date' type='date' value='{date.today().isoformat()}' required></label>"
        f"<label>Eligible Guild spend<input name='eligible_spend' type='number' min='0' step='0.01' value='{escape(eligible)}' required data-eligible-spend>"
        "<small>Defaults to price × quantity. Shipping is excluded; correct this only if Gameology applies different eligibility.</small></label>"
        "<label>Shipping (not added to Guild progress)<input name='shipping' type='number' min='0' step='0.01' value='0'></label>"
        "<label>Order number (optional)<input name='order_number' maxlength='256'></label>"
        "<label>Notes<textarea name='notes' rows='3'></textarea></label>"
        "<p class='fine-print'>Confirming creates the retailer purchase, purchase-lot line and sealed inventory together. Shipping is included in inventory cost basis.</p>"
        "<div class='dialog-actions'><button type='button' class='secondary' onclick=\"this.closest('dialog').close()\">Cancel</button>"
        "<button type='submit'>Confirm purchase</button></div></form></dialog>"
    )


def _milestone_cards(state: dict) -> str:
    cards = []
    for milestone in state["milestones"]:
        pct = min(100, float(state["spend"] / milestone.target_amount * 100)) if milestone.target_amount else 0
        projection = state_projection = None
        if milestone.achieved_at is None:
            from .retailer_goals_service import projection
            state_projection = projection(milestone.target_amount, state["spend"], state["budget"].weekly_contribution)
        remaining = max(ZERO, milestone.target_amount - state["spend"])
        completion = (
            f"~{state_projection['weeks']} weeks &middot; {state_projection['date']:%d %b %Y}"
            if state_projection and state_projection["weeks"] is not None else "Paused / no weekly contribution"
        )
        achieved = (
            f"{pill('✓ ACHIEVED', 'ok')}<div class='milestone-date'>Reached {milestone.achieved_at:%d %B %Y}</div>"
            if milestone.achieved_at else f"<div class='milestone-date'>Projected {completion}</div>"
        )
        cards.append(
            "<div class='milestone-card'>"
            f"<div class='milestone-head'><h3>{escape(milestone.label)}</h3><strong>{_money(milestone.target_amount)}</strong></div>"
            f"<div class='rg-progress'><span style='width:{pct:.1f}%'></span></div>"
            f"<div class='milestone-values'><strong>{_money(state['spend'])} / {_money(milestone.target_amount)}</strong>"
            f"<span>{_money(remaining)} remaining</span></div>{achieved}</div>"
        )
    return "".join(cards)


def _recommendation(state: dict) -> str:
    row = state["recommendation"]
    if row is None:
        return (
            "<div class='panel recommendation'><div class='section-kicker'>What Should I Buy?</div>"
            "<h2>No objective match yet</h2><p class='subtitle'>Add a Gameology price and at least one trusted competitor price to rank the queue.</p></div>"
        )
    p = row["product"]
    best = row["best_retailer"].name if row["best_retailer"] else "No trusted comparison"
    afford = "CAN BUY NOW" if row["affordable"] else (
        f"SAVE {_money(row['shortfall'])} MORE" if row["shortfall"] is not None else "PRICE NEEDED"
    )
    premium = (
        f"{row['premium']:+,.2f} ({row['premium_pct']:+.2f}%)" if row["premium"] is not None else "—"
    )
    return (
        "<div class='panel recommendation'><div class='section-kicker'>What Should I Buy?</div>"
        f"<div class='recommend-grid'><div><h2>{escape(p.name)}</h2>"
        f"<div class='recommend-prices'><span>Gameology <strong>{_money(row['gameology_price'])}</strong></span>"
        f"<span>{escape(best)} <strong>{_money(row['best_price'])}</strong></span>"
        f"<span>Premium <strong>{premium}</strong></span></div>"
        f"{pill(row['classification'], row['classification_kind'])} {pill(afford, 'ok' if row['affordable'] else 'warn')}</div>"
        f"<div class='recommend-outcome'><span>Guild after <strong>{_money(row['progress_after'])}</strong></span>"
        f"<span>Fund after <strong>{_money(row['fund_after'])}</strong></span></div></div>"
        f"<div class='actions'><a class='btn ghost' href='/retailer-goals/products/{escape(p.id)}'>View product</a>"
        f"<button type='button' onclick=\"document.getElementById('purchase-{escape(p.id)}').showModal()\">Mark purchased</button></div>"
        + _purchase_dialog(p, row["gameology_price"]) + "</div>"
    )


def _product_card(row: dict) -> str:
    p = row["product"]
    premium = (
        f"{row['premium']:+,.2f} &middot; {row['premium_pct']:+.2f}%" if row["premium"] is not None else "No trusted comparison"
    )
    if row["affordable"]:
        affordability = pill("CAN BUY NOW", "ok")
    elif row["shortfall"] is not None:
        weeks = row["weeks_until_affordable"]
        when = "next contribution" if weeks == 1 else (f"{weeks} weeks" if weeks is not None else "budget paused")
        affordability = pill(f"SAVE {_money(row['shortfall'])} · {when}", "warn")
    else:
        affordability = pill("ADD GAMEOLOGY PRICE", "neutral")
    image = f"<img src='{escape(p.image_url)}' alt='' loading='lazy'>" if p.image_url else "<span>no image</span>"
    link = (
        f"<a class='btn small' href='{escape(p.retailer_url)}' target='_blank' rel='noopener'>View on Gameology ↗</a>"
        if p.retailer_url else "<span class='muted'>Gameology URL not set</span>"
    )
    last_check = (
        f"<p class='last-check'>Last price check: {row['last_checked']:%d %b %Y %H:%M} UTC</p>"
        if row["last_checked"] else "<p class='last-check'>No price checks yet.</p>"
    )
    timing_bits = [row["product"].stock_status.value.replace("_", " ").title()]
    if p.release_date:
        timing_bits.append(f"release {p.release_date:%d %b %Y}")
    if p.preorder_date:
        timing_bits.append(f"preorder {p.preorder_date:%d %b %Y}")
    if p.purchase_limit:
        timing_bits.append(f"limit {p.purchase_limit}")
    warnings = "".join(pill(message.upper(), "bad") for message in row["budget_warnings"])
    return (
        f"<article class='queue-card' data-status='{p.status.value}' data-type='{escape(p.product_type)}'>"
        f"<div class='queue-image'>{image}</div><div class='queue-main'>"
        f"<div class='queue-head'><div><h3>{escape(p.name)}</h3>"
        f"<p>{escape(TCG_OPTIONS.get(p.tcg, p.tcg))} &middot; {escape(PRODUCT_TYPES.get(p.product_type, p.product_type))}"
        f"{' &middot; ' + escape(p.set_name) if p.set_name else ''}</p>"
        f"<p>{escape(' · '.join(timing_bits))}</p></div>"
        f"{pill(p.status.value.replace('_', ' ').upper(), 'warn' if p.status in {RetailerProductStatus.HIGH_PRIORITY, RetailerProductStatus.PREORDER} else 'neutral')}</div>"
        f"<div class='queue-metrics'><span>Gameology<strong>{_money(row['gameology_price'])}</strong></span>"
        f"<span>Best trusted AU<strong>{_money(row['best_price'])}</strong></span>"
        f"<span>Premium<strong>{premium}</strong></span>"
        f"<span>Guild contribution<strong>{_money(row['wanted_total'])}</strong></span>"
        f"<span>Wanted<strong>{p.quantity_wanted}</strong></span></div>"
        f"<div class='queue-pills'>{pill(row['classification'], row['classification_kind'])}{affordability}{warnings}</div>"
        f"<div class='queue-actions'>{link}<a class='btn small ghost' href='/retailer-goals/products/{escape(p.id)}'>Edit &amp; prices</a>"
        f"<button type='button' class='small' onclick=\"document.getElementById('purchase-{escape(p.id)}').showModal()\">Mark purchased</button></div>"
        + last_check + "</div>" + _purchase_dialog(p, row["gameology_price"]) + "</article>"
    )


def _purchase_history(state: dict, status_filter: str, sort: str) -> str:
    purchases = list(state["purchases"])
    if status_filter in {item.value for item in RetailerPurchaseStatus}:
        purchases = [p for p in purchases if p.status.value == status_filter]
    if sort == "oldest":
        purchases.sort(key=lambda p: (p.purchase_date, p.recorded_at))
    elif sort == "premium":
        purchases.sort(key=lambda p: p.guild_premium_paid or ZERO, reverse=True)
    elif sort == "spend":
        purchases.sort(key=lambda p: p.eligible_spend, reverse=True)

    products = {
        p.id: p for p in state["products"] for p in [p["product"]]
    }
    rows = []
    for purchase in purchases:
        product = products.get(purchase.product_id)
        lot_link = (
            f"<a href='/purchase-lots/{escape(purchase.purchase_lot_id)}'>Open</a>"
            if purchase.purchase_lot_id else "—"
        )
        action = ""
        if purchase.status == RetailerPurchaseStatus.RECORDED:
            action = (
                f"<form method='post' action='/retailer-goals/purchases/{purchase.id}/refund' "
                "onsubmit=\"return confirm('Refund/remove this purchase and reverse its fund and eligible-spend effect?')\">"
                "<button class='text-button' type='submit'>Refund / remove</button></form>"
            )
        rows.append(
            "<tr>"
            f"<td data-label='Date'>{purchase.purchase_date:%d %b %Y}</td>"
            f"<td data-label='Product'>{escape(product.name if product else 'Unknown product')}</td>"
            f"<td data-label='Qty'>{purchase.quantity}</td>"
            f"<td data-label='Price'>{_money(purchase.purchase_unit_price)}</td>"
            f"<td data-label='Eligible'>{_money(purchase.eligible_spend)}</td>"
            f"<td data-label='Market'>{_money(purchase.market_unit_price)}</td>"
            f"<td data-label='Premium'>{_money(purchase.guild_premium_paid)}</td>"
            f"<td data-label='Inventory'>{'Added' if purchase.inventory_added else 'Missing'}</td>"
            f"<td data-label='Purchase lot'>{lot_link}</td>"
            f"<td data-label='Order'>{escape(purchase.order_number or '—')}</td>"
            f"<td data-label='Status'>{pill(purchase.status.value.title(), 'ok' if purchase.status == RetailerPurchaseStatus.RECORDED else 'neutral')}{action}</td>"
            "</tr>"
        )
    return (
        "<div class='panel'><div class='section-head'><div><div class='section-kicker'>History</div><h2>Gameology Purchase History</h2></div>"
        "<form method='get' class='inline-filter'><select name='purchase_status'>"
        f"<option value=''>All statuses</option>{_enum_options(RetailerPurchaseStatus, status_filter)}</select>"
        f"<select name='purchase_sort'>{_options({'newest':'Newest','oldest':'Oldest','spend':'Eligible spend','premium':'Guild premium'}, sort)}</select>"
        "<button type='submit'>Apply</button></form></div>"
        "<div class='history-table'><table><thead><tr><th>Date</th><th>Product</th><th>Qty</th><th>Price</th>"
        "<th>Eligible</th><th>Market</th><th>Premium</th><th>Inventory</th><th>Purchase lot</th><th>Order</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=11>No purchases recorded yet.</td></tr>'}</tbody></table></div></div>"
    )


def _projection_scenarios(state: dict) -> str:
    from .retailer_goals_service import projection

    rows = []
    for weekly in (Decimal("50"), Decimal("75"), Decimal("100"), Decimal("150")):
        values = []
        for milestone in state["milestones"]:
            result = projection(milestone.target_amount, state["spend"], weekly)
            text = "achieved" if result["weeks"] == 0 else f"{result['weeks']} weeks"
            values.append(f"<span>{escape(milestone.label)}<strong>{text}</strong></span>")
        rows.append(f"<div class='projection-row'><b>{_money(weekly)}/week</b>{''.join(values)}</div>")
    return (
        "<div class='panel projection-panel'><div class='section-kicker'>Projected Completion</div>"
        f"<h2>Budget scenarios</h2>{''.join(rows)}"
        "<p class='fine-print'>Projection assumes your budget is eventually spent on eligible Gameology purchases.</p></div>"
    )


@router.get("", response_class=HTMLResponse)
def retailer_goals_index(session: Session = Depends(get_session),
                         user: str = Depends(require_dashboard_user)) -> RedirectResponse:
    program = ensure_gameology_program(session)
    return RedirectResponse(f"/retailer-goals/{program.slug}", status_code=303)


@router.get("/{slug}", response_class=HTMLResponse)
def retailer_goal_dashboard(
    slug: str, notice: str = Query(""), error: str = Query(""), purchased: str = Query(""),
    purchase_status: str = Query(""), purchase_sort: str = Query("newest"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    program = _program(session, slug)
    state = program_state(session, program)
    retailer = session.get(Retailer, program.retailer_id)
    booster = [row for row in state["products"] if row["product"].product_type == "booster_box"]
    queue = state["products"]

    result_html = ""
    if purchased:
        purchase = session.get(RetailerPurchase, purchased)
        if purchase and purchase.program_id == program.id:
            before = money(state["spend"] - (purchase.eligible_spend if purchase.status == RetailerPurchaseStatus.RECORDED else ZERO))
            fund_before = money(state["fund"]["available"] + purchase.purchase_unit_price * purchase.quantity)
            result_html = (
                "<div class='purchase-success'><div class='success-check'>✓</div><div><div class='section-kicker'>Purchase recorded</div>"
                f"<h2>Guild progress {_money(before)} → {_money(state['spend'])}</h2>"
                f"<p>Gameology spend +{_money(purchase.eligible_spend)} &middot; Remaining to $5k {_money(max(ZERO, Decimal('5000') - state['spend']))}"
                f" &middot; Fund {_money(fund_before)} → {_money(state['fund']['available'])}"
                f" &middot; Guild premium {('+' + _money(purchase.guild_premium_paid)) if purchase.guild_premium_paid is not None else 'not available'}"
                f" &middot; Inventory +{purchase.quantity} &middot; <a href='/purchase-lots/{escape(purchase.purchase_lot_id or '')}'>Open purchase lot</a></p></div></div>"
            )

    alerts = (f"<div class='notice ok'>{escape(notice)}</div>" if notice else "") + (
        f"<div class='notice bad'>{escape(error)}</div>" if error else ""
    )
    progress_cards = _milestone_cards(state)
    milestone_history = "".join(
        f"<li>{'✓' if m.achieved_at else '○'} <strong>{escape(m.label)}</strong> — {_money(m.target_amount)}"
        f"{f' — reached {m.achieved_at:%d %B %Y}' if m.achieved_at else ''}</li>"
        for m in state["milestones"]
    )
    queue_cards = "".join(_product_card(row) for row in queue)
    booster_cards = "".join(_product_card(row) for row in booster)
    notification = session.scalar(select(RetailerNotificationPreference).where(
        RetailerNotificationPreference.program_id == program.id
    ))
    retailers = session.scalars(select(Retailer).order_by(Retailer.name)).all()
    retailer_rows = "".join(
        "<tr>"
        f"<td data-label='Retailer'><strong>{escape(r.name)}</strong><small>{escape(r.domain or '')}"
        f"{f' · reviewed {r.last_reviewed:%d %b %Y}' if r.last_reviewed else ''}</small></td>"
        f"<td data-label='Trust'>{pill(r.trust_status.value.title(), 'ok' if r.trust_status == RetailerTrustStatus.TRUSTED else ('bad' if r.trust_status == RetailerTrustStatus.BLOCKED else 'warn'))}</td>"
        f"<td data-label='Best price'>{'Eligible' if r.best_price_eligible and r.trust_status == RetailerTrustStatus.TRUSTED else 'Excluded'}</td>"
        f"<td><form method='post' action='/retailer-goals/retailers/{r.id}' class='trust-form'>"
        f"<select name='trust_status'>{_enum_options(RetailerTrustStatus, r.trust_status.value)}</select>"
        f"<label class='check'><input type='checkbox' name='best_price_eligible' value='1'{' checked' if r.best_price_eligible else ''}> Best-price eligible</label>"
        f"<input name='notes' value='{escape(r.notes or '')}' placeholder='Trust notes'>"
        "<button type='submit'>Save</button></form></td></tr>"
        for r in retailers
    )

    fund_paused = pill("EMERGENCY PAUSE", "bad") if state["budget"].is_paused else ""
    fund = (
        "<div class='panel fund-panel'><div class='section-head'><div><div class='section-kicker'>Gameology Fund</div>"
        f"<h2>{_money(state['fund']['available'])} available</h2></div>{fund_paused}</div>"
        "<div class='metric-grid'>"
        f"<div><span>Weekly contribution</span><strong>{_money(state['budget'].weekly_contribution)}</strong></div>"
        f"<div><span>Next contribution</span><strong>{state['budget'].next_contribution_date:%A, %d %b}</strong></div>"
        f"<div><span>Total contributed</span><strong>{_money(state['fund']['contributed'])}</strong></div>"
        f"<div><span>Total spent</span><strong>{_money(state['fund']['spent'])}</strong></div></div></div>"
    )
    premium_summary = (
        "<div class='panel premium-panel'><div class='section-kicker'>Guild Cost</div><h2>What loyalty has cost</h2>"
        "<div class='metric-grid'>"
        f"<div><span>Recorded Gameology purchases</span><strong>{_money(state['recorded_spend'])}</strong></div>"
        f"<div><span>Equivalent trusted AU cost</span><strong>{_money(state['equivalent_cost'])}</strong></div>"
        f"<div><span>Total Guild premium paid</span><strong>{_money(state['premium_paid'])}</strong></div>"
        f"<div><span>Effective Guild premium</span><strong>{f'{state['effective_premium_pct']:.2f}%' if state['effective_premium_pct'] is not None else '—'}</strong></div>"
        "</div><p class='fine-print'>Price comparison only — not investment advice. Historical starting spend has no old market-price snapshot, so it is excluded from Guild Cost.</p></div>"
    )

    settings = (
        "<details class='panel settings-panel'><summary>Budget, historical spend &amp; thresholds</summary>"
        f"<form method='post' action='/retailer-goals/{program.id}/settings' class='rg-form settings-grid'>"
        f"<label>Current Historical Spend<input name='historical_spend' type='number' min='0' step='0.01' value='{program.historical_eligible_spend}' required></label>"
        f"<label>Current fund balance<input name='fund_balance' type='number' step='0.01' value='{state['fund']['available']}' required><small>Changing this creates an auditable adjustment.</small></label>"
        f"<label>Weekly contribution<input name='weekly_contribution' type='number' min='0' step='0.01' value='{state['budget'].weekly_contribution}' required></label>"
        f"<label>Contribution day<select name='contribution_weekday'>{_options({str(i): name for i, name in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])}, str(state['budget'].contribution_weekday))}</select></label>"
        f"<label>Monthly maximum<input name='monthly_maximum' type='number' min='0' step='0.01' value='{state['budget'].monthly_maximum or ''}'></label>"
        f"<label>Per-product maximum<input name='per_product_maximum' type='number' min='0' step='0.01' value='{state['budget'].per_product_maximum or ''}'></label>"
        f"<label>Excellent ≤ %<input name='excellent_max_pct' type='number' step='0.01' value='{state['premium_config'].excellent_max_pct}'></label>"
        f"<label>Excellent Guild Buy ≤ %<input name='excellent_guild_max_pct' type='number' step='0.01' value='{state['premium_config'].excellent_guild_max_pct}'></label>"
        f"<label>Acceptable ≤ %<input name='acceptable_max_pct' type='number' step='0.01' value='{state['premium_config'].acceptable_max_pct}'></label>"
        f"<label>Borderline ≤ %<input name='borderline_max_pct' type='number' step='0.01' value='{state['premium_config'].borderline_max_pct}'></label>"
        f"<label class='check danger-check'><input name='is_paused' type='checkbox' value='1'{' checked' if state['budget'].is_paused else ''}> Emergency pause weekly contributions</label>"
        "<button type='submit'>Save settings</button></form>"
        "<p class='fine-print'>Projection assumes your budget is eventually spent on eligible Gameology purchases.</p></details>"
    )

    add_product = (
        "<details class='panel'><summary>Add a product to the Buy Queue</summary>"
        f"<form method='post' action='/retailer-goals/{program.id}/products' class='rg-form settings-grid'>"
        "<label>Product name<input name='name' required></label>"
        f"<label>TCG<select name='tcg'>{_options(TCG_OPTIONS, 'pokemon')}</select></label>"
        f"<label>Product type<select name='product_type'>{_options(PRODUCT_TYPES, 'booster_box')}</select></label>"
        "<label>Set<input name='set_name'></label><label>Gameology price<input name='gameology_price' type='number' min='0' step='0.01'></label>"
        "<label>Gameology URL<input name='retailer_url' type='url'></label><label>Quantity wanted<input name='quantity_wanted' type='number' min='1' value='1'></label>"
        "<label>Maximum acceptable price<input name='maximum_acceptable_price' type='number' min='0' step='0.01'></label>"
        "<label>Release date<input name='release_date' type='date'></label><label>Preorder date<input name='preorder_date' type='date'></label>"
        "<label>Purchase limit<input name='purchase_limit' type='number' min='1'></label>"
        f"<label>Stock<select name='stock_status'>{_enum_options(RetailerStockStatus, RetailerStockStatus.UNKNOWN.value)}</select></label>"
        f"<label>Status<select name='status'>{_enum_options(RetailerProductStatus, RetailerProductStatus.WATCH.value)}</select></label>"
        "<label>Priority (1–5)<input name='priority' type='number' min='1' max='5' value='3'></label>"
        "<label>Image URL<input name='image_url' type='url'></label><label class='wide'>Notes<textarea name='notes' rows='3'></textarea></label>"
        "<button type='submit'>Add to queue</button></form></details>"
    )

    notification_form = ""
    if notification:
        flags = [
            ("preorder_open", "Gameology preorder opens"), ("restock", "Product restocks"),
            ("price_drop", "Price drops"), ("premium_at_or_below_5", "Premium ≤ 5%"),
            ("premium_at_or_below_10", "Premium ≤ 10%"), ("becomes_affordable", "Product becomes affordable"),
            ("high_priority_status_change", "High-priority status changes"),
        ]
        checks = "".join(
            f"<label class='check'><input type='checkbox' name='{name}' value='1'{' checked' if getattr(notification, name) else ''}> {label}</label>"
            for name, label in flags
        )
        notification_form = (
            "<details class='panel'><summary>Notification preferences</summary>"
            f"<form method='post' action='/retailer-goals/{program.id}/notification-preferences' class='check-grid'>{checks}"
            "<button type='submit'>Save preferences</button></form>"
            "<p class='fine-print'>Preferences are ready for a future monitor; no automated ordering or payment exists.</p></details>"
        )

    body = (
        brand_header("Retailer Goals — Gameology") + alerts + result_html
        + "<section class='guild-hero'><div><div class='section-kicker'>Gameology Guild</div>"
        f"<h1>{_money(state['spend'])}</h1><p>Lifetime eligible spend</p></div>"
        f"<div class='hero-side'><span>Weekly budget<strong>{_money(state['budget'].weekly_contribution)}</strong></span>"
        f"<span>Available fund<strong>{_money(state['fund']['available'])}</strong></span></div></section>"
        + f"<section class='milestone-grid'>{progress_cards}</section>"
        + f"<section class='summary-grid'>{fund}{premium_summary}</section>"
        + _projection_scenarios(state)
        + _recommendation(state)
        + "<div class='section-title'><div><div class='section-kicker'>Priority view</div><h2>Booster Box Opportunities</h2></div>"
          "<p>English Pokémon, One Piece, preorders and restocks rise through priority and availability.</p></div>"
        + f"<section class='queue-list'>{booster_cards or '<div class=panel>No booster boxes in the queue.</div>'}</section>"
        + "<div class='section-title'><div><div class='section-kicker'>Plan</div><h2>Gameology Buy Queue</h2></div>"
          "<div class='queue-filter'><button type='button' data-filter='all'>All</button><button type='button' data-filter='high_priority'>High priority</button><button type='button' data-filter='preorder'>Preorders</button></div></div>"
        + f"<section class='queue-list' id='buy-queue'>{queue_cards or '<div class=panel>No queue items yet.</div>'}</section>"
        + add_product
        + _purchase_history(state, purchase_status, purchase_sort)
        + f"<div class='summary-grid'><div class='panel'><div class='section-kicker'>Milestones</div><h2>Milestone history</h2><ul class='milestone-history'>{milestone_history}</ul>"
          f"<form method='post' action='/retailer-goals/{program.id}/milestones' class='inline-form'><input name='label' placeholder='New milestone' required>"
          "<input name='target_amount' type='number' min='0.01' step='0.01' placeholder='Target' required><button>Add</button></form></div>"
        + settings + "</div>"
        + "<details class='panel'><summary>Trusted Australian retailers</summary><div class='history-table'><table><thead><tr><th>Retailer</th><th>Trust</th><th>Best price</th><th>Review</th></tr></thead>"
        + f"<tbody>{retailer_rows}</tbody></table></div>"
        + "<form method='post' action='/retailer-goals/retailers' class='inline-form'><input name='name' placeholder='Retailer name' required>"
          "<input name='domain' placeholder='example.com.au'><input name='website' type='url' placeholder='https://…'>"
          f"<select name='trust_status'>{_enum_options(RetailerTrustStatus, RetailerTrustStatus.UNVERIFIED.value)}</select>"
          "<input name='notes' placeholder='Why trusted / what to verify'><button>Add retailer</button></form></details>"
        + notification_form + _STYLE + _SCRIPT
    )
    return HTMLResponse(page(f"EzBay — {program.name}", body))


@router.post("/{program_id}/settings")
def update_settings(
    program_id: str, historical_spend: str = Form(...), fund_balance: str = Form(...),
    weekly_contribution: str = Form(...), contribution_weekday: str = Form("4"),
    monthly_maximum: str = Form(""), per_product_maximum: str = Form(""),
    excellent_max_pct: str = Form("0"), excellent_guild_max_pct: str = Form("5"),
    acceptable_max_pct: str = Form("10"), borderline_max_pct: str = Form("15"),
    is_paused: str = Form(""), user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    program = session.get(RetailerProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="program not found")
    budget = session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == program.id))
    config = session.scalar(select(RetailerPremiumConfig).where(RetailerPremiumConfig.program_id == program.id))
    if budget is None or config is None:
        raise HTTPException(status_code=409, detail="program configuration missing")
    try:
        accrue_weekly_fund(session, budget)
        desired_balance = money(fund_balance)
        current_balance = fund_totals(session, budget)["available"]
        delta = money(desired_balance - current_balance)
        if delta:
            session.add(RetailerFundEntry(
                id=str(uuid.uuid4()), budget_id=budget.id, kind=RetailerFundEntryKind.ADJUSTMENT,
                amount=delta, entry_date=date.today(), notes="Manual fund balance adjustment",
            ))
        was_paused = budget.is_paused
        weekday = int(contribution_weekday)
        if weekday not in range(7):
            raise ValueError("Contribution day is invalid.")
        program.historical_eligible_spend = money(historical_spend, allow_negative=False)
        budget.weekly_contribution = money(weekly_contribution, allow_negative=False)
        budget.monthly_maximum = _optional_money(monthly_maximum)
        budget.per_product_maximum = _optional_money(per_product_maximum)
        budget.contribution_weekday = weekday
        budget.is_paused = bool(is_paused)
        if (was_paused and not budget.is_paused) or budget.next_contribution_date.weekday() != weekday:
            budget.next_contribution_date = next_weekday(date.today(), weekday)
        config.excellent_max_pct = money(excellent_max_pct)
        config.excellent_guild_max_pct = money(excellent_guild_max_pct)
        config.acceptable_max_pct = money(acceptable_max_pct)
        config.borderline_max_pct = money(borderline_max_pct)
        if not (config.excellent_max_pct <= config.excellent_guild_max_pct <= config.acceptable_max_pct <= config.borderline_max_pct):
            raise ValueError("Premium thresholds must increase from Excellent through Borderline.")
        update_milestones(session, program)
        session.commit()
    except ValueError as exc:
        session.rollback()
        return RedirectResponse(f"/retailer-goals/{program.slug}?error={quote(str(exc))}", status_code=303)
    record_event(session, actor_type="user", actor_id=user, action="retailer_goals.settings",
                 resource_type="retailer_program", resource_id=program.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"paused": budget.is_paused})
    return RedirectResponse(f"/retailer-goals/{program.slug}?notice={quote('Budget and Guild settings updated.')}", status_code=303)


@router.post("/{program_id}/milestones")
def add_milestone(program_id: str, label: str = Form(...), target_amount: str = Form(...),
                  user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    program = session.get(RetailerProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="program not found")
    target = money(target_amount, allow_negative=False)
    if target <= 0:
        raise HTTPException(status_code=400, detail="target must be above zero")
    session.add(RetailerMilestone(
        id=str(uuid.uuid4()), program_id=program.id, label=label.strip()[:256],
        target_amount=target, sort_order=int(target),
    ))
    session.commit()
    update_milestones(session, program)
    session.commit()
    return RedirectResponse(f"/retailer-goals/{program.slug}#milestones", status_code=303)


@router.post("/{program_id}/products")
def add_product(
    program_id: str, name: str = Form(...), tcg: str = Form("other"), product_type: str = Form("other"),
    set_name: str = Form(""), gameology_price: str = Form(""), retailer_url: str = Form(""),
    quantity_wanted: str = Form("1"), maximum_acceptable_price: str = Form(""),
    release_date: str = Form(""), preorder_date: str = Form(""), purchase_limit: str = Form(""),
    stock_status: str = Form(RetailerStockStatus.UNKNOWN.value),
    status: str = Form(RetailerProductStatus.WATCH.value), priority: str = Form("3"),
    image_url: str = Form(""), notes: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    program = session.get(RetailerProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="program not found")
    if tcg not in TCG_OPTIONS or product_type not in PRODUCT_TYPES:
        raise HTTPException(status_code=400, detail="unknown TCG or product type")
    priority_int = int(priority)
    if priority_int not in range(1, 6):
        raise HTTPException(status_code=400, detail="priority must be from 1 to 5")
    catalog = ensure_catalog_product(
        session, name=name.strip(), tcg=tcg, product_type=product_type,
        set_name=set_name.strip() or None, image_url=_url(image_url),
    )
    product = RetailerProduct(
        id=str(uuid.uuid4()), program_id=program.id, catalog_item_id=catalog.id,
        name=name.strip()[:256], tcg=tcg, product_type=product_type,
        set_name=set_name.strip()[:256] or None, retailer_url=_url(retailer_url),
        quantity_wanted=positive_int(quantity_wanted),
        maximum_acceptable_price=_optional_money(maximum_acceptable_price),
        release_date=_date(release_date), preorder_date=_date(preorder_date),
        purchase_limit=positive_int(purchase_limit) if purchase_limit.strip() else None,
        stock_status=RetailerStockStatus(stock_status), status=RetailerProductStatus(status),
        priority=priority_int, image_url=_url(image_url), notes=notes.strip() or None,
    )
    session.add(product)
    session.flush()
    if gameology_price.strip():
        session.add(RetailerPriceSnapshot(
            id=str(uuid.uuid4()), product_id=product.id, retailer_id=program.retailer_id,
            price=money(gameology_price, allow_negative=False), product_url=product.retailer_url,
            stock_status=product.stock_status, source="manual",
        ))
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="retailer_goals.product_added",
                 resource_type="retailer_product", resource_id=product.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"program_id": program.id})
    return RedirectResponse(f"/retailer-goals/{program.slug}?notice={quote('Product added to the Buy Queue.')}", status_code=303)


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(product_id: str, notice: str = Query(""), user: str = Depends(require_dashboard_user),
                   session: Session = Depends(get_session)) -> HTMLResponse:
    product = session.get(RetailerProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    program = session.get(RetailerProgram, product.program_id)
    state = program_state(session, program)
    row = next(item for item in state["products"] if item["product"].id == product.id)
    retailers = session.scalars(select(Retailer).order_by(Retailer.name)).all()
    retailer_options = "".join(f"<option value='{r.id}'>{escape(r.name)} ({r.trust_status.value})</option>" for r in retailers)
    history = session.scalars(select(RetailerPriceSnapshot).where(
        RetailerPriceSnapshot.product_id == product.id
    ).order_by(RetailerPriceSnapshot.observed_at.desc())).all()
    retailer_by_id = {r.id: r for r in retailers}
    price_rows = "".join(
        "<tr>"
        f"<td data-label='Checked'>{snap.observed_at:%d %b %Y %H:%M}</td>"
        f"<td data-label='Retailer'>{escape(retailer_by_id.get(snap.retailer_id).name if retailer_by_id.get(snap.retailer_id) else 'Unknown')}</td>"
        f"<td data-label='Price'>{_money(snap.price)}</td><td data-label='Stock'>{escape(snap.stock_status.value.replace('_', ' ').title())}</td>"
        f"<td data-label='Source'>{escape(snap.source)}</td>"
        f"<td>{f'<a href="{escape(snap.product_url)}" target="_blank" rel="noopener">Open ↗</a>' if snap.product_url else '—'}</td></tr>"
        for snap in history
    )
    body = (
        brand_header(product.name)
        + (f"<div class='notice ok'>{escape(notice)}</div>" if notice else "")
        + f"<div class='product-summary panel'><div><div class='section-kicker'>Current comparison</div><h2>{escape(product.name)}</h2>"
        f"<p>Gameology {_money(row['gameology_price'])} &middot; Best trusted AU {_money(row['best_price'])} &middot; "
        f"Premium {_money(row['premium'])}{f' ({row['premium_pct']:+.2f}%)' if row['premium_pct'] is not None else ''}</p>"
        f"{pill(row['classification'], row['classification_kind'])}</div>"
        f"<div class='actions'>{f'<a class="btn" href="{escape(product.retailer_url)}" target="_blank" rel="noopener">View on Gameology ↗</a>' if product.retailer_url else ''}"
        f"<button type='button' onclick=\"document.getElementById('purchase-{product.id}').showModal()\">Mark purchased</button></div></div>"
        + _purchase_dialog(product, row["gameology_price"])
        + "<div class='summary-grid'><div class='panel'><h2>Edit queue item</h2>"
        f"<form method='post' action='/retailer-goals/products/{product.id}' class='rg-form'>"
        f"<label>Product name<input name='name' value='{escape(product.name)}' required></label>"
        f"<label>TCG<select name='tcg'>{_options(TCG_OPTIONS, product.tcg)}</select></label>"
        f"<label>Product type<select name='product_type'>{_options(PRODUCT_TYPES, product.product_type)}</select></label>"
        f"<label>Set<input name='set_name' value='{escape(product.set_name or '')}'></label>"
        f"<label>Gameology URL<input name='retailer_url' type='url' value='{escape(product.retailer_url or '')}'></label>"
        f"<label>Quantity wanted<input name='quantity_wanted' type='number' min='1' value='{product.quantity_wanted}'></label>"
        f"<label>Maximum acceptable price<input name='maximum_acceptable_price' type='number' min='0' step='0.01' value='{product.maximum_acceptable_price or ''}'></label>"
        f"<label>Release date<input name='release_date' type='date' value='{product.release_date or ''}'></label>"
        f"<label>Preorder date<input name='preorder_date' type='date' value='{product.preorder_date or ''}'></label>"
        f"<label>Purchase limit<input name='purchase_limit' type='number' min='1' value='{product.purchase_limit or ''}'></label>"
        f"<label>Stock<select name='stock_status'>{_enum_options(RetailerStockStatus, product.stock_status.value)}</select></label>"
        f"<label>Status<select name='status'>{_enum_options(RetailerProductStatus, product.status.value)}</select></label>"
        f"<label>Priority<input name='priority' type='number' min='1' max='5' value='{product.priority}'></label>"
        f"<label>Image URL<input name='image_url' type='url' value='{escape(product.image_url or '')}'></label>"
        f"<label>Notes<textarea name='notes' rows='4'>{escape(product.notes or '')}</textarea></label><button>Save product</button></form></div>"
        + "<div class='panel'><h2>Add retailer price</h2>"
        f"<form method='post' action='/retailer-goals/products/{product.id}/prices' class='rg-form'>"
        f"<label>Retailer<select name='retailer_id'>{retailer_options}</select></label>"
        "<label>Price (AUD)<input name='price' type='number' min='0' step='0.01' required></label>"
        f"<label>Stock<select name='stock_status'>{_enum_options(RetailerStockStatus, RetailerStockStatus.IN_STOCK.value)}</select></label>"
        "<label>Verified product URL<input name='product_url' type='url'></label><button>Add price check</button></form>"
        "<p class='fine-print'>Only retailers marked Trusted and Best-price eligible can establish the comparison price.</p></div></div>"
        + "<div class='panel'><h2>Price history</h2><div class='history-table'><table><thead><tr><th>Checked</th><th>Retailer</th><th>Price</th><th>Stock</th><th>Source</th><th>Link</th></tr></thead>"
        f"<tbody>{price_rows or '<tr><td colspan=6>No price history yet.</td></tr>'}</tbody></table></div></div>"
        + _STYLE + _SCRIPT
    )
    return HTMLResponse(page(f"EzBay — {product.name}", body))


@router.post("/products/{product_id}")
def edit_product(
    product_id: str, name: str = Form(...), tcg: str = Form("other"), product_type: str = Form("other"),
    set_name: str = Form(""), retailer_url: str = Form(""), quantity_wanted: str = Form("1"),
    maximum_acceptable_price: str = Form(""), release_date: str = Form(""), preorder_date: str = Form(""),
    purchase_limit: str = Form(""), stock_status: str = Form("unknown"), status: str = Form("watch"),
    priority: str = Form("3"), image_url: str = Form(""), notes: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    product = session.get(RetailerProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    priority_int = int(priority)
    if priority_int not in range(1, 6):
        raise HTTPException(status_code=400, detail="priority must be from 1 to 5")
    product.name = name.strip()[:256]
    product.tcg = tcg if tcg in TCG_OPTIONS else "other"
    product.product_type = product_type if product_type in PRODUCT_TYPES else "other"
    product.set_name = set_name.strip()[:256] or None
    product.retailer_url = _url(retailer_url)
    product.quantity_wanted = positive_int(quantity_wanted)
    product.maximum_acceptable_price = _optional_money(maximum_acceptable_price)
    product.release_date = _date(release_date)
    product.preorder_date = _date(preorder_date)
    product.purchase_limit = positive_int(purchase_limit) if purchase_limit.strip() else None
    product.stock_status = RetailerStockStatus(stock_status)
    product.status = RetailerProductStatus(status)
    product.priority = priority_int
    product.image_url = _url(image_url)
    product.notes = notes.strip() or None
    session.commit()
    return RedirectResponse(f"/retailer-goals/products/{product.id}?notice={quote('Product updated.')}", status_code=303)


@router.post("/products/{product_id}/prices")
def add_price(
    product_id: str, retailer_id: str = Form(...), price: str = Form(...),
    stock_status: str = Form(RetailerStockStatus.IN_STOCK.value), product_url: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    product = session.get(RetailerProduct, product_id)
    retailer = session.get(Retailer, retailer_id)
    if product is None or retailer is None:
        raise HTTPException(status_code=404, detail="product or retailer not found")
    amount = money(price, allow_negative=False)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="price must be above zero")
    url = _url(product_url)
    status = RetailerStockStatus(stock_status)
    session.add(RetailerPriceSnapshot(
        id=str(uuid.uuid4()), product_id=product.id, retailer_id=retailer.id,
        price=amount, product_url=url, stock_status=status, source="manual",
    ))
    program = session.get(RetailerProgram, product.program_id)
    if retailer.id == program.retailer_id:
        product.stock_status = status
        if url:
            product.retailer_url = url
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="retailer_goals.price_checked",
                 resource_type="retailer_product", resource_id=product.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={"retailer_id": retailer.id, "stock": status.value})
    return RedirectResponse(f"/retailer-goals/products/{product.id}?notice={quote('Retailer price recorded.')}", status_code=303)


@router.post("/products/{product_id}/purchase")
def mark_purchased(
    product_id: str, purchase_price: str = Form(...), quantity: str = Form("1"),
    purchase_date: str = Form(...), eligible_spend: str = Form(""), shipping: str = Form("0"),
    order_number: str = Form(""), notes: str = Form(""), submission_key: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    product = session.get(RetailerProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    program = session.get(RetailerProgram, product.program_id)
    clean_submission_key = submission_key.strip()[:36]
    if clean_submission_key:
        existing = session.scalar(select(RetailerPurchase).where(
            RetailerPurchase.submission_key == clean_submission_key
        ))
        if existing is not None:
            if existing.product_id != product.id:
                raise HTTPException(status_code=409, detail="purchase submission key was already used")
            return RedirectResponse(
                f"/retailer-goals/{program.slug}?purchased={existing.id}", status_code=303
            )
    try:
        unit = money(purchase_price, allow_negative=False)
        qty = positive_int(quantity)
        if unit <= 0:
            raise ValueError("Purchase price must be above zero.")
        eligible = money(eligible_spend, allow_negative=False) if eligible_spend.strip() else money(unit * qty)
        result = record_purchase(
            session, program=program, product=product, unit_price=unit, quantity=qty,
            purchase_date=_date(purchase_date, default=date.today()), eligible_amount=eligible,
            shipping=money(shipping, allow_negative=False), order_number=order_number,
            notes=notes, submission_key=clean_submission_key,
        )
    except ValueError as exc:
        session.rollback()
        return RedirectResponse(f"/retailer-goals/{program.slug}?error={quote(str(exc))}", status_code=303)
    record_event(session, actor_type="user", actor_id=user, action="retailer_goals.purchase_recorded",
                 resource_type="retailer_purchase", resource_id=result["purchase"].id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={
                     "quantity": qty, "inventory_item_id": result["purchase"].inventory_item_id,
                     "purchase_lot_id": result["purchase"].purchase_lot_id,
                 })
    return RedirectResponse(f"/retailer-goals/{program.slug}?purchased={result['purchase'].id}", status_code=303)


@router.post("/purchases/{purchase_id}/refund")
def refund(purchase_id: str, user: str = Depends(require_dashboard_user),
           session: Session = Depends(get_session)):
    purchase = session.get(RetailerPurchase, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail="purchase not found")
    program = session.get(RetailerProgram, purchase.program_id)
    try:
        refund_purchase(session, purchase)
    except ValueError as exc:
        session.rollback()
        return RedirectResponse(f"/retailer-goals/{program.slug}?error={quote(str(exc))}", status_code=303)
    record_event(session, actor_type="user", actor_id=user, action="retailer_goals.purchase_refunded",
                 resource_type="retailer_purchase", resource_id=purchase.id, outcome="success",
                 correlation_id=str(uuid.uuid4()), details={})
    return RedirectResponse(f"/retailer-goals/{program.slug}?notice={quote('Purchase refunded and its fund, progress and inventory effects reversed.')}", status_code=303)


@router.post("/retailers")
def add_retailer(name: str = Form(...), domain: str = Form(""), website: str = Form(""), notes: str = Form(""),
                 trust_status: str = Form(RetailerTrustStatus.UNVERIFIED.value),
                 user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    trust = RetailerTrustStatus(trust_status)
    session.add(Retailer(
        id=str(uuid.uuid4()), name=name.strip()[:256], domain=domain.strip()[:256] or None,
        website=_url(website), trust_status=trust,
        best_price_eligible=False, last_reviewed=date.today() if trust != RetailerTrustStatus.UNVERIFIED else None,
        notes=notes.strip() or None,
    ))
    session.commit()
    return RedirectResponse(f"/retailer-goals/{GAMEOLOGY_PROGRAM_SLUG}?notice={quote('Retailer added. Review its trust status before using it for Best AU price.')}", status_code=303)


@router.post("/retailers/{retailer_id}")
def update_retailer(retailer_id: str, trust_status: str = Form(...), best_price_eligible: str = Form(""),
                    notes: str = Form(""),
                    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    retailer = session.get(Retailer, retailer_id)
    if retailer is None:
        raise HTTPException(status_code=404, detail="retailer not found")
    retailer.trust_status = RetailerTrustStatus(trust_status)
    retailer.best_price_eligible = bool(best_price_eligible) and retailer.trust_status == RetailerTrustStatus.TRUSTED
    retailer.notes = notes.strip() or None
    retailer.last_reviewed = date.today()
    session.commit()
    return RedirectResponse(f"/retailer-goals/{GAMEOLOGY_PROGRAM_SLUG}?notice={quote('Retailer trust updated.')}", status_code=303)


@router.post("/{program_id}/notification-preferences")
async def update_notification_preferences(program_id: str, request: Request, user: str = Depends(require_dashboard_user),
                                          session: Session = Depends(get_session)):
    preference = session.scalar(select(RetailerNotificationPreference).where(
        RetailerNotificationPreference.program_id == program_id
    ))
    program = session.get(RetailerProgram, program_id)
    if preference is None or program is None:
        raise HTTPException(status_code=404, detail="notification preferences not found")
    form = await request.form()
    for field in (
        "preorder_open", "restock", "price_drop", "premium_at_or_below_5",
        "premium_at_or_below_10", "becomes_affordable", "high_priority_status_change",
    ):
        setattr(preference, field, bool(form.get(field)))
    session.commit()
    return RedirectResponse(f"/retailer-goals/{program.slug}?notice={quote('Notification preferences saved.')}", status_code=303)


_STYLE = """<style>
.section-kicker{text-transform:uppercase;letter-spacing:.1em;font-size:11px;font-weight:800;color:var(--accent)}
.guild-hero{display:flex;justify-content:space-between;gap:22px;align-items:end;padding:24px;margin:0 0 18px;
  border:1px solid var(--panel-border);border-radius:16px;background:linear-gradient(135deg,rgba(var(--accent-rgb),.10),rgba(124,58,237,.08))}
.guild-hero h1{font-size:38px;margin:4px 0 0;letter-spacing:-.04em}.guild-hero p{margin:0;color:var(--text-dim)}
.hero-side{display:flex;gap:26px}.hero-side span,.metric-grid span{display:flex;flex-direction:column;color:var(--text-dim);font-size:12px}
.hero-side strong{font-size:20px;color:var(--text);margin-top:3px}.milestone-grid,.summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:18px}
.milestone-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:14px;padding:18px;box-shadow:var(--shadow)}
.milestone-head,.milestone-values,.section-head{display:flex;justify-content:space-between;gap:12px;align-items:center}
.milestone-head h3{margin:0;font-size:15px}.rg-progress{height:12px;background:var(--surface-sunken);border-radius:999px;overflow:hidden;margin:15px 0 9px}
.rg-progress span{height:100%;display:block;background:linear-gradient(90deg,var(--accent),var(--accent-2));border-radius:inherit}
.milestone-values{font-size:12px;color:var(--text-dim)}.milestone-date{margin-top:10px;font-size:12px;color:var(--text-dim)}
.metric-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.metric-grid strong{font-size:18px;color:var(--text);margin-top:4px}
.recommendation{border-color:rgba(var(--accent-rgb),.35)}.recommend-grid{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:center}
.recommendation h2,.section-title h2,.section-head h2{margin:4px 0 10px}.recommend-prices{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px}
.recommend-prices span,.recommend-outcome span{display:flex;flex-direction:column;font-size:12px;color:var(--text-dim)}
.recommend-outcome{display:flex;flex-direction:column;gap:10px}.recommend-outcome strong{font-size:18px;color:var(--text)}
.actions,.queue-actions,.dialog-actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:14px}.section-title{display:flex;justify-content:space-between;gap:16px;align-items:end;margin:28px 0 12px}
.section-title p{max-width:470px;margin:0;color:var(--text-dim);font-size:12px}.queue-list{display:grid;gap:12px}.queue-card{display:grid;grid-template-columns:92px 1fr;gap:16px;background:var(--panel);border:1px solid var(--panel-border);border-radius:14px;padding:14px;box-shadow:var(--shadow)}
.queue-image{width:92px;height:104px;border-radius:10px;background:var(--surface-sunken);display:flex;align-items:center;justify-content:center;color:var(--text-faint);font-size:11px;overflow:hidden}
.queue-image img{width:100%;height:100%;object-fit:contain}.queue-head{display:flex;justify-content:space-between;gap:10px}.queue-head h3{margin:0;font-size:15px}.queue-head p{margin:3px 0 0;color:var(--text-dim);font-size:12px}
.queue-metrics{display:grid;grid-template-columns:repeat(5,minmax(90px,1fr));gap:10px;margin:12px 0}.queue-metrics span{font-size:11px;color:var(--text-dim);display:flex;flex-direction:column}.queue-metrics strong{font-size:14px;color:var(--text);margin-top:2px}
.queue-pills{display:flex;gap:7px;flex-wrap:wrap}.last-check,.fine-print{color:var(--text-dim);font-size:11.5px;margin:10px 0 0}.btn.small,button.small{padding:7px 10px;font-size:12px}.btn.ghost,button.secondary{background:var(--surface-sunken);color:var(--text);border:1px solid var(--panel-border);box-shadow:none}
details>summary{cursor:pointer;font-weight:750}.rg-form{display:grid;gap:11px;margin-top:14px}.settings-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.rg-form label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--text-dim)}
.rg-form input,.rg-form select,.rg-form textarea,.inline-form input,.inline-form select,.inline-filter select,.trust-form select{width:100%;box-sizing:border-box}.rg-form .wide{grid-column:1/-1}.check{flex-direction:row!important;align-items:center!important}.check input{width:auto!important}.danger-check{grid-column:1/-1;color:var(--danger)!important}
.purchase-dialog{width:min(520px,calc(100vw - 24px));border:1px solid var(--panel-border);border-radius:16px;background:var(--panel);color:var(--text);padding:20px;box-shadow:0 24px 80px #0005}.purchase-dialog::backdrop{background:#0f172a99}.dialog-head{display:flex;justify-content:space-between;align-items:center}.dialog-head h2{margin:0}.icon-button{background:none;color:var(--text);font-size:25px;padding:2px 8px}.dialog-actions{justify-content:flex-end}
.purchase-success{display:flex;gap:14px;align-items:center;padding:17px;margin-bottom:16px;border:1px solid var(--success);background:var(--success-bg);border-radius:14px}.success-check{width:38px;height:38px;border-radius:50%;display:grid;place-items:center;background:var(--success);color:white;font-size:21px}.purchase-success h2{margin:3px 0}.purchase-success p{margin:0;color:var(--text-dim);font-size:12px}
.projection-row{display:grid;grid-template-columns:110px repeat(2,1fr);gap:14px;padding:10px 0;border-bottom:1px solid var(--panel-border)}.projection-row:last-of-type{border-bottom:0}.projection-row span{display:flex;flex-direction:column;color:var(--text-dim);font-size:11px}.projection-row strong{color:var(--text);font-size:14px;margin-top:2px}
.history-table{overflow-x:auto}.history-table small{display:block;color:var(--text-dim)}.history-table td{vertical-align:top}.text-button{background:none;color:var(--danger);padding:4px 0;font-size:11px}.inline-filter,.inline-form,.trust-form{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.inline-form{margin-top:14px}.inline-form input{width:auto;flex:1;min-width:130px}.trust-form{min-width:270px}.milestone-history{padding-left:18px;line-height:1.8}.check-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:14px}.queue-filter{display:flex;gap:7px}.queue-filter button{padding:7px 10px;font-size:11px}
@media(max-width:700px){.guild-hero{align-items:flex-start;flex-direction:column}.hero-side{width:100%;justify-content:space-between}.milestone-grid,.summary-grid,.settings-grid,.check-grid{grid-template-columns:1fr}.recommend-grid{grid-template-columns:1fr}.queue-card{grid-template-columns:64px 1fr}.queue-image{width:64px;height:82px}.queue-metrics{grid-template-columns:repeat(2,1fr)}.section-title{align-items:flex-start;flex-direction:column}.projection-row{grid-template-columns:1fr}.history-table{overflow:visible}.history-table table,.history-table tbody,.history-table tr,.history-table td{display:block;width:100%;box-sizing:border-box}.history-table thead{display:none}.history-table tr{padding:10px 0;border-bottom:1px solid var(--panel-border)}.history-table td{display:flex;justify-content:space-between;gap:12px;border:0;padding:4px 0;text-align:right}.history-table td::before{content:attr(data-label);color:var(--text-dim);font-size:11px;text-align:left}.history-table td[colspan]{display:block;text-align:left}.history-table td[colspan]::before{display:none}.trust-form{min-width:0;justify-content:flex-end}.purchase-dialog{max-height:90vh;overflow:auto}.guild-hero h1{font-size:32px}}
</style>"""

_SCRIPT = """<script>
(function(){
  document.querySelectorAll('.purchase-dialog').forEach(function(dialog){
    var unit=dialog.querySelector('[data-unit-price]'), qty=dialog.querySelector('[data-purchase-qty]'), eligible=dialog.querySelector('[data-eligible-spend]');
    if(!unit||!qty||!eligible)return;
    var touched=false; eligible.addEventListener('input',function(){touched=true;});
    function calculate(){if(touched)return;var total=(parseFloat(unit.value)||0)*(parseInt(qty.value,10)||0);eligible.value=total.toFixed(2);}
    unit.addEventListener('input',calculate);qty.addEventListener('input',calculate);
  });
  document.querySelectorAll('[data-filter]').forEach(function(button){button.addEventListener('click',function(){
    var wanted=button.getAttribute('data-filter');document.querySelectorAll('#buy-queue .queue-card').forEach(function(card){card.hidden=wanted!=='all'&&card.getAttribute('data-status')!==wanted;});
  });});
})();
</script>"""
