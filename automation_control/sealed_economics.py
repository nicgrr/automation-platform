"""Sealed products and the case-economics calculator -- Module 11.

A SealedProduct's `units_per_display` (packs per box) and
`displays_per_case` (boxes per case) are what a case-vs-box-vs-singles
comparison is built from -- this is where those numbers actually get used,
by computing all four scenarios from the spec side by side so buying a
case is a comparison, not a guess.
"""

import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import CatalogItem, CatalogItemType, SealedProduct
from .ui import brand_header, page, pill

router = APIRouter(tags=["sealed-economics"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


@router.get("/sealed-products", response_class=HTMLResponse)
def sealed_products_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.execute(
        select(CatalogItem, SealedProduct).join(SealedProduct, SealedProduct.catalog_item_id == CatalogItem.id)
    ).all()
    table_rows = "".join(
        "<tr>"
        f"<td><a href='/sealed-products/{escape(item.id)}/economics'>{escape(item.name)}</a></td>"
        f"<td class='muted'>{escape(sp.brand or '—')} &middot; {escape(sp.product_type or '—')}</td>"
        f"<td>{sp.units_per_display or '—'} / display</td>"
        f"<td>{sp.displays_per_case or '—'} / case</td>"
        f"<td>{f'${sp.rrp:,.2f}' if sp.rrp else '—'}</td>"
        "</tr>"
        for item, sp in rows
    ) or "<tr><td colspan=5>No sealed products yet.</td></tr>"
    table = f"<div class='panel'><div class='table-wrap'><table><thead><tr><th>Product</th><th>Brand / type</th><th>Units/display</th><th>Displays/case</th><th>RRP</th></tr></thead><tbody>{table_rows}</tbody></table></div></div>"

    form = (
        "<div class='panel'><h2>Add a sealed product</h2>"
        "<form method='post' action='/sealed-products' class='calc-form'>"
        "<label>Name<input name='name' required placeholder='One Piece OP18 Booster Box'></label>"
        "<label>Brand<input name='brand'></label>"
        "<label>Game<input name='game' placeholder='one_piece'></label>"
        "<label>Product type<input name='product_type' placeholder='booster_box, display, case, etb'></label>"
        "<label>Units per display (packs/box)<input name='units_per_display' type='number'></label>"
        "<label>Displays per case (boxes/case)<input name='displays_per_case' type='number'></label>"
        "<label>RRP<input name='rrp' type='number' step='0.01'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = brand_header("Sealed products") + "<p class='subtitle'>Module 11 -- booster boxes, displays, cases.</p>" + table + form + _STYLE
    return HTMLResponse(page("EzBay — Sealed products", body))


@router.post("/sealed-products")
def create_sealed_product(
    name: str = Form(...), brand: str = Form(""), game: str = Form(""), product_type: str = Form(""),
    units_per_display: str = Form(""), displays_per_case: str = Form(""), rrp: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.SEALED_PRODUCT, name=name))
    session.add(SealedProduct(
        catalog_item_id=item_id, brand=brand or None, game=game or None, product_type=product_type or None,
        units_per_display=int(units_per_display) if units_per_display.strip() else None,
        displays_per_case=int(displays_per_case) if displays_per_case.strip() else None,
        rrp=_dec(rrp) if rrp.strip() else None,
    ))
    session.commit()
    return RedirectResponse(f"/sealed-products/{item_id}/economics", status_code=303)


@router.get("/sealed-products/{item_id}/economics", response_class=HTMLResponse)
def sealed_product_economics(
    item_id: str,
    case_cost: str = Query(""), price_per_box: str = Query(""), price_per_pack: str = Query(""),
    avg_singles_value_per_pack: str = Query(""), case_resale_value: str = Query(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    item = session.get(CatalogItem, item_id)
    sealed = session.get(SealedProduct, item_id)
    if item is None or sealed is None:
        raise HTTPException(status_code=404, detail="sealed product not found")

    boxes_per_case = sealed.displays_per_case or 1
    packs_per_box = sealed.units_per_display or 1
    packs_per_case = boxes_per_case * packs_per_box

    result_html = ""
    if case_cost.strip():
        cost = _dec(case_cost)

        def scenario(label: str, revenue: Decimal) -> str:
            profit = revenue - cost
            roi = (profit / cost * 100) if cost else None
            kind = "ok" if profit > 0 else ("bad" if profit < 0 else "neutral")
            return (
                "<tr>"
                f"<td>{escape(label)}</td>"
                f"<td>${revenue:,.2f}</td>"
                f"<td>{pill(f'${profit:,.2f}', kind)}</td>"
                f"<td>{f'{roi:.0f}%' if roi is not None else '—'}</td>"
                "</tr>"
            )

        rows = [
            scenario("Sell whole case", _dec(case_resale_value)),
            scenario("Sell as booster boxes", _dec(price_per_box) * boxes_per_case),
            scenario("Sell as individual packs", _dec(price_per_pack) * packs_per_case),
            scenario("Open case, sell singles", _dec(avg_singles_value_per_pack) * packs_per_case),
        ]
        result_html = (
            "<div class='panel'><h2>Scenario comparison</h2><div class='table-wrap'><table>"
            "<thead><tr><th>Scenario</th><th>Revenue</th><th>Profit</th><th>ROI</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            f"<p class='subtitle'>{packs_per_case} pack(s) per case ({boxes_per_case} box(es) &times; {packs_per_box} pack(s))</p>"
            "</div>"
        )

    def field(name: str, label: str, value: str) -> str:
        return f"<label>{label}<input name='{name}' value='{escape(value)}' type='number' step='0.01'></label>"

    body = (
        brand_header(item.name)
        + f"<p class='subtitle'>{boxes_per_case} box(es)/case &middot; {packs_per_box} pack(s)/box &middot; {packs_per_case} pack(s)/case</p>"
        + "<form method='get' class='calc-form panel'>"
        + field("case_cost", "Case cost", case_cost)
        + field("case_resale_value", "Expected resale (whole case)", case_resale_value)
        + field("price_per_box", "Expected sale price per box", price_per_box)
        + field("price_per_pack", "Expected sale price per pack", price_per_pack)
        + field("avg_singles_value_per_pack", "Expected avg. singles value per pack opened", avg_singles_value_per_pack)
        + "<button type='submit'>Compare scenarios</button>"
        + "</form>"
        + result_html
        + _STYLE
    )
    return HTMLResponse(page(f"EzBay — {item.name} economics", body))


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#04101a;font-weight:700;cursor:pointer;margin-top:4px}
.muted{color:var(--text-dim);font-size:12px}
</style>"""
