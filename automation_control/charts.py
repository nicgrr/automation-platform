"""Hand-drawn inline SVG charts -- no charting library, matching this
project's plain-string-HTML convention (see ui.py's own docstring on why:
a private admin tool stays self-contained, no CDN scripts). Reused by the
light-themed executive dashboard and available for any future page.
"""

import math
from html import escape

DEFAULT_PALETTE = ["#0891b2", "#7c3aed", "#d97706", "#059669", "#dc2626", "#64748b"]


def donut_chart(segments: list[tuple[str, float]], size: int = 140, palette: list[str] | None = None) -> str:
    """segments: [(label, value), ...]. Renders as stacked arcs via the
    stroke-dasharray trick (each segment is a fraction of the circle's own
    circumference, offset by the running total so far), rotated -90deg so
    the first segment starts at 12 o'clock like every conventional donut."""
    palette = palette or DEFAULT_PALETTE
    total = sum(v for _, v in segments) or 1.0
    r = size / 2 - 10
    circumference = 2 * math.pi * r
    cx = cy = size / 2

    arcs = []
    offset = 0.0
    for i, (_, value) in enumerate(segments):
        fraction = value / total
        length = fraction * circumference
        arcs.append(
            f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='{palette[i % len(palette)]}' "
            f"stroke-width='16' stroke-dasharray='{length:.2f} {circumference:.2f}' "
            f"stroke-dashoffset='{-offset:.2f}' transform='rotate(-90 {cx} {cy})'/>"
        )
        offset += length

    return (
        f"<svg viewBox='0 0 {size} {size}' width='{size}' height='{size}'>"
        f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#eef0f5' stroke-width='16'/>"
        + "".join(arcs) +
        "</svg>"
    )


def donut_legend(segments: list[tuple[str, float]], palette: list[str] | None = None) -> str:
    palette = palette or DEFAULT_PALETTE
    total = sum(v for _, v in segments) or 1.0
    rows = "".join(
        f"<div class='legend-row'><span class='legend-dot' style='background:{palette[i % len(palette)]}'></span>"
        f"<span class='legend-label'>{escape(label)}</span>"
        f"<span class='legend-value'>{value / total * 100:.0f}%</span></div>"
        for i, (label, value) in enumerate(segments)
    )
    return f"<div class='legend'>{rows}</div>"


def gauge_chart(pct: float, size: int = 140, color: str = "#0891b2", label: str = "") -> str:
    """A single-value progress ring -- the donut technique with one
    foreground segment (pct) and the remainder left as the background
    track, with the percentage printed in the centre."""
    pct = max(0.0, min(100.0, pct))
    r = size / 2 - 10
    circumference = 2 * math.pi * r
    cx = cy = size / 2
    length = pct / 100 * circumference
    return (
        f"<svg viewBox='0 0 {size} {size}' width='{size}' height='{size}'>"
        f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#eef0f5' stroke-width='16'/>"
        f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='{color}' stroke-width='16' "
        f"stroke-linecap='round' stroke-dasharray='{length:.2f} {circumference:.2f}' transform='rotate(-90 {cx} {cy})'/>"
        f"<text x='{cx}' y='{cy - 2}' text-anchor='middle' font-size='26' font-weight='700' fill='#12141c'>{pct:.0f}%</text>"
        + (f"<text x='{cx}' y='{cy + 18}' text-anchor='middle' font-size='11' fill='#6b7280'>{escape(label)}</text>" if label else "")
        + "</svg>"
    )


def bar_list(items: list[tuple[str, float]], color: str = "#0891b2", money: bool = True) -> str:
    """A horizontal bar list -- plain divs, not SVG, since a filled
    rectangle scaled by percentage width needs nothing more."""
    if not items:
        return "<p class='chart-empty'>No data yet.</p>"
    max_value = max(v for _, v in items) or 1.0
    rows = []
    for label, value in items:
        width = max(2.0, value / max_value * 100)
        value_text = f"${value:,.2f}" if money else f"{value:,.0f}"
        rows.append(
            "<div class='bar-row'>"
            f"<div class='bar-label'>{escape(label)}</div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{width:.0f}%;background:{color}'></div></div>"
            f"<div class='bar-value'>{value_text}</div>"
            "</div>"
        )
    return "".join(rows)


CHART_STYLE = """
.legend{display:flex;flex-direction:column;gap:8px}
.legend-row{display:flex;align-items:center;gap:8px;font-size:13px}
.legend-dot{width:10px;height:10px;border-radius:50%;flex:none}
.legend-label{flex:1;color:#374151}
.legend-value{font-weight:650;color:#12141c}
.bar-row{display:grid;grid-template-columns:110px 1fr 90px;align-items:center;gap:10px;margin-bottom:10px;font-size:13px}
.bar-label{color:#374151;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{background:#eef0f5;border-radius:999px;height:10px;overflow:hidden}
.bar-fill{height:100%;border-radius:999px}
.bar-value{text-align:right;font-weight:650;color:#12141c}
.chart-empty{color:#9ca3af;font-size:13px}
"""
