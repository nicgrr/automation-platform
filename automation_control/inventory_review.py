"""Browse the scan-ingest pipeline's inventory: what's actually been
scanned, identified, and committed (automation_control/scan_ingest/). Read
-only -- this router shows what commit.py has already written, it never
writes anything itself.

Organised set-first: the landing page is a grid of set tiles (one aggregate
query, no card images at all), and card detail lives behind a set. The
previous version rendered every owned card on one page with a full-size
750x1050 scan as its thumbnail -- 126 images at ~340KB was tens of
megabytes for a page whose job is "what do I own", and it got slower with
every scan. See `inventory_thumb` for how images are served now.

Values are shown in AUD (converted at display time, see scan_ingest/fx.py);
`CardPrice` rows keep their source currency.
"""

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .models import CardPrice, CardSet, CardVariant, CatalogCard, InventoryItem
from .scan_ingest import fx
from .ui import brand_header, page, pill

router = APIRouter(prefix="/inventory", tags=["inventory"])

# Long enough that a scan's own detail still reads well, small enough that a
# whole set's worth loads instantly. The source scans are 750x1050.
THUMB_WIDTH = 220
THUMB_QUALITY = 78


def _latest_prices(session: Session, card_ids: set[str]) -> dict[tuple[str, CardVariant], CardPrice]:
    """Most recent CardPrice per (card_id, variant). Done in Python rather
    than a SQL window function -- this app has no precedent for those, and
    at the scale of one operator's scanned inventory a single ORDER BY +
    first-seen-wins pass is simple and plenty fast."""
    if not card_ids:
        return {}
    rows = session.scalars(
        select(CardPrice).where(CardPrice.card_id.in_(card_ids)).order_by(CardPrice.fetched_at.desc())
    )
    latest: dict[tuple[str, CardVariant], CardPrice] = {}
    for row in rows:
        key = (row.card_id, row.variant)
        if key not in latest:
            latest[key] = row
    return latest


def _aud(price: CardPrice | None, quantity: int = 1) -> float | None:
    if price is None:
        return None
    converted = fx.to_aud(price.price, price.currency)
    return None if converted is None else converted * quantity


def _money(value: float | None) -> str:
    return "&mdash;" if value is None else f"${value:,.2f}"


# --- set grid (landing) ----------------------------------------------------

@router.get("", response_class=HTMLResponse)
def inventory_sets(
    request: Request,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Every set you own cards from, as tiles. Deliberately loads no card
    images: this is the "what do I have" view, and set artwork is a handful
    of small cached logos rather than one scan per card."""
    owned = session.execute(
        select(
            CatalogCard.set_id,
            func.count(func.distinct(InventoryItem.card_id)).label("distinct_cards"),
            func.sum(InventoryItem.quantity).label("copies"),
        )
        .join(InventoryItem, InventoryItem.card_id == CatalogCard.id)
        .group_by(CatalogCard.set_id)
    ).all()

    if not owned:
        body = (
            brand_header("Inventory")
            + "<p class='subtitle'>Cards committed by the scan-ingest pipeline.</p>"
            + "<div class='panel'><p>Nothing scanned yet. Press the scanner button and the "
              "background service will do the rest.</p></div>"
        )
        return HTMLResponse(page("EzBay — Inventory", body))

    set_ids = {row.set_id for row in owned}
    sets = {s.id: s for s in session.scalars(select(CardSet).where(CardSet.id.in_(set_ids)))}

    # Value per set, from the latest price per (card, variant) it holds.
    items = list(session.scalars(
        select(InventoryItem).join(CatalogCard, InventoryItem.card_id == CatalogCard.id)
        .where(CatalogCard.set_id.in_(set_ids))
    ))
    prices = _latest_prices(session, {i.card_id for i in items})
    card_set_of = {c.id: c.set_id for c in session.scalars(select(CatalogCard).where(CatalogCard.id.in_({i.card_id for i in items})))}

    value_by_set: dict[str, float] = {}
    for item in items:
        line = _aud(prices.get((item.card_id, item.variant)), item.quantity)
        if line:
            value_by_set[card_set_of[item.card_id]] = value_by_set.get(card_set_of[item.card_id], 0.0) + line

    rows = sorted(owned, key=lambda r: -value_by_set.get(r.set_id, 0.0))
    total_copies = sum(r.copies or 0 for r in rows)
    total_distinct = sum(r.distinct_cards or 0 for r in rows)
    total_value = sum(value_by_set.values())

    tiles = []
    for row in rows:
        card_set = sets.get(row.set_id)
        name = card_set.name if card_set else row.set_id
        printed = (card_set.printed_total if card_set else None) or 0
        logo = card_set.logo_url if card_set else None
        released = (card_set.release_date if card_set else "") or ""
        pct = (row.distinct_cards / printed * 100) if printed else 0

        art = (
            f"<img src='{escape(logo)}' alt='' loading='lazy' decoding='async'"
            f" style='max-width:82%;max-height:64px;object-fit:contain'>"
            if logo else f"<span style='font-weight:700;font-size:15px'>{escape(name)}</span>"
        )
        tiles.append(
            f"<a class='set-tile' href='/inventory/set/{escape(row.set_id)}'>"
            f"<div class='set-art'>{art}"
            f"{f'<span class=set-date>{escape(released)}</span>' if released else ''}"
            f"{f'<span class=set-pct>{pct:.0f}%</span>' if pct >= 1 else ''}"
            "</div>"
            f"<div class='set-name'>{escape(name)}</div>"
            f"<div class='set-meta'>Progress: {row.distinct_cards}"
            f"{'/' + str(printed) if printed else ''}</div>"
            f"<div class='set-meta'>Total Value: <strong>{_money(value_by_set.get(row.set_id))}</strong></div>"
            "</a>"
        )

    rate_note = "" if fx.is_live() else " (approximate rate — live FX unavailable)"
    stat_grid = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Sets</div><div class='value'>{len(rows)}</div></div>"
        f"<div class='stat-card'><div class='label'>Distinct cards</div><div class='value'>{total_distinct}</div></div>"
        f"<div class='stat-card'><div class='label'>Total copies</div><div class='value'>{total_copies}</div></div>"
        f"<div class='stat-card'><div class='label'>Estimated value</div><div class='value'>{_money(total_value)}<span style='font-size:12px;color:var(--text-dim)'> AUD</span></div></div>"
        "</div>"
    )

    body = (
        brand_header("Inventory")
        + f"<p class='subtitle'>Cards committed by the scan-ingest pipeline. Values are rough "
          f"pokemontcg.io estimates converted to AUD{rate_note} — not listing prices.</p>"
        + stat_grid
        + "<input id='set-search' class='set-search' type='search' placeholder='Search sets…' autocomplete='off'>"
        + f"<div class='set-grid' id='set-grid'>{''.join(tiles)}</div>"
        + _SET_GRID_STYLE
        + _SET_SEARCH_SCRIPT
    )
    return HTMLResponse(page("EzBay — Inventory", body))


_SET_GRID_STYLE = """<style>
.set-search{width:100%;max-width:420px;margin:0 0 20px;background:#0a0f1c;border:1px solid var(--panel-border);
  border-radius:10px;padding:11px 14px;color:var(--text);font-size:14px}
.set-search:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(94,234,255,0.15)}
.set-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:18px}
a.set-tile{display:flex;flex-direction:column;gap:4px;text-decoration:none;color:var(--text)}
.set-art{position:relative;display:grid;place-items:center;height:104px;border-radius:12px;
  background:#f4f5f7;border:1px solid var(--panel-border);overflow:hidden;transition:transform .15s ease,box-shadow .15s ease}
a.set-tile:hover .set-art{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.35)}
.set-date{position:absolute;top:7px;right:7px;background:rgba(255,255,255,.9);color:#12161c;
  font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:999px}
.set-pct{position:absolute;bottom:0;left:0;background:var(--accent);color:#04101a;
  font-size:10.5px;font-weight:700;padding:1px 6px;border-top-right-radius:6px}
.set-name{font-weight:600;font-size:14px;margin-top:6px}
.set-meta{font-size:12.5px;color:var(--text-dim)}
.set-meta strong{color:var(--text)}
@media (max-width:560px){.set-grid{grid-template-columns:repeat(2,1fr);gap:14px}.set-art{height:84px}}
</style>"""

_SET_SEARCH_SCRIPT = """<script>
(function(){
  var box=document.getElementById('set-search');
  if(!box)return;
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase();
    document.querySelectorAll('#set-grid .set-tile').forEach(function(t){
      var name=(t.querySelector('.set-name')||{}).textContent||'';
      t.style.display=name.toLowerCase().includes(q)?'':'none';
    });
  });
})();
</script>"""


# --- one set's cards -------------------------------------------------------

@router.get("/set/{set_id}", response_class=HTMLResponse)
def inventory_set(
    set_id: str,
    request: Request,
    variant: str = Query(""),
    sort: str = Query("number"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    card_set = session.get(CardSet, set_id)
    if card_set is None:
        raise HTTPException(status_code=404, detail=f"unknown set: {set_id!r}")

    query = (
        select(InventoryItem)
        .join(CatalogCard, InventoryItem.card_id == CatalogCard.id)
        .where(CatalogCard.set_id == set_id)
    )
    if variant:
        try:
            query = query.where(InventoryItem.variant == CardVariant(variant))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown variant: {variant!r}")

    items = list(session.scalars(query))
    cards = {c.id: c for c in session.scalars(select(CatalogCard).where(CatalogCard.id.in_({i.card_id for i in items})))} if items else {}
    prices = _latest_prices(session, {i.card_id for i in items})

    rows = [(i, cards[i.card_id], prices.get((i.card_id, i.variant))) for i in items if i.card_id in cards]
    if sort == "value_desc":
        rows.sort(key=lambda t: _aud(t[2], t[0].quantity) or 0.0, reverse=True)
    elif sort == "quantity_desc":
        rows.sort(key=lambda t: t[0].quantity, reverse=True)
    else:
        rows.sort(key=lambda t: _sort_number(t[1].number))

    total_value = sum(_aud(p, i.quantity) or 0.0 for i, _, p in rows)
    total_copies = sum(i.quantity for i, _, _ in rows)

    cells = []
    for item, card, price in rows:
        thumb = (
            f"<img src='/inventory/thumb/{item.id}' alt='' loading='lazy' decoding='async' width='{THUMB_WIDTH}'>"
            if item.scan_image_path else "<div class='no-scan'>no scan</div>"
        )
        unit = _aud(price)
        cells.append(
            f"<a class='card-tile' href='/inventory/image/{item.id}'>"
            f"<div class='card-thumb'>{thumb}"
            f"{f'<span class=qty>x{item.quantity}</span>' if item.quantity > 1 else ''}</div>"
            f"<div class='card-name'>{escape(card.name)}</div>"
            f"<div class='card-meta'>#{escape(card.number)} &middot; {escape(item.variant.value.replace('_',' '))}</div>"
            f"<div class='card-meta'><strong>{_money(unit)}</strong></div>"
            "</a>"
        )

    printed = card_set.printed_total or 0
    header = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Progress</div><div class='value'>{len(rows)}{'/' + str(printed) if printed else ''}</div></div>"
        f"<div class='stat-card'><div class='label'>Copies</div><div class='value'>{total_copies}</div></div>"
        f"<div class='stat-card'><div class='label'>Set value</div><div class='value'>{_money(total_value)}<span style='font-size:12px;color:var(--text-dim)'> AUD</span></div></div>"
        "</div>"
    )

    body = (
        brand_header(card_set.name)
        + f"<p class='subtitle'><a href='/inventory' style='color:var(--accent)'>&larr; All sets</a> &middot; "
          f"{escape(card_set.series or '')} {escape(card_set.release_date or '')}</p>"
        + header
        + (f"<div class='card-grid'>{''.join(cells)}</div>" if cells else "<div class='panel'><p>No cards from this set yet.</p></div>")
        + _CARD_GRID_STYLE
    )
    return HTMLResponse(page(f"EzBay — {card_set.name}", body))


_CARD_GRID_STYLE = """<style>
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:16px}
a.card-tile{text-decoration:none;color:var(--text);display:flex;flex-direction:column;gap:3px}
.card-thumb{position:relative;border-radius:10px;overflow:hidden;background:#0a0f1c;
  border:1px solid var(--panel-border);aspect-ratio:5/7;display:grid;place-items:center;
  transition:transform .15s ease,box-shadow .15s ease}
a.card-tile:hover .card-thumb{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.35)}
.card-thumb img{width:100%;height:100%;object-fit:cover;display:block}
.no-scan{color:var(--text-dim);font-size:12px}
.qty{position:absolute;top:6px;right:6px;background:var(--accent);color:#04101a;font-size:11px;
  font-weight:700;padding:1px 7px;border-radius:999px}
.card-name{font-weight:600;font-size:13px;margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-meta{font-size:12px;color:var(--text-dim)}
.card-meta strong{color:var(--text)}
@media (max-width:560px){.card-grid{grid-template-columns:repeat(3,1fr);gap:10px}}
</style>"""


def _sort_number(number: str) -> tuple[int, str]:
    """Sort card numbers numerically ('2' before '10') with a text fallback
    for the non-numeric ones some sets have (promos, 'SWSH001')."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return (int(digits) if digits else 0, number)


# --- images ----------------------------------------------------------------

def _thumb_path(item_id: str, source: Path) -> Path:
    settings = get_settings()
    thumb_dir = Path(settings.scan_media_dir) / "thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    return thumb_dir / f"{item_id}-{THUMB_WIDTH}.jpg"


@router.get("/thumb/{item_id}")
def inventory_thumb(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """A small JPEG for grid display, generated once and cached on disk.

    Serving the original 750x1050 scan as a grid thumbnail meant ~340KB per
    card; at a few dozen cards that dominated page load and got worse with
    every scan. Regenerating is cheap and the cache is disposable -- delete
    scan_ingest_data/media/thumbs to force a rebuild.
    """
    item = session.get(InventoryItem, item_id)
    if not item or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="no image for this item")
    source = Path(item.scan_image_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="image file missing")

    thumb = _thumb_path(item_id, source)
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                height = round(image.height * (THUMB_WIDTH / image.width))
                image.resize((THUMB_WIDTH, height), Image.LANCZOS).save(
                    thumb, "JPEG", quality=THUMB_QUALITY, optimize=True
                )
        except Exception:
            # Never fail the page over a thumbnail -- fall back to the original.
            return FileResponse(source, headers={"Cache-Control": "public, max-age=86400"})

    return FileResponse(thumb, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/image/{item_id}")
def inventory_image(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if not item or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="no image for this item")
    path = Path(item.scan_image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})
