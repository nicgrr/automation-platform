"""Domain calculations and transactions for retailer loyalty programs.

All money stays as Decimal and is rounded to cents at the service boundary.
The module contains no HTTP or HTML concerns, which keeps the progress,
budget, premium, purchase and refund rules directly testable.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    InventoryStatus,
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
    SealedProduct,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
GAMEOLOGY_PROGRAM_SLUG = "gameology-guild"


def money(value: object, *, allow_negative: bool = True) -> Decimal:
    """Parse and round an accounting input without ever passing via float."""
    try:
        result = Decimal(str(value if value not in (None, "") else "0")).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Enter a valid dollar amount.") from exc
    if not allow_negative and result < 0:
        raise ValueError("Amount cannot be negative.")
    return result


def positive_int(value: object, *, default: int = 1, maximum: int = 10_000) -> int:
    try:
        parsed = int(str(value).strip()) if str(value).strip() else default
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid whole-number quantity.") from exc
    if parsed < 1 or parsed > maximum:
        raise ValueError(f"Quantity must be between 1 and {maximum}.")
    return parsed


def next_weekday(day: date, weekday: int) -> date:
    days = (weekday - day.weekday()) % 7
    return day + timedelta(days=days or 7)


def _slug(text: str) -> str:
    return "-".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


def _retailer(session: Session, name: str, *, domain: str | None = None,
              website: str | None = None, trusted: bool = False) -> Retailer:
    row = session.scalar(select(Retailer).where(func.lower(Retailer.name) == name.lower()))
    if row is None:
        row = Retailer(
            id=str(uuid.uuid4()), name=name, domain=domain, website=website,
            trust_status=RetailerTrustStatus.TRUSTED if trusted else RetailerTrustStatus.UNVERIFIED,
            best_price_eligible=trusted, last_reviewed=date.today() if trusted else None,
        )
        session.add(row)
        session.flush()
    return row


def ensure_catalog_product(session: Session, *, name: str, tcg: str, product_type: str,
                           set_name: str | None = None, image_url: str | None = None) -> CatalogItem:
    existing = session.scalar(
        select(CatalogItem)
        .join(SealedProduct, SealedProduct.catalog_item_id == CatalogItem.id)
        .where(func.lower(CatalogItem.name) == name.lower())
        .limit(1)
    )
    if existing is not None:
        if image_url and not existing.image_url:
            existing.image_url = image_url
        return existing

    set_id = None
    if set_name:
        card_set = session.scalar(select(CardSet).where(func.lower(CardSet.name) == set_name.lower()).limit(1))
        set_id = card_set.id if card_set else None
    item = CatalogItem(
        id=str(uuid.uuid4()), item_type=CatalogItemType.SEALED_PRODUCT,
        name=name, image_url=image_url,
    )
    session.add(item)
    session.add(SealedProduct(
        catalog_item_id=item.id, brand="Pokemon" if tcg == "pokemon" else ("Bandai" if tcg == "one_piece" else None),
        game=tcg, set_id=set_id, product_type=product_type,
    ))
    session.flush()
    return item


def ensure_gameology_program(session: Session) -> RetailerProgram:
    """Idempotently seed the first program and editable starter queue."""
    gameology = _retailer(
        session, "Gameology", domain="gameology.com.au", website="https://www.gameology.com.au", trusted=True,
    )
    # Seed comparison retailers conservatively. They are visible, but cannot
    # set Best Reputable AU until the operator explicitly marks them trusted.
    for name, domain in (
        ("BIG W", "bigw.com.au"), ("Kmart", "kmart.com.au"),
        ("EB Games", "ebgames.com.au"), ("JB Hi-Fi", "jbhifi.com.au"),
        ("The Gamesmen", "gamesmen.com.au"), ("Hobbyco", "hobbyco.com.au"),
        ("Popcultcha", "popcultcha.com.au"), ("Good Games", "goodgames.com.au"),
    ):
        _retailer(session, name, domain=domain)

    program = session.scalar(select(RetailerProgram).where(RetailerProgram.slug == GAMEOLOGY_PROGRAM_SLUG))
    if program is None:
        program = RetailerProgram(
            id=str(uuid.uuid4()), retailer_id=gameology.id, slug=GAMEOLOGY_PROGRAM_SLUG,
            name="Gameology Guild", historical_eligible_spend=ZERO,
            eligible_spend_notes="Shipping is excluded unless manually entered as eligible spend.",
        )
        session.add(program)
        session.flush()

    milestones = [
        ("Early Access", Decimal("1000.00"), 10),
        ("Priority Early Access / Guild Master", Decimal("5000.00"), 50),
    ]
    for label, target, order in milestones:
        exists = session.scalar(select(RetailerMilestone.id).where(
            RetailerMilestone.program_id == program.id,
            RetailerMilestone.target_amount == target,
        ))
        if not exists:
            session.add(RetailerMilestone(
                id=str(uuid.uuid4()), program_id=program.id, label=label,
                target_amount=target, sort_order=order,
            ))

    budget = session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == program.id))
    if budget is None:
        budget = RetailerBudget(
            id=str(uuid.uuid4()), program_id=program.id,
            weekly_contribution=Decimal("100.00"), contribution_weekday=4,
            next_contribution_date=next_weekday(date.today(), 4),
        )
        session.add(budget)

    if session.scalar(select(RetailerPremiumConfig.id).where(RetailerPremiumConfig.program_id == program.id)) is None:
        session.add(RetailerPremiumConfig(id=str(uuid.uuid4()), program_id=program.id))
    if session.scalar(select(RetailerNotificationPreference.id).where(
        RetailerNotificationPreference.program_id == program.id
    )) is None:
        session.add(RetailerNotificationPreference(id=str(uuid.uuid4()), program_id=program.id))

    starters = (
        ("One Piece OP-18 Booster Box", "one_piece", "booster_box", "OP-18"),
        ("One Piece EB-05 Booster Box", "one_piece", "booster_box", "EB-05"),
        ("Pokémon Mega Evolution — Delta Reign Booster Box", "pokemon", "booster_box", "Mega Evolution — Delta Reign"),
    )
    for name, tcg, product_type, set_name in starters:
        exists = session.scalar(select(RetailerProduct.id).where(
            RetailerProduct.program_id == program.id,
            func.lower(RetailerProduct.name) == name.lower(),
        ))
        if exists:
            continue
        catalog = ensure_catalog_product(
            session, name=name, tcg=tcg, product_type=product_type, set_name=set_name,
        )
        session.add(RetailerProduct(
            id=str(uuid.uuid4()), program_id=program.id, catalog_item_id=catalog.id,
            name=name, tcg=tcg, product_type=product_type, set_name=set_name,
            status=RetailerProductStatus.WATCH, stock_status=RetailerStockStatus.UNKNOWN,
            priority=3, quantity_wanted=1,
        ))
    session.commit()
    return program


def accrue_weekly_fund(session: Session, budget: RetailerBudget, *, today: date | None = None) -> int:
    """Add every due weekly contribution once; paused weeks never catch up."""
    today = today or date.today()
    if budget.is_paused or budget.weekly_contribution <= 0:
        return 0
    added = 0
    while budget.next_contribution_date <= today:
        due = budget.next_contribution_date
        key = f"weekly:{budget.id}:{due.isoformat()}"
        if session.scalar(select(RetailerFundEntry.id).where(RetailerFundEntry.dedupe_key == key)) is None:
            session.add(RetailerFundEntry(
                id=str(uuid.uuid4()), budget_id=budget.id,
                kind=RetailerFundEntryKind.CONTRIBUTION,
                amount=money(budget.weekly_contribution), entry_date=due,
                dedupe_key=key, notes="Scheduled weekly contribution",
            ))
            added += 1
        budget.next_contribution_date = due + timedelta(days=7)
    return added


def fund_totals(session: Session, budget: RetailerBudget) -> dict[str, Decimal]:
    entries = session.scalars(select(RetailerFundEntry).where(RetailerFundEntry.budget_id == budget.id)).all()
    available = money(sum((entry.amount for entry in entries), ZERO))
    contributed = money(sum((entry.amount for entry in entries if entry.kind in {
        RetailerFundEntryKind.OPENING_BALANCE,
        RetailerFundEntryKind.CONTRIBUTION,
        RetailerFundEntryKind.ADJUSTMENT,
    } and entry.amount > 0), ZERO))
    purchase_spend = -sum((entry.amount for entry in entries if entry.kind == RetailerFundEntryKind.PURCHASE), ZERO)
    refunds = sum((entry.amount for entry in entries if entry.kind == RetailerFundEntryKind.REFUND), ZERO)
    return {
        "available": available,
        "contributed": contributed,
        "spent": money(max(ZERO, purchase_spend - refunds)),
    }


def eligible_spend(session: Session, program: RetailerProgram) -> Decimal:
    purchases = session.scalars(select(RetailerPurchase).where(
        RetailerPurchase.program_id == program.id,
        RetailerPurchase.status == RetailerPurchaseStatus.RECORDED,
    )).all()
    return money(program.historical_eligible_spend + sum((p.eligible_spend for p in purchases), ZERO))


def update_milestones(session: Session, program: RetailerProgram, *, now: datetime | None = None) -> int:
    total = eligible_spend(session, program)
    achieved = 0
    for milestone in session.scalars(select(RetailerMilestone).where(
        RetailerMilestone.program_id == program.id,
    )).all():
        if milestone.achieved_at is None and total >= milestone.target_amount:
            milestone.achieved_at = now or datetime.now(UTC)
            achieved += 1
    return achieved


def premium_metrics(gameology_price: Decimal | None, best_price: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    if gameology_price is None or best_price is None or best_price <= 0:
        return None, None
    premium = money(gameology_price - best_price)
    pct = ((premium / best_price) * Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    return premium, pct


def classify_premium(pct: Decimal | None, config: RetailerPremiumConfig) -> tuple[str, str]:
    if pct is None:
        return "NO COMPARISON", "neutral"
    if pct <= config.excellent_max_pct:
        return "EXCELLENT", "ok"
    if pct <= config.excellent_guild_max_pct:
        return "EXCELLENT GUILD BUY", "ok"
    if pct <= config.acceptable_max_pct:
        return "ACCEPTABLE GUILD PREMIUM", "ok"
    if pct <= config.borderline_max_pct:
        return "BORDERLINE", "warn"
    return "BUY ELSEWHERE / WAIT", "bad"


def latest_product_prices(session: Session, product: RetailerProduct,
                          program_retailer_id: str) -> dict:
    rows = session.scalars(
        select(RetailerPriceSnapshot)
        .where(RetailerPriceSnapshot.product_id == product.id)
        .order_by(RetailerPriceSnapshot.observed_at.desc(), RetailerPriceSnapshot.id.desc())
    ).all()
    latest: dict[str, RetailerPriceSnapshot] = {}
    for row in rows:
        latest.setdefault(row.retailer_id, row)

    retailers = {
        row.id: row for row in session.scalars(select(Retailer).where(Retailer.id.in_(list(latest) or [""]))).all()
    }
    gameology = latest.get(program_retailer_id)
    trusted_competitors = [
        snap for retailer_id, snap in latest.items()
        if retailer_id != program_retailer_id
        and retailers.get(retailer_id)
        and retailers[retailer_id].trust_status == RetailerTrustStatus.TRUSTED
        and retailers[retailer_id].best_price_eligible
        and snap.stock_status != RetailerStockStatus.OUT_OF_STOCK
    ]
    best = min(trusted_competitors, key=lambda row: row.price, default=None)
    return {
        "gameology": gameology,
        "best": best,
        "best_retailer": retailers.get(best.retailer_id) if best else None,
        "latest": latest,
        "retailers": retailers,
        "last_checked": max((row.observed_at for row in latest.values()), default=None),
    }


def product_metrics(session: Session, product: RetailerProduct, program: RetailerProgram,
                    budget: RetailerBudget, premium_config: RetailerPremiumConfig,
                    *, fund: Decimal | None = None, spend: Decimal | None = None) -> dict:
    prices = latest_product_prices(session, product, program.retailer_id)
    gameology_price = prices["gameology"].price if prices["gameology"] else None
    best_price = prices["best"].price if prices["best"] else None
    premium, premium_pct = premium_metrics(gameology_price, best_price)
    classification, classification_kind = classify_premium(premium_pct, premium_config)
    fund = fund if fund is not None else fund_totals(session, budget)["available"]
    wanted_total = money(gameology_price * product.quantity_wanted) if gameology_price is not None else None
    shortfall = money(max(ZERO, wanted_total - fund)) if wanted_total is not None else None
    if shortfall is None or shortfall <= 0:
        weeks = 0 if shortfall is not None else None
    elif budget.weekly_contribution > 0 and not budget.is_paused:
        weeks = int(math.ceil(shortfall / budget.weekly_contribution))
    else:
        weeks = None

    score = product.priority * 20
    score += {"ok": 35, "warn": 5, "bad": -35, "neutral": 0}[classification_kind]
    score += {
        RetailerStockStatus.IN_STOCK: 25,
        RetailerStockStatus.PREORDER: 15,
        RetailerStockStatus.UNKNOWN: 0,
        RetailerStockStatus.OUT_OF_STOCK: -40,
    }[product.stock_status]
    if shortfall is not None and shortfall <= 0:
        score += 25
    elif weeks is not None:
        score -= min(25, weeks * 3)
    if gameology_price is not None and product.maximum_acceptable_price is not None:
        score += 10 if gameology_price <= product.maximum_acceptable_price else -50
    if budget.per_product_maximum is not None and wanted_total is not None and wanted_total > budget.per_product_maximum:
        score -= 35
    month_start = date.today().replace(day=1)
    month_purchases = session.scalars(select(RetailerPurchase).where(
        RetailerPurchase.program_id == program.id,
        RetailerPurchase.status == RetailerPurchaseStatus.RECORDED,
        RetailerPurchase.purchase_date >= month_start,
    )).all()
    month_spend = money(sum((p.purchase_unit_price * p.quantity for p in month_purchases), ZERO))
    monthly_remaining = (
        money(max(ZERO, budget.monthly_maximum - month_spend))
        if budget.monthly_maximum is not None else None
    )
    budget_warnings: list[str] = []
    if wanted_total is not None and budget.per_product_maximum is not None and wanted_total > budget.per_product_maximum:
        budget_warnings.append("Over per-product maximum")
    if wanted_total is not None and monthly_remaining is not None and wanted_total > monthly_remaining:
        budget_warnings.append("Over remaining monthly budget")
        score -= 50
    if product.status in {RetailerProductStatus.HIGH_PRIORITY, RetailerProductStatus.BUY}:
        score += 20
    if product.status in {
        RetailerProductStatus.SKIP, RetailerProductStatus.SOLD_OUT,
        RetailerProductStatus.PRICE_TOO_HIGH, RetailerProductStatus.PURCHASED,
    }:
        score -= 200

    return {
        "product": product, **prices,
        "gameology_price": gameology_price, "best_price": best_price,
        "premium": premium, "premium_pct": premium_pct,
        "classification": classification, "classification_kind": classification_kind,
        "wanted_total": wanted_total, "shortfall": shortfall, "weeks_until_affordable": weeks,
        "affordable": wanted_total is not None and wanted_total <= fund,
        "score": score, "last_checked": prices["last_checked"],
        "budget_warnings": budget_warnings, "monthly_remaining": monthly_remaining,
        "progress_after": money((spend or ZERO) + (wanted_total or ZERO)),
        "fund_after": money(fund - wanted_total) if wanted_total is not None else None,
    }


def projection(target: Decimal, current: Decimal, weekly: Decimal, *, today: date | None = None) -> dict:
    remaining = money(max(ZERO, target - current))
    if remaining <= 0:
        return {"remaining": remaining, "weeks": 0, "date": today or date.today()}
    if weekly <= 0:
        return {"remaining": remaining, "weeks": None, "date": None}
    weeks = int(math.ceil(remaining / weekly))
    start = today or date.today()
    return {"remaining": remaining, "weeks": weeks, "date": start + timedelta(weeks=weeks)}


def program_state(session: Session, program: RetailerProgram, *, today: date | None = None) -> dict:
    budget = session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == program.id))
    config = session.scalar(select(RetailerPremiumConfig).where(RetailerPremiumConfig.program_id == program.id))
    if budget is None or config is None:
        raise ValueError("Retailer program configuration is incomplete.")
    accrue_weekly_fund(session, budget, today=today)
    total_spend = eligible_spend(session, program)
    update_milestones(session, program)
    session.commit()
    fund = fund_totals(session, budget)
    milestones = session.scalars(select(RetailerMilestone).where(
        RetailerMilestone.program_id == program.id,
    ).order_by(RetailerMilestone.sort_order, RetailerMilestone.target_amount)).all()
    products = session.scalars(select(RetailerProduct).where(
        RetailerProduct.program_id == program.id,
    ).order_by(RetailerProduct.priority.desc(), RetailerProduct.date_added.desc())).all()
    product_rows = [product_metrics(
        session, product, program, budget, config, fund=fund["available"], spend=total_spend,
    ) for product in products]
    recommendation = max(product_rows, key=lambda row: row["score"], default=None)
    if recommendation and recommendation["score"] < 0:
        recommendation = None

    purchases = session.scalars(select(RetailerPurchase).where(
        RetailerPurchase.program_id == program.id,
    ).order_by(RetailerPurchase.purchase_date.desc(), RetailerPurchase.recorded_at.desc())).all()
    active = [p for p in purchases if p.status == RetailerPurchaseStatus.RECORDED]
    recorded_spend = money(sum((p.purchase_unit_price * p.quantity for p in active), ZERO))
    equivalent = money(sum((p.market_unit_price * p.quantity for p in active if p.market_unit_price is not None), ZERO))
    premium_paid = money(sum((p.guild_premium_paid or ZERO for p in active), ZERO))
    effective_pct = ((premium_paid / equivalent) * Decimal("100")).quantize(CENT) if equivalent > 0 else None

    month_start = (today or date.today()).replace(day=1)
    month_spend = money(sum((p.purchase_unit_price * p.quantity for p in active if p.purchase_date >= month_start), ZERO))
    return {
        "program": program, "budget": budget, "premium_config": config,
        "spend": total_spend, "fund": fund, "milestones": milestones,
        "products": product_rows, "recommendation": recommendation,
        "purchases": purchases, "recorded_spend": recorded_spend,
        "equivalent_cost": equivalent, "premium_paid": premium_paid,
        "effective_premium_pct": effective_pct, "month_spend": month_spend,
    }


def _inventory_for_purchase(session: Session, purchase: RetailerPurchase,
                            product: RetailerProduct, retailer: Retailer) -> InventoryItem:
    if not product.catalog_item_id:
        catalog = ensure_catalog_product(
            session, name=product.name, tcg=product.tcg, product_type=product.product_type,
            set_name=product.set_name, image_url=product.image_url,
        )
        product.catalog_item_id = catalog.id

    item = session.scalar(select(InventoryItem).where(
        InventoryItem.catalog_item_id == product.catalog_item_id,
        InventoryItem.card_id.is_(None),
        InventoryItem.condition == "Sealed",
        InventoryItem.status == InventoryStatus.AVAILABLE,
    ).order_by(InventoryItem.added_at).limit(1))
    note = f"Purchased from {retailer.name} on {purchase.purchase_date.isoformat()}"
    if purchase.order_number:
        note += f" | order {purchase.order_number}"
    note += f" | retailer purchase {purchase.id}"
    if item is None:
        item = InventoryItem(
            id=str(uuid.uuid4()), card_id=None, catalog_item_id=product.catalog_item_id,
            variant=CardVariant.NORMAL, condition="Sealed", quantity=purchase.quantity,
            allocated_cost_basis=money(purchase.purchase_unit_price),
            status=InventoryStatus.AVAILABLE, notes=note,
            added_at=datetime.combine(purchase.purchase_date, datetime.min.time(), tzinfo=UTC),
        )
        session.add(item)
    else:
        old_qty = item.quantity
        old_basis = item.allocated_cost_basis or ZERO
        combined_cost = (old_basis * old_qty) + (purchase.purchase_unit_price * purchase.quantity)
        item.quantity += purchase.quantity
        item.allocated_cost_basis = money(combined_cost / item.quantity)
        item.notes = (item.notes + "\n" if item.notes else "") + note
    session.flush()
    return item


def record_purchase(session: Session, *, program: RetailerProgram, product: RetailerProduct,
                    unit_price: Decimal, quantity: int, purchase_date: date,
                    eligible_amount: Decimal, shipping: Decimal = ZERO,
                    order_number: str | None = None, notes: str | None = None,
                    add_to_inventory: bool = False) -> dict:
    budget = session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == program.id))
    if budget is None:
        raise ValueError("Retailer budget is missing.")
    accrue_weekly_fund(session, budget, today=purchase_date)
    retailer = session.get(Retailer, program.retailer_id)
    if retailer is None:
        raise ValueError("Program retailer is missing.")
    prices = latest_product_prices(session, product, program.retailer_id)
    market = prices["best"].price if prices["best"] else None
    premium_paid = money((unit_price - market) * quantity) if market is not None else None
    purchase = RetailerPurchase(
        id=str(uuid.uuid4()), program_id=program.id, product_id=product.id,
        retailer_id=program.retailer_id, purchase_unit_price=money(unit_price, allow_negative=False),
        quantity=positive_int(quantity), eligible_spend=money(eligible_amount, allow_negative=False),
        shipping=money(shipping, allow_negative=False), market_unit_price=market,
        guild_premium_paid=premium_paid, purchase_date=purchase_date,
        order_number=(order_number or "").strip() or None,
        notes=(notes or "").strip() or None,
    )
    session.add(purchase)
    session.flush()
    total_cost = money(purchase.purchase_unit_price * purchase.quantity)
    session.add(RetailerFundEntry(
        id=str(uuid.uuid4()), budget_id=budget.id, purchase_id=purchase.id,
        kind=RetailerFundEntryKind.PURCHASE, amount=-total_cost,
        entry_date=purchase_date, dedupe_key=f"purchase:{purchase.id}", notes=product.name,
    ))
    if add_to_inventory:
        inventory = _inventory_for_purchase(session, purchase, product, retailer)
        purchase.inventory_added = True
        purchase.inventory_item_id = inventory.id
    product.status = RetailerProductStatus.PURCHASED
    before = money(eligible_spend(session, program) - purchase.eligible_spend)
    after = eligible_spend(session, program)
    update_milestones(session, program)
    session.commit()
    return {
        "purchase": purchase, "before": before, "after": after,
        "remaining": money(max(ZERO, Decimal("5000") - after)),
        "fund": fund_totals(session, budget), "premium": premium_paid,
    }


def refund_purchase(session: Session, purchase: RetailerPurchase) -> None:
    if purchase.status != RetailerPurchaseStatus.RECORDED:
        raise ValueError("This purchase has already been refunded or removed.")
    budget = session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == purchase.program_id))
    if budget is None:
        raise ValueError("Retailer budget is missing.")
    total = money(purchase.purchase_unit_price * purchase.quantity)
    session.add(RetailerFundEntry(
        id=str(uuid.uuid4()), budget_id=budget.id, purchase_id=purchase.id,
        kind=RetailerFundEntryKind.REFUND, amount=total, entry_date=date.today(),
        dedupe_key=f"refund:{purchase.id}", notes="Purchase refunded / removed",
    ))
    if purchase.inventory_added and purchase.inventory_item_id:
        inventory = session.get(InventoryItem, purchase.inventory_item_id)
        if inventory is not None:
            old_qty = inventory.quantity
            removed = min(old_qty, purchase.quantity)
            inventory.quantity = old_qty - removed
            if inventory.quantity > 0 and inventory.allocated_cost_basis is not None:
                remaining_cost = (inventory.allocated_cost_basis * old_qty) - (purchase.purchase_unit_price * removed)
                inventory.allocated_cost_basis = money(max(ZERO, remaining_cost) / inventory.quantity)
    purchase.status = RetailerPurchaseStatus.REFUNDED
    purchase.refunded_at = datetime.now(UTC)
    product = session.get(RetailerProduct, purchase.product_id)
    if product is not None:
        other = session.scalar(select(RetailerPurchase.id).where(
            RetailerPurchase.product_id == product.id,
            RetailerPurchase.status == RetailerPurchaseStatus.RECORDED,
            RetailerPurchase.id != purchase.id,
        ).limit(1))
        if other is None:
            product.status = RetailerProductStatus.WATCH
    session.commit()
