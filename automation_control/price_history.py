"""Price history -- Module 5's missing half. The pricing engine
(scan_ingest/pricing.py's PriceSource abstraction, CardPrice as an
append-only, source-tagged observation log) already exists and has been
writing real data all along; this is the first page that actually shows
it back. Per variant: current price, 7-day average, 30-day average, change,
high, low, last updated, source -- plus a hand-drawn inline SVG sparkline
(no charting library, matching this project's plain-string-HTML
convention throughout).
"""

from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import CardPrice, CatalogItem, TcgCard
from .scan_ingest import fx
from .ui import brand_header, page

router = APIRouter(prefix="/prices", tags=["prices"])


def _sparkline(points: list[float], width: int = 240, height: int = 48) -> str:
    """A bare inline SVG sparkline -- no library, just a polyline scaled to
    fit. Flat/single-point series draw a straight mid-height line rather
    than dividing by zero."""
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    span = hi - lo or 1.0
    step = width / (len(points) - 1)
    coords = " ".join(f"{i * step:.1f},{height - ((v - lo) / span) * height:.1f}" for i, v in enumerate(points))
    return (
        f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' class='sparkline'>"
        f"<polyline points='{coords}' fill='none' stroke='currentColor' stroke-width='2'/>"
        "</svg>"
    )


@router.get("/{card_id}", response_class=HTMLResponse)
def price_history_page(card_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    item = session.get(CatalogItem, card_id)
    if item is None:
        raise HTTPException(status_code=404, detail="catalogue item not found")
    tcg = session.get(TcgCard, card_id)

    observations = session.scalars(
        select(CardPrice).where(CardPrice.card_id == card_id).order_by(CardPrice.fetched_at)
    ).all()

    now = datetime.now(UTC)

    def naive(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    by_variant: dict[str, list[CardPrice]] = {}
    for obs in observations:
        by_variant.setdefault(obs.variant.value, []).append(obs)

    sections = []
    for variant, obs_list in by_variant.items():
        aud_points = [(naive(o.fetched_at), fx.to_aud(o.price, o.currency)) for o in obs_list]
        aud_points = [(dt, v) for dt, v in aud_points if v is not None]
        if not aud_points:
            continue

        current = aud_points[-1][1]
        last_updated = aud_points[-1][0]
        source = obs_list[-1].source
        window_7d = [v for dt, v in aud_points if now - dt <= timedelta(days=7)]
        window_30d = [v for dt, v in aud_points if now - dt <= timedelta(days=30)]
        avg_7d = sum(window_7d) / len(window_7d) if window_7d else None
        avg_30d = sum(window_30d) / len(window_30d) if window_30d else None
        high = max(v for _, v in aud_points)
        low = min(v for _, v in aud_points)
        change_pct = ((current - aud_points[0][1]) / aud_points[0][1] * 100) if aud_points[0][1] else None

        def money(v: float | None) -> str:
            return f"${v:,.2f}" if v is not None else "—"

        sections.append(
            "<div class='panel'>"
            f"<h2>{escape(variant.replace('_', ' ').title())}</h2>"
            f"<div class='price-layout'>{_sparkline([v for _, v in aud_points])}"
            "<div class='calc-grid'>"
            f"<div><div class='label'>Current</div><div class='value'>{money(current)}</div></div>"
            f"<div><div class='label'>7-day avg</div><div class='value'>{money(avg_7d)}</div></div>"
            f"<div><div class='label'>30-day avg</div><div class='value'>{money(avg_30d)}</div></div>"
            f"<div><div class='label'>Change</div><div class='value'>{f'{change_pct:+.1f}%' if change_pct is not None else '—'}</div></div>"
            f"<div><div class='label'>High</div><div class='value'>{money(high)}</div></div>"
            f"<div><div class='label'>Low</div><div class='value'>{money(low)}</div></div>"
            "</div></div>"
            f"<p class='subtitle'>Last updated {last_updated:%Y-%m-%d} via {escape(source)} &middot; {len(obs_list)} observation(s)</p>"
            "</div>"
        )

    if not sections:
        sections = ["<div class='panel'><p>No price observations recorded for this card yet.</p></div>"]

    game_label = f" &middot; {escape(tcg.game)}" if tcg else ""
    body = (
        brand_header(item.name)
        + f"<p class='subtitle'>Price history{game_label} -- Module 5.</p>"
        + "".join(sections)
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {item.name} price history", body))


_STYLE = """<style>
.price-layout{display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.sparkline{color:var(--accent);flex:0 0 auto}
.calc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:14px;flex:1 1 300px}
.calc-grid .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);margin-bottom:4px}
.calc-grid .value{font-size:18px;font-weight:650}
</style>"""
