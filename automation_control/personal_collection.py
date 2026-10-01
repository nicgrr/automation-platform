"""Personal Collection -- the CRM location for cards the user owns and
wants tracked, but hasn't decided to sell.

Distinct from normal for-sale inventory: an InventoryItem lands here
(status=PERSONAL_COLLECTION) via scripts/import_collector_export.py
rather than the scan/capture flow, and stays invisible to every other
admin page and the storefront until explicitly promoted. "List for sale"
is the one action this page offers per item -- it flips the item to
AVAILABLE (real, normal owned stock) and creates a DRAFT ShopListing
using the exact same pricing logic as scripts/list_for_sale.py (best
price on file, floored at the profit floor), then hands off to the
already-built /shop-listings review page instead of duplicating it.
"""

import mimetypes
import uuid
from decimal import Decimal
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .image_processing import auto_orient
from .models import (
    CardPrice, CardSet, CatalogCard, CatalogItem, InventoryItem, InventoryStatus, ShippingRate, ShippingZone,
    ShopListing, ShopListingStatus, TcgCard,
)
from .scan_ingest.games import GAME_LABELS
from .shop_pricing import default_costs, price_floor
from .ui import brand_header, page, pill

router = APIRouter(tags=["personal-collection"])

# Some cards (a brand-new set, a low-print premium booster) don't have a
# clean, unwatermarked photo on ANY catalogue source yet -- neither
# optcgapi nor TCGplayer, both checked by hand. For those, letting the
# user attach their own photo is the only real fix. Stored under /static
# (already mounted, see api.py) so it needs no new serving route -- and
# kept OUT of catalog_items.image_url, which is shared with the
# storefront app on a different origin where a bare /static/... path
# wouldn't resolve; this is deliberately scoped to this admin page only.
PHOTO_DIR = Path("static/personal_collection_photos")


def _save_photo(item_id: str, data: bytes, content_type: str | None) -> str:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    extension = mimetypes.guess_extension(content_type or "") or ".jpg"
    path = PHOTO_DIR / f"{item_id}{extension}"
    for stale in PHOTO_DIR.glob(f"{item_id}.*"):
        if stale != path:
            stale.unlink(missing_ok=True)
    path.write_bytes(data)
    try:
        auto_orient(path)
    except Exception:
        pass
    return f"/static/personal_collection_photos/{path.name}"

# Same trust order scripts/list_for_sale.py uses for normal stock -- a
# personal-collection item usually only has "collector_import" on file,
# which falls through to the generic "any priced row" branch below.
# "manual" wins over every live-market source, not just as a fallback when
# nothing else exists -- a human explicitly overriding a price (see
# inventory_review.py's rarity/cost edit) is a deliberate correction, and a
# stale automated re-scrape shouldn't quietly outrank it.
PREFERRED_SOURCES = ["manual", "ebay_active_low", "optcgapi_market", "pokemonpricetracker_market"]


def _best_price(session: Session, catalog_item_id: str) -> tuple[Decimal, str] | None:
    rows = session.scalars(
        select(CardPrice).where(CardPrice.card_id == catalog_item_id).order_by(CardPrice.fetched_at.desc())
    ).all()
    if not rows:
        return None
    for source in PREFERRED_SOURCES:
        for row in rows:
            if row.source == source and row.price and row.price > 0:
                return Decimal(str(row.price)), row.source
    for row in rows:
        if row.price and row.price > 0:
            return Decimal(str(row.price)), row.source
    return None


def _best_prices(session: Session, catalog_item_ids: list[str]) -> dict[str, Decimal]:
    """Batched form of _best_price, for rendering a whole page of rows
    without one CardPrice query per row."""
    if not catalog_item_ids:
        return {}
    rows = session.execute(
        select(CardPrice.card_id, CardPrice.source, CardPrice.price)
        .where(CardPrice.card_id.in_(catalog_item_ids), CardPrice.price > 0)
        .order_by(CardPrice.fetched_at.desc())
    ).all()
    by_item: dict[str, list[tuple[str, Decimal]]] = {}
    for card_id, source, price in rows:
        by_item.setdefault(card_id, []).append((source, Decimal(str(price))))

    best: dict[str, Decimal] = {}
    for card_id, observations in by_item.items():
        for source in PREFERRED_SOURCES:
            match = next((price for src, price in observations if src == source), None)
            if match is not None:
                best[card_id] = match
                break
        else:
            best[card_id] = observations[0][1]
    return best

def _money(value) -> str:
    return "—" if value is None else f"${Decimal(str(value)):,.2f}"


def _query(session: Session, game: str, q: str):
    stmt = (
        select(InventoryItem, CatalogItem, TcgCard)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .outerjoin(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .where(InventoryItem.status == InventoryStatus.PERSONAL_COLLECTION)
    )
    if game != "all":
        stmt = stmt.where(TcgCard.game == game)
    if q:
        stmt = stmt.where(CatalogItem.name.ilike(f"%{q}%"))
    return stmt


def _set_names(session: Session, tcg_cards: list[TcgCard]) -> dict[str, str]:
    set_ids = {c.set_id for c in tcg_cards if c.set_id}
    if not set_ids:
        return {}
    rows = session.execute(select(CardSet.id, CardSet.name).where(CardSet.id.in_(set_ids))).all()
    return dict(rows)


def _is_web_url(url: str | None) -> bool:
    """CatalogItem.image_url holds a local scan-ingest cache path
    (`scan_ingest_data/cache/...`) for the vast majority of Pokemon rows --
    an identification-cache artifact, not a publicly servable URL. Same
    guard storefront/common.py uses, kept local since this admin app
    doesn't import from the separate storefront package."""
    return bool(url) and (url.startswith("http://") or url.startswith("https://"))


def _reference_images(session: Session, tcg_cards: list[TcgCard]) -> dict[str, str]:
    """(set_id, number) -> real hosted image URL, from the catalog_cards
    cache built for perceptual-hash identification -- the one place a
    real URL is guaranteed for a Pokemon card, per _is_web_url above."""
    keys = {(c.set_id, c.number) for c in tcg_cards if c.game == "pokemon" and c.set_id and c.number}
    if not keys:
        return {}
    set_ids = {k[0] for k in keys}
    rows = session.execute(
        select(CatalogCard.set_id, CatalogCard.number, CatalogCard.image_url).where(CatalogCard.set_id.in_(set_ids))
    ).all()
    return {f"{set_id}|{number}": url for set_id, number, url in rows if (set_id, number) in keys and _is_web_url(url)}


def _image_url(item: InventoryItem, catalog_item: CatalogItem, tcg_card: TcgCard | None,
                reference_images: dict[str, str]) -> str | None:
    # A photo the user uploaded themselves wins outright -- they only
    # ever add one because every catalogue source came back watermarked
    # or missing, so it's never a downgrade.
    if item.scan_image_path:
        return item.scan_image_path
    if _is_web_url(catalog_item.image_url):
        return catalog_item.image_url
    if tcg_card and tcg_card.set_id and tcg_card.number:
        return reference_images.get(f"{tcg_card.set_id}|{tcg_card.number}")
    return None


def _row(item: InventoryItem, catalog_item: CatalogItem, tcg_card: TcgCard | None, set_name: str | None,
         image_url: str | None, price: Decimal | None) -> str:
    game_label = GAME_LABELS.get(tcg_card.game, tcg_card.game) if tcg_card else "—"
    set_bits = " · ".join(part for part in [set_name, tcg_card.number if tcg_card else None] if part)
    sub_parts = [p for p in [game_label, set_bits or None, item.condition] if p]
    graded = f" {pill(item.grading_company, 'ok')}" if item.grading_company else ""
    thumb = (
        f"<img class='pc-thumb' src='{escape(image_url)}' alt='' loading='lazy'>"
        if image_url else "<div class='pc-thumb pc-thumb-empty'>No image</div>"
    )
    upload_form = (
        f"<form method='post' action='/personal-collection/{escape(item.id)}/upload-photo' "
        "enctype='multipart/form-data' class='pc-photo-form'>"
        "<input type='file' name='photo' accept='image/*' required>"
        f"<button class='btn small ghost'>{'Replace' if item.scan_image_path else 'Add'} photo</button>"
        "</form>"
    )
    return (
        "<tr>"
        f"<td>{thumb}{upload_form}</td>"
        f"<td><strong>{escape(catalog_item.name)}</strong>{graded}"
        f"<div class='muted'>{escape(' · '.join(sub_parts))}</div></td>"
        f"<td>{item.quantity}</td>"
        f"<td>{_money(item.allocated_cost_basis)}</td>"
        f"<td>{_money(price)}</td>"
        f"<td class='pc-actions'>"
        # No form here -- a nested <form> inside the upload form's row
        # isn't valid HTML, and 407 near-identical single-button forms
        # (one per row, all POSTing nothing but the item id already in
        # their own URL) was most of this page's 810-form, 319KB weight.
        # HTML5's `form=`/`formaction=` let a button submit an external
        # form to its own row's URL -- see pc-list-form below -- so this
        # collapses to one shared form and 407 buttons that reference it.
        f"<button class='btn small' form='pc-list-form' "
        f"formaction='/personal-collection/{escape(item.id)}/list-for-sale'>List for sale</button></td>"
        "</tr>"
    )


SORTS = {
    "name": "Name",
    "price_desc": "Price: high to low",
    "price_asc": "Price: low to high",
    "unpriced": "Unpriced only",
}


@router.get("/personal-collection", response_class=HTMLResponse)
def personal_collection_page(
    game: str = Query("all"), q: str = Query(""), sort: str = Query("name"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> HTMLResponse:
    query = q.strip()
    game = game if game in {*GAME_LABELS, "all"} else "all"
    sort = sort if sort in SORTS else "name"

    count = len(session.execute(_query(session, game, query)).all())
    rows_data = session.execute(
        _query(session, game, query).order_by(CatalogItem.name).limit(500)
    ).all()
    tcg_cards = [tc for _, _, tc in rows_data if tc]
    set_names = _set_names(session, tcg_cards)
    reference_images = _reference_images(session, tcg_cards)
    prices = _best_prices(session, [catalog_item.id for _, catalog_item, _ in rows_data])

    if sort == "unpriced":
        # Not a sort at all -- a filter down to only the items with no
        # CardPrice on file, so they're easy to find and price up rather
        # than buried under 500 priced rows.
        rows_data = [r for r in rows_data if prices.get(r[1].id) is None]
    elif sort in ("price_desc", "price_asc"):
        # Unpriced items always sink to the bottom, in either direction --
        # "no price on file" isn't the same as "worth zero", and burying
        # priced items under a wall of unknowns would defeat the point.
        priced_rows = [r for r in rows_data if prices.get(r[1].id) is not None]
        unpriced_rows = [r for r in rows_data if prices.get(r[1].id) is None]
        priced_rows.sort(key=lambda r: prices[r[1].id], reverse=(sort == "price_desc"))
        rows_data = priced_rows + unpriced_rows

    rows = "".join(
        _row(item, catalog_item, tcg_card, set_names.get(tcg_card.set_id) if tcg_card else None,
             _image_url(item, catalog_item, tcg_card, reference_images), prices.get(catalog_item.id))
        for item, catalog_item, tcg_card in rows_data
    ) or "<tr><td colspan='6' class='muted'>Nothing here.</td></tr>"

    query_suffix = f"&q={escape(query)}" if query else ""
    sort_suffix = f"&sort={sort}" if sort != "name" else ""
    game_tabs = "".join(
        f"<a class='filter-tab{' active' if key == game else ''}' "
        f"href='/personal-collection?game={key}{query_suffix}{sort_suffix}'>{escape(label)}</a>"
        for key, label in [("all", "All"), *GAME_LABELS.items()]
    )
    sort_options = "".join(
        f"<option value='{key}'{' selected' if key == sort else ''}>{escape(label)}</option>"
        for key, label in SORTS.items()
    )
    search_form = (
        "<form method='get' action='/personal-collection' class='listing-search'>"
        f"<input type='hidden' name='game' value='{escape(game)}'>"
        f"<input name='q' value='{escape(query)}' placeholder='Search by name…'>"
        "<button class='btn small'>Search</button>"
        + (f"<a class='btn small ghost' href='/personal-collection?game={escape(game)}'>Clear</a>" if query else "")
        + f"<label class='field'>Sort<select name='sort' onchange='this.form.submit()'>{sort_options}</select></label>"
        + "</form>"
    )

    note = (
        f"<p class='subtitle'>{len(rows_data) if sort == 'unpriced' else count} item(s) "
        f"{'with no price on file' if sort == 'unpriced' else 'held privately, off the shop and out of normal inventory'}."
        f"{' Shown, capped at 500 rows -- narrow your search to see the rest.' if count > 500 else ''}"
        " Use “List for sale” to move one into review on the Shop listings page.</p>"
    )

    body = (
        brand_header("Personal Collection")
        + note
        + f"<div class='filter-tabs'>{game_tabs}</div>"
        + f"<div class='listing-toolbar'>{search_form}</div>"
        # Empty and shared: every "List for sale" button across every row
        # references this one form via form='pc-list-form' rather than
        # each carrying its own -- see _row's comment for why.
        + "<form id='pc-list-form' method='post'></form>"
        + "<div class='panel'><div class='table-wrap'><table><thead><tr>"
        + "<th></th><th>Item</th><th>Qty</th><th>Cost paid</th><th>Price</th><th></th>"
        + f"</tr></thead><tbody>{rows}</tbody></table></div></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Personal Collection", body))


@router.post("/personal-collection/{item_id}/list-for-sale")
def list_item_for_sale(
    item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item = session.get(InventoryItem, item_id)
    if item is None or item.status != InventoryStatus.PERSONAL_COLLECTION:
        raise HTTPException(status_code=404, detail="personal collection item not found")

    catalog_item = session.get(CatalogItem, item.catalog_item_id)
    found = _best_price(session, item.catalog_item_id)
    if catalog_item is None or found is None:
        raise HTTPException(status_code=400, detail="no price on file for this item -- can't list without one")
    market, source = found

    au_rate = session.scalar(select(ShippingRate).where(ShippingRate.zone == ShippingZone.AU).limit(1))
    floor_model = price_floor(
        default_costs(session), shipping_charged=Decimal(str(au_rate.price)) if au_rate else Decimal("0"),
    )
    price = max(market, floor_model.floor).quantize(Decimal("0.01"))

    tcg_card = session.get(TcgCard, item.catalog_item_id)
    title = catalog_item.name
    if tcg_card and tcg_card.number:
        title = f"{catalog_item.name} #{tcg_card.number}"
    variant = item.variant.value.replace("_", " ").title() if item.variant else None
    condition = " · ".join(part for part in [item.condition, variant] if part)

    item.status = InventoryStatus.AVAILABLE
    session.add(ShopListing(
        catalog_item_id=item.catalog_item_id, inventory_item_id=item.id,
        title=title, condition=condition or None, price=price, currency="AUD",
        quantity=item.quantity, status=ShopListingStatus.DRAFT,
        image_url=catalog_item.image_url,
    ))
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="personal_collection.list_for_sale",
                resource_type="inventory_item", resource_id=item_id, outcome="success",
                correlation_id=item_id, details={"price": str(price), "source": source})
    return RedirectResponse("/shop-listings?status=draft", status_code=303)


@router.post("/personal-collection/{item_id}/upload-photo")
async def upload_photo(
    item_id: str, photo: UploadFile = File(...),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    item = session.get(InventoryItem, item_id)
    if item is None or item.status != InventoryStatus.PERSONAL_COLLECTION:
        raise HTTPException(status_code=404, detail="personal collection item not found")

    item.scan_image_path = _save_photo(item_id, await photo.read(), photo.content_type)
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="personal_collection.upload_photo",
                resource_type="inventory_item", resource_id=item_id, outcome="success",
                correlation_id=item_id, details={})
    return RedirectResponse("/personal-collection", status_code=303)


_STYLE = """<style>
.filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}
.filter-tab{padding:7px 13px;border-radius:999px;font-size:13px;font-weight:600;
  background:var(--surface-sunken);color:var(--text-dim)}
.filter-tab.active{background:var(--accent);color:#fff}
.listing-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:14px}
.listing-search{display:flex;gap:8px}
.listing-search input{padding:7px 10px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px;width:220px}
.listing-search .field{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--text-dim)}
.listing-search .field select{padding:6px 8px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px}
.pc-actions{display:flex;gap:6px}
.btn.small{padding:6px 12px;font-size:13px}
.pc-thumb{width:48px;height:64px;object-fit:contain;border-radius:4px;background:var(--surface-sunken);display:block}
.pc-thumb-empty{display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--text-dim);text-align:center}
.pc-photo-form{display:flex;flex-direction:column;gap:4px;margin-top:6px;width:70px}
.pc-photo-form input[type=file]{font-size:10px;width:70px}
.pc-photo-form .btn.small{padding:3px 6px;font-size:11px}
@media (max-width: 680px) {
  .listing-toolbar{flex-direction:column;align-items:stretch}
  .listing-search{flex-wrap:wrap}
  .listing-search input{width:100%}
}
</style>"""
