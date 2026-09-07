"""Sonny Angel, Smiski, and other blind-box collectibles -- Module 12.

Catalogue only, same scope boundary as sealed_economics.py: InventoryItem's
`card_id` is a NOT NULL foreign key into catalog_cards specifically (the
TCG-card table), not the generic catalog_items table -- a leftover from
before the generic catalogue existed. Actually tracking "I own 3 of this
Sonny Angel" needs that column relaxed, which means rebuilding
inventory_items (SQLite can't just drop a NOT NULL constraint via ALTER
TABLE) while scan-ingest.service -- which writes to that table continuously
-- is stopped. That's a real, separate, reviewed migration, not something
to shortcut here with a fake catalog_cards row. Tracked as a known
limitation until then.
"""

import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import CatalogItem, CatalogItemType, CollectibleProduct
from .ui import brand_header, page, pill

router = APIRouter(tags=["collectibles"])


def _dec(value: str, default: str = "0") -> Decimal:
    try:
        return Decimal(value) if value.strip() else Decimal(default)
    except InvalidOperation:
        return Decimal(default)


@router.get("/collectibles", response_class=HTMLResponse)
def collectibles_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.execute(
        select(CatalogItem, CollectibleProduct).join(CollectibleProduct, CollectibleProduct.catalog_item_id == CatalogItem.id)
        .order_by(CatalogItem.name)
    ).all()
    table_rows = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td>"
        f"<td class='muted'>{escape(cp.brand or '—')}</td>"
        f"<td class='muted'>{escape(cp.series or '—')}</td>"
        f"<td class='muted'>{escape(cp.variant or '—')}</td>"
        f"<td>{pill('secret', 'warn') if cp.is_secret else '—'}</td>"
        f"<td>{f'${cp.retail_price:,.2f}' if cp.retail_price else '—'}</td>"
        "</tr>"
        for item, cp in rows
    ) or "<tr><td colspan=6>No collectibles catalogued yet.</td></tr>"
    table = (
        "<div class='panel'><div class='table-wrap'><table>"
        "<thead><tr><th>Name</th><th>Brand</th><th>Series</th><th>Variant</th><th></th><th>Retail</th></tr></thead>"
        f"<tbody>{table_rows}</tbody></table></div></div>"
    )

    form = (
        "<div class='panel'><h2>Add a collectible</h2>"
        "<form method='post' action='/collectibles' class='calc-form'>"
        "<label>Name<input name='name' required placeholder='Sonny Angel Fruits Series - Peach'></label>"
        "<label>Brand<input name='brand' placeholder='Sonny Angel, Smiski, ...'></label>"
        "<label>Series<input name='series'></label>"
        "<label>Character<input name='character'></label>"
        "<label>Variant<input name='variant'></label>"
        "<label>Blind box series<input name='blind_box_series'></label>"
        "<label><input name='is_secret' type='checkbox' value='true' style='width:auto'> Secret / chase figure</label>"
        "<label>Retail price<input name='retail_price' type='number' step='0.01'></label>"
        "<button type='submit'>Add</button></form></div>"
    )
    body = (
        brand_header("Collectibles")
        + "<p class='subtitle'>Module 12 -- Sonny Angel, Smiski, blind boxes. Catalogue only for now; "
          "see this module's own docstring for why inventory tracking isn't wired up yet.</p>"
        + table + form + _STYLE
    )
    return HTMLResponse(page("EzBay — Collectibles", body))


@router.post("/collectibles")
def create_collectible(
    name: str = Form(...), brand: str = Form(""), series: str = Form(""), character: str = Form(""),
    variant: str = Form(""), blind_box_series: str = Form(""), is_secret: str = Form(""), retail_price: str = Form(""),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item_id = str(uuid.uuid4())
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(
        catalog_item_id=item_id, brand=brand or None, series=series or None, character=character or None,
        variant=variant or None, blind_box_series=blind_box_series or None, is_secret=bool(is_secret),
        retail_price=_dec(retail_price) if retail_price.strip() else None,
    ))
    session.commit()
    return RedirectResponse("/collectibles", status_code=303)


_STYLE = """<style>
.calc-form{display:flex;flex-direction:column;gap:12px;max-width:420px}
.calc-form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--text-dim)}
.calc-form input{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:8px;
  padding:10px 12px;color:var(--text);font-size:14px}
.calc-form button{background:linear-gradient(120deg,var(--accent),var(--accent-2));border:none;padding:11px;
  border-radius:8px;color:#04101a;font-weight:700;cursor:pointer;margin-top:4px}
.muted{color:var(--text-dim);font-size:13px}
</style>"""
