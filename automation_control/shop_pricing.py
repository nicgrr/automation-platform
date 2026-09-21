"""Break-even and floor pricing for the NL Vault shop -- Module 25.

Replaces the bulk-lot builder's hardcoded $12 floor, which was a guess
with no way to tell whether it was profitable. This works the number out
from what fulfilment actually costs.

The maths, for one order line:

    revenue  = price + shipping_charged
    costs    = stock_cost + postage_cost + packaging + payment_fees
    fees     = payment_pct × revenue + payment_fixed

Setting profit to zero and solving for price:

    price = (stock_cost + postage + packaging + fixed) / (1 - pct)
            - shipping_charged

That subtraction matters: postage the customer pays is revenue, so
charging shipping lowers the price you need to break even. Ignoring it
(as a flat floor does) overstates the floor on shipped-separately items
and understates it on free-shipping ones.

Stock cost is usually unknown for a scanned collection --
`allocated_cost_basis` is mostly empty -- so it defaults to zero and the
result is labelled accordingly. A break-even that excludes what the cards
cost you is still the right *marginal* answer for stock you already own;
it just isn't a full P&L, and shouldn't be read as one.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ShippingZone, ShopCostConfig

CENT = Decimal("0.01")


def _round(value: Decimal, rounding=ROUND_HALF_UP) -> Decimal:
    return value.quantize(CENT, rounding=rounding)


@dataclass(frozen=True)
class PriceFloor:
    """A break-even and a floor, with the parts that produced them so the
    number can be argued with rather than just trusted."""

    stock_cost: Decimal
    postage_cost: Decimal
    packaging_cost: Decimal
    payment_fixed: Decimal
    payment_pct: Decimal
    shipping_charged: Decimal
    break_even: Decimal
    floor: Decimal
    margin_pct: Decimal
    stock_cost_known: bool

    @property
    def marginal_costs(self) -> Decimal:
        return self.stock_cost + self.postage_cost + self.packaging_cost

    def profit_at(self, price: Decimal) -> Decimal:
        """Actual profit if this sold at `price`, fees and all."""
        revenue = Decimal(str(price)) + self.shipping_charged
        fees = revenue * self.payment_pct / Decimal("100") + self.payment_fixed
        return _round(revenue - self.marginal_costs - fees)

    def margin_at(self, price: Decimal) -> Decimal | None:
        price = Decimal(str(price))
        if price <= 0:
            return None
        return _round(self.profit_at(price) / price * Decimal("100"))

    def clears_floor(self, price: Decimal) -> bool:
        return Decimal(str(price)) >= self.floor


def default_costs(session: Session) -> ShopCostConfig:
    """The configured cost assumptions, or sane defaults if none exist yet
    -- pricing should never silently fail because a config row is missing."""
    config = session.scalar(
        select(ShopCostConfig).where(ShopCostConfig.is_default == True).limit(1)  # noqa: E712
    ) or session.scalar(select(ShopCostConfig).limit(1))
    # Spell the fallback out. Column defaults are applied on INSERT, so an
    # unpersisted ShopCostConfig() has None in every field -- which would
    # turn a missing config row into a crash deep in the arithmetic.
    return config or ShopCostConfig(
        label="Fallback", postage_cost_au=Decimal("9.95"), postage_cost_intl=Decimal("29.95"),
        packaging_cost=Decimal("0.50"), payment_pct=Decimal("1.75"),
        payment_fixed=Decimal("0.30"), target_margin_pct=Decimal("25.00"),
    )


def price_floor(
    config: ShopCostConfig,
    *,
    zone: ShippingZone = ShippingZone.AU,
    shipping_charged: Decimal | float = Decimal("0"),
    stock_cost: Decimal | float | None = None,
    margin_pct: Decimal | float | None = None,
    payment_pct: Decimal | float | None = None,
) -> PriceFloor:
    """Break-even and floor for one item or lot.

    `shipping_charged` is what the customer pays for postage; pass 0 for a
    free-shipping item, where you absorb it and the floor rises to cover it.

    `payment_pct` overrides config's own payment_pct (~1.75%, the direct
    Stripe rate this config models for the website) -- pass eBay's ~13%
    final value fee here instead when the floor is for something sold
    through eBay rather than the website's own checkout, since eBay's cut
    already bundles payment processing into that one number and the two
    percentages must never be stacked together.
    """
    postage = Decimal(str(config.postage_cost_intl if zone == ShippingZone.INTERNATIONAL else config.postage_cost_au))
    packaging = Decimal(str(config.packaging_cost))
    pct = Decimal(str(config.payment_pct if payment_pct is None else payment_pct))
    fixed = Decimal(str(config.payment_fixed))
    charged = Decimal(str(shipping_charged))
    margin = Decimal(str(config.target_margin_pct if margin_pct is None else margin_pct))

    known = stock_cost is not None
    stock = Decimal(str(stock_cost)) if known else Decimal("0")

    divisor = Decimal("1") - pct / Decimal("100")
    if divisor <= 0:  # a >=100% payment fee is nonsense; refuse rather than divide by ~zero
        raise ValueError("payment_pct must be below 100")

    break_even = (stock + postage + packaging + fixed) / divisor - charged
    break_even = max(break_even, Decimal("0"))

    # Round the floor up: rounding a floor down would put it fractionally
    # below break-even, which is the one direction that must never happen.
    floor = _round(break_even * (Decimal("1") + margin / Decimal("100")), rounding=ROUND_CEILING)

    return PriceFloor(
        stock_cost=_round(stock), postage_cost=_round(postage), packaging_cost=_round(packaging),
        payment_fixed=_round(fixed), payment_pct=pct, shipping_charged=_round(charged),
        break_even=_round(break_even, rounding=ROUND_CEILING), floor=floor,
        margin_pct=margin, stock_cost_known=known,
    )
