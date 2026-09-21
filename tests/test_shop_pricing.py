from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import ShippingZone, ShopCostConfig
from automation_control.shop_pricing import default_costs, price_floor


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def config(**overrides) -> ShopCostConfig:
    values = dict(
        label="Test", postage_cost_au=9.95, postage_cost_intl=29.95, packaging_cost=0.50,
        payment_pct=1.75, payment_fixed=0.30, target_margin_pct=25.0,
    )
    values.update(overrides)
    return ShopCostConfig(**values)


def test_charging_postage_lowers_the_break_even():
    """The whole reason a flat $12 floor was wrong: postage the customer
    pays is revenue, so who pays it moves break-even by an order of
    magnitude on cheap items."""
    paid = price_floor(config(), shipping_charged=Decimal("9.95"))
    free = price_floor(config(), shipping_charged=Decimal("0"))
    assert paid.break_even < free.break_even
    assert free.break_even - paid.break_even > Decimal("9")


def test_break_even_actually_breaks_even():
    floor = price_floor(config(), shipping_charged=Decimal("9.95"))
    # profit at break-even is zero to within the rounding-up of a cent
    profit = floor.profit_at(floor.break_even)
    assert Decimal("0") <= profit <= Decimal("0.02"), profit


def test_floor_is_never_below_break_even():
    """Rounds up on purpose -- a floor rounded down sits fractionally under
    break-even, which is the one direction that must never happen."""
    for pct in [0, 5, 12.5, 25, 100]:
        floor = price_floor(config(), shipping_charged=Decimal("3.33"), margin_pct=pct)
        assert floor.floor >= floor.break_even, pct
        assert floor.profit_at(floor.floor) >= 0


def test_payment_pct_override_replaces_not_stacks_with_configs_own_rate():
    """eBay's ~13% final value fee already bundles payment processing --
    build_bulk_lots.py passes it here in place of the website's own ~1.75%
    Stripe rate for anything sold through eBay rather than the website's
    own checkout, never on top of it."""
    website = price_floor(config(payment_pct=1.75), shipping_charged=Decimal("9.95"))
    ebay = price_floor(config(payment_pct=1.75), shipping_charged=Decimal("9.95"), payment_pct=Decimal("13"))
    assert ebay.payment_pct == Decimal("13")
    assert ebay.break_even > website.break_even  # a bigger cut means a higher floor
    assert ebay.floor > website.floor


def test_break_even_never_goes_negative():
    # heavily subsidised postage shouldn't produce a negative floor that
    # would let something be priced at nothing
    floor = price_floor(config(), shipping_charged=Decimal("50.00"))
    assert floor.break_even == Decimal("0")
    assert floor.floor == Decimal("0")


def test_international_uses_the_international_postage_cost():
    au = price_floor(config(), zone=ShippingZone.AU)
    intl = price_floor(config(), zone=ShippingZone.INTERNATIONAL)
    assert intl.break_even > au.break_even


def test_stock_cost_is_optional_and_flagged():
    unknown = price_floor(config())
    known = price_floor(config(), stock_cost=Decimal("4.00"))
    assert unknown.stock_cost_known is False
    assert known.stock_cost_known is True
    assert known.break_even > unknown.break_even


def test_profit_and_margin_at_a_real_price():
    floor = price_floor(config(), shipping_charged=Decimal("9.95"))
    # revenue 21.95, fees 1.75% + 0.30 = 0.684, costs 10.45 -> 10.82
    assert floor.profit_at(Decimal("12.00")) == Decimal("10.82")
    assert floor.margin_at(Decimal("12.00")) == Decimal("90.17")
    assert floor.clears_floor(Decimal("12.00"))
    assert not floor.clears_floor(Decimal("0.50"))


def test_margin_at_zero_price_is_undefined_not_a_crash():
    assert price_floor(config()).margin_at(Decimal("0")) is None


def test_a_payment_fee_of_100_percent_is_refused():
    with pytest.raises(ValueError):
        price_floor(config(payment_pct=100))


def test_default_costs_falls_back_when_nothing_is_configured(session):
    # pricing must never fail because a config row is missing
    assert default_costs(session).postage_cost_au > 0


def test_default_costs_prefers_the_default_row(session):
    session.add(config(label="Other", is_default=False, postage_cost_au=1.00))
    session.add(config(label="Main", is_default=True, postage_cost_au=8.00))
    session.commit()
    assert default_costs(session).label == "Main"


def test_shop_pricing_page_lists_items_with_a_verdict(session):
    from fastapi.testclient import TestClient

    from automation_control.api import app
    from automation_control.auth import require_dashboard_user
    from automation_control.database import get_session
    from automation_control.models import BulkLot, ShopListingStatus

    session.add(config(is_default=True))
    session.add(BulkLot(
        set_id="sv1", game="pokemon", title="Cheap lot", card_count=5,
        price=Decimal("1.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.add(BulkLot(
        set_id="sv2", game="pokemon", title="Fine lot", card_count=50,
        price=Decimal("40.00"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE,
    ))
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    try:
        body = TestClient(app).get("/shop-pricing").text
    finally:
        app.dependency_overrides.clear()

    assert "Cheap lot" in body and "Fine lot" in body
    assert "Below floor" in body  # the $1.00 lot
    assert "Break-even" in body


def test_shop_pricing_is_in_the_nav_drawer():
    from automation_control.ui import _sidenav_html

    assert "href='/shop-pricing'" in _sidenav_html()
