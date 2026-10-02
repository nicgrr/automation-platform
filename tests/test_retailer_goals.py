from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import get_session
from automation_control.models import (
    InventoryItem,
    InventoryStatus,
    PurchaseLot,
    PurchaseLotItem,
    PurchaseLotStatus,
    Retailer,
    RetailerBudget,
    RetailerFundEntry,
    RetailerFundEntryKind,
    RetailerMilestone,
    RetailerPremiumConfig,
    RetailerPriceSnapshot,
    RetailerProduct,
    RetailerProductStatus,
    RetailerPurchase,
    RetailerPurchaseStatus,
    RetailerStockStatus,
    RetailerTrustStatus,
    ShopListing,
    ShopListingStatus,
)
from automation_control.retailer_goals_service import (
    accrue_weekly_fund,
    classify_premium,
    eligible_spend,
    ensure_gameology_program,
    fund_totals,
    latest_product_prices,
    money,
    premium_metrics,
    product_metrics,
    projection,
    record_purchase,
    refund_purchase,
    update_milestones,
)
from automation_control.retailer_goals import retailer_goal_dashboard


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'retailer-goals.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def program(session):
    return ensure_gameology_program(session)


def _budget(session, program):
    return session.scalar(select(RetailerBudget).where(RetailerBudget.program_id == program.id))


def _config(session, program):
    return session.scalar(select(RetailerPremiumConfig).where(RetailerPremiumConfig.program_id == program.id))


def _product(session, program, name="Test Booster Box"):
    product = RetailerProduct(
        program_id=program.id, name=name, tcg="pokemon", product_type="booster_box",
        set_name="Test Set", priority=4, quantity_wanted=1,
        status=RetailerProductStatus.HIGH_PRIORITY, stock_status=RetailerStockStatus.IN_STOCK,
    )
    session.add(product)
    session.commit()
    return product


def _snapshot(session, product, retailer, price, stock=RetailerStockStatus.IN_STOCK):
    row = RetailerPriceSnapshot(
        product_id=product.id, retailer_id=retailer.id, price=money(price),
        stock_status=stock, source="test", observed_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    return row


def test_money_rounding_never_uses_binary_float():
    assert money("10.005") == Decimal("10.01")
    assert money(Decimal("299.949")) == Decimal("299.95")


def test_historical_spend_is_a_starting_balance_and_purchases_add_to_it(session, program):
    program.historical_eligible_spend = Decimal("680.00")
    product = _product(session, program)

    result = record_purchase(
        session, program=program, product=product, unit_price=Decimal("299.95"),
        quantity=2, purchase_date=date.today(), eligible_amount=Decimal("599.90"),
    )

    assert result["before"] == Decimal("680.00")
    assert result["after"] == Decimal("1279.90")
    assert program.historical_eligible_spend == Decimal("680.00")
    assert eligible_spend(session, program) == Decimal("1279.90")


def test_purchase_quantity_drives_fund_and_eligible_totals(session, program):
    product = _product(session, program)
    result = record_purchase(
        session, program=program, product=product, unit_price=Decimal("12.345"),
        quantity=3, purchase_date=date.today(), eligible_amount=money(Decimal("12.345") * 3),
        shipping=Decimal("9.95"),
    )

    assert result["purchase"].purchase_unit_price == Decimal("12.35")
    assert result["purchase"].eligible_spend == Decimal("37.04")
    assert result["purchase"].shipping == Decimal("9.95")
    assert result["fund"]["available"] == Decimal("-37.05")


def test_weekly_fund_catches_up_once_and_pause_stops_new_entries(session, program):
    budget = _budget(session, program)
    budget.weekly_contribution = Decimal("75.00")
    budget.next_contribution_date = date.today() - timedelta(days=14)

    assert accrue_weekly_fund(session, budget) == 3
    session.commit()
    assert fund_totals(session, budget)["available"] == Decimal("225.00")
    assert accrue_weekly_fund(session, budget) == 0

    budget.next_contribution_date = date.today()
    budget.is_paused = True
    assert accrue_weekly_fund(session, budget) == 0
    assert fund_totals(session, budget)["available"] == Decimal("225.00")


def test_premium_calculation_and_configurable_classification(session, program):
    premium, pct = premium_metrics(Decimal("299.95"), Decimal("289.00"))
    assert premium == Decimal("10.95")
    assert pct == Decimal("3.79")

    config = _config(session, program)
    assert classify_premium(Decimal("0"), config)[0] == "EXCELLENT"
    assert classify_premium(Decimal("5"), config)[0] == "EXCELLENT GUILD BUY"
    assert classify_premium(Decimal("10"), config)[0] == "ACCEPTABLE GUILD PREMIUM"
    assert classify_premium(Decimal("15"), config)[0] == "BORDERLINE"
    assert classify_premium(Decimal("15.01"), config)[0] == "BUY ELSEWHERE / WAIT"


def test_only_trusted_best_price_eligible_retailers_set_best_au(session, program):
    product = _product(session, program)
    gameology = session.get(Retailer, program.retailer_id)
    popcultcha = session.scalar(select(Retailer).where(Retailer.name == "Popcultcha"))
    big_w = session.scalar(select(Retailer).where(Retailer.name == "BIG W"))
    popcultcha.trust_status = RetailerTrustStatus.TRUSTED
    popcultcha.best_price_eligible = True
    _snapshot(session, product, gameology, "319.95")
    _snapshot(session, product, popcultcha, "289.00")
    _snapshot(session, product, big_w, "250.00")  # cheaper but still unverified

    prices = latest_product_prices(session, product, program.retailer_id)

    assert prices["best"].price == Decimal("289.00")
    assert prices["best_retailer"].name == "Popcultcha"


def test_affordability_and_weeks_until_affordable(session, program):
    product = _product(session, program)
    gameology = session.get(Retailer, program.retailer_id)
    _snapshot(session, product, gameology, "299.95")
    budget = _budget(session, program)
    budget.weekly_contribution = Decimal("100.00")
    session.add(RetailerFundEntry(
        budget_id=budget.id, kind=RetailerFundEntryKind.OPENING_BALANCE,
        amount=Decimal("227.40"), entry_date=date.today(),
    ))
    session.commit()

    row = product_metrics(session, product, program, budget, _config(session, program))

    assert row["affordable"] is False
    assert row["shortfall"] == Decimal("72.55")
    assert row["weeks_until_affordable"] == 1


def test_projection_uses_existing_eligible_spend():
    result = projection(Decimal("5000"), Decimal("1427.85"), Decimal("100"), today=date(2026, 9, 24))
    assert result["remaining"] == Decimal("3572.15")
    assert result["weeks"] == 36
    assert result["date"] == date(2027, 6, 3)


def test_milestone_records_first_achievement_date(session, program):
    program.historical_eligible_spend = Decimal("1000.00")
    reached = datetime(2026, 11, 14, tzinfo=UTC)

    assert update_milestones(session, program, now=reached) == 1
    session.commit()
    early = session.scalar(select(RetailerMilestone).where(RetailerMilestone.target_amount == 1000))
    # SQLite returns timezone-aware columns naive; the stored wall time is
    # still UTC, matching the rest of EzBay's existing timestamp handling.
    assert early.achieved_at == reached.replace(tzinfo=None)

    program.historical_eligible_spend = Decimal("900.00")
    assert update_milestones(session, program, now=datetime(2026, 11, 20, tzinfo=UTC)) == 0
    assert early.achieved_at == reached.replace(tzinfo=None)


def test_purchase_automatically_creates_linked_lot_inventory_and_refund_reverses_it(session, program):
    product = next(iter(session.scalars(select(RetailerProduct).where(
        RetailerProduct.program_id == program.id
    ))))
    result = record_purchase(
        session, program=program, product=product, unit_price=Decimal("299.95"),
        quantity=2, purchase_date=date.today(), eligible_amount=Decimal("599.90"),
        shipping=Decimal("10.00"), order_number="GAME-123",
    )
    purchase = result["purchase"]
    inventory = session.get(InventoryItem, purchase.inventory_item_id)
    assert inventory.quantity == 2
    lot = session.get(PurchaseLot, purchase.purchase_lot_id)
    line = session.scalars(select(PurchaseLotItem)).one()
    assert inventory.allocated_cost_basis == Decimal("304.95")
    assert "Gameology" in inventory.notes
    assert purchase.inventory_added is True
    assert lot.status == PurchaseLotStatus.PURCHASED
    assert lot.external_order_number == "GAME-123"
    assert lot.purchase_price == Decimal("609.90")
    assert line.retailer_purchase_id == purchase.id
    assert line.catalog_item_id == product.catalog_item_id
    assert line.inventory_item_id == inventory.id

    refund_purchase(session, purchase)

    assert purchase.status == RetailerPurchaseStatus.REFUNDED
    assert inventory.quantity == 0
    assert inventory.status == InventoryStatus.RETURNED
    assert lot.status == PurchaseLotStatus.REFUNDED
    assert eligible_spend(session, program) == Decimal("0.00")
    assert fund_totals(session, _budget(session, program))["available"] == Decimal("0.00")


def test_refund_is_idempotently_rejected(session, program):
    product = _product(session, program)
    purchase = record_purchase(
        session, program=program, product=product, unit_price=Decimal("20"), quantity=1,
        purchase_date=date.today(), eligible_amount=Decimal("20"),
    )["purchase"]
    refund_purchase(session, purchase)
    with pytest.raises(ValueError, match="already"):
        refund_purchase(session, purchase)


def test_repurchase_reuses_and_reactivates_returned_holding(session, program):
    product = _product(session, program)
    first = record_purchase(
        session, program=program, product=product, unit_price=Decimal("20"), quantity=1,
        purchase_date=date.today(), eligible_amount=Decimal("20"),
    )["purchase"]
    inventory_id = first.inventory_item_id
    refund_purchase(session, first)

    second = record_purchase(
        session, program=program, product=product, unit_price=Decimal("22"), quantity=2,
        purchase_date=date.today(), eligible_amount=Decimal("44"),
    )["purchase"]

    inventory = session.get(InventoryItem, inventory_id)
    assert second.inventory_item_id == inventory_id
    assert inventory.quantity == 2
    assert inventory.status == InventoryStatus.AVAILABLE
    assert inventory.allocated_cost_basis == Decimal("22.00")
    assert session.query(InventoryItem).filter_by(catalog_item_id=product.catalog_item_id).count() == 1


def test_same_retailer_order_groups_lines_into_one_purchase_lot(session, program):
    first = _product(session, program, "First Product")
    second = _product(session, program, "Second Product")
    one = record_purchase(
        session, program=program, product=first, unit_price=Decimal("20"), quantity=1,
        purchase_date=date.today(), eligible_amount=Decimal("20"), order_number="ORDER-42",
    )["purchase"]
    two = record_purchase(
        session, program=program, product=second, unit_price=Decimal("30"), quantity=2,
        purchase_date=date.today(), eligible_amount=Decimal("60"), shipping=Decimal("5"),
        order_number="ORDER-42",
    )["purchase"]

    assert one.purchase_lot_id == two.purchase_lot_id
    lot = session.get(PurchaseLot, one.purchase_lot_id)
    assert lot.purchase_price == Decimal("85.00")
    assert session.query(PurchaseLot).count() == 1
    assert session.query(PurchaseLotItem).count() == 2

    refund_purchase(session, one)
    assert lot.status == PurchaseLotStatus.PURCHASED
    refund_purchase(session, two)
    assert lot.status == PurchaseLotStatus.REFUNDED


def test_refund_is_blocked_while_inventory_is_allocated_to_shop(session, program):
    product = _product(session, program)
    purchase = record_purchase(
        session, program=program, product=product, unit_price=Decimal("20"), quantity=1,
        purchase_date=date.today(), eligible_amount=Decimal("20"),
    )["purchase"]
    inventory = session.get(InventoryItem, purchase.inventory_item_id)
    session.add(ShopListing(
        catalog_item_id=inventory.catalog_item_id, inventory_item_id=inventory.id,
        title="Allocated product", price=Decimal("30"), quantity=1,
        status=ShopListingStatus.ACTIVE,
    ))
    session.commit()
    entries_before = session.query(RetailerFundEntry).count()

    with pytest.raises(ValueError, match="Withdraw"):
        refund_purchase(session, purchase)

    session.rollback()
    session.refresh(purchase)
    session.refresh(inventory)
    assert purchase.status == RetailerPurchaseStatus.RECORDED
    assert inventory.quantity == 1
    assert session.query(RetailerFundEntry).count() == entries_before


def test_retried_purchase_form_is_idempotent(client, session, program):
    product = _product(session, program)
    data = {
        "purchase_price": "20", "quantity": "1", "purchase_date": date.today().isoformat(),
        "eligible_spend": "20", "shipping": "0", "order_number": "IDEMPOTENT-1",
        "notes": "", "submission_key": "c20cb45a-d0ba-468d-a102-45f56e793cca",
    }

    first = client.post(
        f"/retailer-goals/products/{product.id}/purchase", data=data, follow_redirects=False,
    )
    second = client.post(
        f"/retailer-goals/products/{product.id}/purchase", data=data, follow_redirects=False,
    )

    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    assert session.query(RetailerPurchase).count() == 1
    assert session.query(PurchaseLot).count() == 1
    assert session.query(PurchaseLotItem).count() == 1
    assert session.query(RetailerFundEntry).filter(
        RetailerFundEntry.kind == RetailerFundEntryKind.PURCHASE
    ).count() == 1


def test_mobile_dashboard_renders_native_tracker_controls(session, program):
    response = retailer_goal_dashboard(
        program.slug, notice="", error="", purchased="",
        purchase_status="", purchase_sort="newest", user="nic", session=session,
    )
    html = response.body.decode()

    assert "Gameology Guild" in html
    assert "Early Access" in html
    assert "Priority Early Access / Guild Master" in html
    assert "What Should I Buy?" in html
    assert "Booster Box Opportunities" in html
    assert "Gameology Purchase History" in html
    assert "Current Historical Spend" in html
    assert "Trusted Australian retailers" in html
    assert "purchase-dialog" in html
    assert "@media(max-width:700px)" in html
