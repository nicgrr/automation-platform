"""Shop pricing review -- Module 25.

The answer to "how do I know we're actually in profit?": every listing and
bulk lot, with what fulfilling it costs, what it breaks even at, and what
it currently earns. Read-only; changing a price is still a deliberate act
elsewhere.

Two things this page exists to make obvious:

  * Who pays postage dominates everything at these order values. With the
    customer paying $9.95, break-even on a lot is about $1.70. With free
    shipping it's about $11.65 -- nearly seven times higher for the same
    goods.
  * So the risk isn't the floor, it's the free-shipping threshold: an
    order that crosses it turns postage from revenue into cost.
"""

from decimal import Decimal
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import BulkLot, ShippingRate, ShippingZone, ShopListing, ShopListingStatus
from .shop_pricing import default_costs, price_floor
from .ui import brand_header, page, pill

router = APIRouter(tags=["shop-pricing"])


def _money(value) -> str:
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


@router.get("/shop-pricing", response_class=HTMLResponse)
def shop_pricing(
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)
) -> HTMLResponse:
    config = default_costs(session)
    au_rate = session.scalar(select(ShippingRate).where(ShippingRate.zone == ShippingZone.AU).limit(1))
    charged = Decimal(str(au_rate.price)) if au_rate else Decimal("0")
    free_over = Decimal(str(au_rate.free_over)) if (au_rate and au_rate.free_over is not None) else None

    paid_floor = price_floor(config, shipping_charged=charged)
    free_floor = price_floor(config, shipping_charged=Decimal("0"))

    # Every saleable thing, singles and lots together -- the question
    # "am I in profit" doesn't care which table it came from.
    rows: list[tuple[str, str, Decimal, int]] = [
        ("Single", listing.title, Decimal(str(listing.price)), listing.quantity)
        for listing in session.scalars(
            select(ShopListing).where(ShopListing.status == ShopListingStatus.ACTIVE)
        ).all()
    ] + [
        ("Bulk lot", lot.title, Decimal(str(lot.price)), lot.quantity)
        for lot in session.scalars(
            select(BulkLot).where(BulkLot.status == ShopListingStatus.ACTIVE)
        ).all()
    ]
    rows.sort(key=lambda r: r[2])

    body_rows = []
    below = 0
    for kind, title, price, quantity in rows:
        profit = paid_floor.profit_at(price)
        margin = paid_floor.margin_at(price)
        clears = paid_floor.clears_floor(price)
        free_profit = free_floor.profit_at(price)
        if not clears:
            below += 1
        verdict = pill("OK", "ok") if clears else pill("Below floor", "bad")
        free_cell = (
            f"{_money(free_profit)}" if free_profit > 0
            else f"<span style='color:var(--danger)'>{_money(free_profit)}</span>"
        )
        body_rows.append(
            "<tr>"
            f"<td class='muted'>{escape(kind)}</td>"
            f"<td>{escape(title)}</td>"
            f"<td>{quantity}</td>"
            f"<td>{_money(price)}</td>"
            f"<td>{_money(profit)}</td>"
            f"<td>{f'{margin:.0f}%' if margin is not None else '—'}</td>"
            f"<td>{free_cell}</td>"
            f"<td>{verdict}</td>"
            "</tr>"
        )

    table = (
        "<div class='panel'><h2>Every active item</h2>"
        "<div class='table-wrap'><table><thead><tr>"
        "<th>Type</th><th>Item</th><th>Qty</th><th>Price</th>"
        "<th>Profit</th><th>Margin</th><th>If free ship</th><th></th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows) or '<tr><td colspan=8>Nothing listed yet.</td></tr>'}</tbody>"
        "</table></div></div>"
    )

    assumptions = (
        "<div class='panel'><h2>What this assumes</h2>"
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Postage cost (AU)</div><div class='value'>{_money(config.postage_cost_au)}</div></div>"
        f"<div class='stat-card'><div class='label'>Packaging</div><div class='value'>{_money(config.packaging_cost)}</div></div>"
        f"<div class='stat-card'><div class='label'>Payment fee</div><div class='value'>{config.payment_pct}% + {_money(config.payment_fixed)}</div></div>"
        f"<div class='stat-card'><div class='label'>Target margin</div><div class='value'>{config.target_margin_pct}%</div></div>"
        "</div>"
        "<p class='muted' style='margin-top:12px'>Stock cost is excluded — "
        "<code>allocated_cost_basis</code> is empty for most scanned cards, so these are "
        "<strong>marginal</strong> profits on stock you already hold, not a full P&amp;L.</p>"
        "</div>"
    )

    floors = (
        "<div class='panel'><h2>Break-even</h2>"
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Customer pays postage</div>"
        f"<div class='value'>{_money(paid_floor.break_even)}"
        f"<span class='value-sub'> break-even · floor {_money(paid_floor.floor)}</span></div></div>"
        f"<div class='stat-card'><div class='label'>Free shipping</div>"
        f"<div class='value'>{_money(free_floor.break_even)}"
        f"<span class='value-sub'> break-even · floor {_money(free_floor.floor)}</span></div></div>"
        "</div>"
        + (
            "<div class='panel' style='margin:14px 0 0;box-shadow:none;background:var(--warning-bg);border-color:var(--warning)'>"
            f"<p style='margin:0;color:var(--warning)'><strong>Free shipping kicks in over {_money(free_over)}.</strong> "
            "Above that you absorb postage, so an order made up of cheap lots earns far less than the same value "
            "in singles. Worth raising the threshold or excluding bulk lots from it.</p></div>"
            if free_over is not None else ""
        )
        + "</div>"
    )

    summary = (
        f"<p class='subtitle'>{len(rows)} active item(s). "
        + (f"<strong>{below}</strong> priced below the floor." if below else "All clear the floor.")
        + "</p>"
    )

    body = (
        brand_header("Shop pricing")
        + summary + floors + assumptions + table
        + "<style>.muted{color:var(--text-dim);font-size:13px}</style>"
    )
    return HTMLResponse(page("EzBay — Shop pricing", body))
