"""Browse and adjust the scan-ingest pipeline's inventory: what's actually
been scanned, identified, and committed (automation_control/scan_ingest/).

Almost read-only. The single write is `inventory_adjust`, which changes how
many of a card you hold -- and deliberately nothing else. It cannot change
which card a holding *is*, because getting that wrong is what put a Joltik
in the inventory as a Gengar; re-identifying is the review queue's job.
Every adjustment is audited like the rest of this app's mutating actions.

Organised set-first: the landing page is a grid of set tiles (one aggregate
query, no card images at all), and cards live behind a set. The previous
version rendered every owned card on one page with a full-size 750x1050
scan as its thumbnail -- tens of megabytes for a page whose job is "what do
I own", getting worse with every scan.

A set page lists the *whole* set, not just what's owned, so "Progress:
15/114" is something you can actually see against. Card faces come from the
cached reference art (uniformly 245x342, already on disk from cache-set)
rather than the scans, so every tile is the same size and shape; the scan
of your own copy is one click away.

Values are shown in AUD (converted at display time, see scan_ingest/fx.py);
`CardPrice` rows keep their source currency.
"""

from html import escape
from pathlib import Path

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .models import CardPrice, CardSet, CardVariant, CatalogCard, InventoryItem
from .scan_ingest import fx
from .ui import brand_header, page

router = APIRouter(prefix="/inventory", tags=["inventory"])

# Scans are 750x1050; reference art is 245x342. Both are resized to one
# width so every tile in a grid is identical regardless of source.
THUMB_WIDTH = 220
THUMB_QUALITY = 78

SET_SORTS = {
    "released": "Newest first",
    "released_asc": "Oldest first",
    "name": "Name (A-Z)",
    "value": "Value",
    "progress": "Most collected",
}
CARD_SORTS = {"number": "Card number", "value_desc": "Value", "quantity_desc": "Most copies"}

# What a manually-added card is assumed to be. Scanning records the real
# condition; this is only for "I own this but never scanned it".
DEFAULT_CONDITION = "Near Mint"

# TCGPlayer's own printing keys, which is how we learn a card exists in a
# reverse-holo or holo printing at all -- the catalog's card record doesn't
# say. A card with no pricing data falls back to showing the normal print.
_VARIANT_BY_TCG_KEY = {
    "normal": CardVariant.NORMAL,
    "unlimited": CardVariant.NORMAL,
    "reverseHolofoil": CardVariant.REVERSE_HOLO,
    "holofoil": CardVariant.HOLO,
    "1stEditionHolofoil": CardVariant.HOLO,
}
_VARIANT_ORDER = {CardVariant.NORMAL: 0, CardVariant.REVERSE_HOLO: 1, CardVariant.HOLO: 2}


def _variants_for(card: CatalogCard, owned: set[CardVariant]) -> list[CardVariant]:
    """Which printings to show for a card: every one it's known to exist in,
    plus any actually owned (which is authoritative even if pricing data
    doesn't mention that printing)."""
    found = set(owned)
    tcgplayer = (card.raw_prices or {}).get("tcgplayer") or {}
    for key in (tcgplayer.get("prices") or {}):
        variant = _VARIANT_BY_TCG_KEY.get(key)
        if variant:
            found.add(variant)
    return sorted(found or {CardVariant.NORMAL}, key=lambda v: _VARIANT_ORDER.get(v, 9))


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


def _sort_control(options: dict[str, str], current: str, name: str = "sort") -> str:
    opts = "".join(
        f"<option value='{escape(k)}'{' selected' if k == current else ''}>{escape(v)}</option>"
        for k, v in options.items()
    )
    return (
        f"<label class='sort-control'>Sort"
        f"<select name='{name}' onchange=\"location.search=new URLSearchParams("
        f"Object.assign(Object.fromEntries(new URLSearchParams(location.search)),{{{name}:this.value}}))\">"
        f"{opts}</select></label>"
    )



def _edit_controls(set_id: str, card: CatalogCard, variant: CardVariant,
                   quantity: int, sort: str, owned_only: str) -> str:
    """Per-card quantity controls.

    Every button is its own form post rather than JavaScript, so the page
    keeps working the way the rest of this app does and a mis-tap is a
    normal browser action. `back` carries the current view so a change
    doesn't dump you at the top of an unfiltered set.
    """
    back = f"/inventory/set/{escape(set_id)}?sort={escape(sort)}" + ("&owned_only=1" if owned_only else "")
    common = (
        f"<input type='hidden' name='card_id' value='{escape(card.id)}'>"
        f"<input type='hidden' name='variant' value='{escape(variant.value)}'>"
        f"<input type='hidden' name='back' value='{escape(back)}'>"
    )
    if not quantity:
        return (
            "<form class='card-edit' method='post' action='/inventory/adjust'>"
            f"{common}<button name='action' value='add' class='add'>+ Add</button></form>"
        )
    return (
        "<form class='card-edit' method='post' action='/inventory/adjust'>"
        f"{common}"
        "<button name='action' value='dec' aria-label='One fewer'>&minus;</button>"
        f"<span class='qty'>{quantity}</span>"
        "<button name='action' value='add' aria-label='One more'>+</button>"
        "<button name='action' value='remove' class='ghost' aria-label='Remove all'>Remove</button>"
        "</form>"
    )


# --- set grid (landing) ----------------------------------------------------

@router.get("", response_class=HTMLResponse)
def inventory_sets(
    request: Request,
    sort: str = Query("released"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Every set you own cards from, as tiles. Deliberately loads no card
    images: this is the "what do I have" view, and set artwork is a handful
    of small cached logos rather than one scan per card."""
    if sort not in SET_SORTS:
        sort = "released"

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

    items = list(session.scalars(
        select(InventoryItem).join(CatalogCard, InventoryItem.card_id == CatalogCard.id)
        .where(CatalogCard.set_id.in_(set_ids))
    ))
    prices = _latest_prices(session, {i.card_id for i in items})
    set_of = {c.id: c.set_id for c in session.scalars(select(CatalogCard).where(CatalogCard.id.in_({i.card_id for i in items})))}

    value_by_set: dict[str, float] = {}
    for item in items:
        line = _aud(prices.get((item.card_id, item.variant)), item.quantity)
        if line:
            value_by_set[set_of[item.card_id]] = value_by_set.get(set_of[item.card_id], 0.0) + line

    def released(row) -> str:
        # ISO-ish "YYYY/MM/DD" sorts correctly as a string; empty dates sort
        # last on newest-first rather than pretending to be ancient.
        return (sets[row.set_id].release_date if sets.get(row.set_id) else "") or ""

    if sort == "released":
        rows = sorted(owned, key=lambda r: (released(r) or "0000", r.set_id), reverse=True)
    elif sort == "released_asc":
        rows = sorted(owned, key=lambda r: (released(r) or "9999", r.set_id))
    elif sort == "name":
        rows = sorted(owned, key=lambda r: (sets[r.set_id].name if sets.get(r.set_id) else r.set_id).lower())
    elif sort == "progress":
        rows = sorted(owned, key=lambda r: -(r.distinct_cards or 0))
    else:
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
        release = (card_set.release_date if card_set else "") or ""
        pct = (row.distinct_cards / printed * 100) if printed else 0

        art = (
            f"<img src='{escape(logo)}' alt='' loading='lazy' decoding='async'>"
            if logo else f"<span class='set-fallback'>{escape(name)}</span>"
        )
        tiles.append(
            f"<a class='set-tile' href='/inventory/set/{escape(row.set_id)}'>"
            f"<div class='set-art'>{art}"
            f"{f'<span class=set-date>{escape(release)}</span>' if release else ''}"
            f"{f'<span class=set-pct>{pct:.0f}%</span>' if pct >= 1 else ''}"
            "</div>"
            f"<div class='set-name'>{escape(name)}</div>"
            f"<div class='set-meta'>Progress: {row.distinct_cards}{'/' + str(printed) if printed else ''}</div>"
            f"<div class='set-meta'>Total Value: <strong>{_money(value_by_set.get(row.set_id))}</strong></div>"
            "</a>"
        )

    rate_note = "" if fx.is_live() else " (approximate rate — live FX unavailable)"
    stat_grid = (
        "<div class='stat-grid'>"
        f"<div class='stat-card'><div class='label'>Sets</div><div class='value'>{len(rows)}</div></div>"
        f"<div class='stat-card'><div class='label'>Distinct cards</div><div class='value'>{total_distinct}</div></div>"
        f"<div class='stat-card'><div class='label'>Total copies</div><div class='value'>{total_copies}</div></div>"
        f"<div class='stat-card'><div class='label'>Estimated value</div><div class='value'>{_money(total_value)}"
        "<span class='unit'> AUD</span></div></div>"
        "</div>"
    )

    body = (
        brand_header("Inventory")
        + f"<p class='subtitle'>Cards committed by the scan-ingest pipeline. Values are rough "
          f"pokemontcg.io estimates converted to AUD{rate_note} — not listing prices.</p>"
        + stat_grid
        + "<div class='toolbar'>"
        + "<input id='set-search' class='search-box' type='search' placeholder='Search sets…' autocomplete='off'>"
        + _sort_control(SET_SORTS, sort)
        + "</div>"
        + f"<div class='set-grid' id='set-grid'>{''.join(tiles)}</div>"
        + _STYLE
        + _SEARCH_SCRIPT
    )
    return HTMLResponse(page("EzBay — Inventory", body))


# --- one set's cards -------------------------------------------------------

@router.get("/set/{set_id}", response_class=HTMLResponse)
def inventory_set(
    set_id: str,
    request: Request,
    variant: str = Query(""),
    sort: str = Query("number"),
    owned_only: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    card_set = session.get(CardSet, set_id)
    if card_set is None:
        raise HTTPException(status_code=404, detail=f"unknown set: {set_id!r}")
    if sort not in CARD_SORTS:
        sort = "number"

    variant_filter: CardVariant | None = None
    if variant:
        try:
            variant_filter = CardVariant(variant)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown variant: {variant!r}")

    # The whole set, so "Progress: 15/114" is visible rather than asserted.
    catalog_cards = list(session.scalars(select(CatalogCard).where(CatalogCard.set_id == set_id)))

    item_query = select(InventoryItem).join(CatalogCard, InventoryItem.card_id == CatalogCard.id).where(CatalogCard.set_id == set_id)
    if variant_filter is not None:
        item_query = item_query.where(InventoryItem.variant == variant_filter)
    items = list(session.scalars(item_query))

    held_by: dict[tuple[str, CardVariant], list[InventoryItem]] = {}
    for item in items:
        held_by.setdefault((item.card_id, item.variant), []).append(item)

    prices = _latest_prices(session, {c.id for c in catalog_cards})
    printed = card_set.printed_total or len(catalog_cards)

    # One row per (card, printing) -- the same card in normal and reverse
    # holo are different things to own and are priced differently.
    rows = []
    for card in catalog_cards:
        owned_variants = {v for (cid, v) in held_by if cid == card.id}
        for variant in _variants_for(card, owned_variants):
            if variant_filter is not None and variant != variant_filter:
                continue
            held = held_by.get((card.id, variant), [])
            quantity = sum(i.quantity for i in held)
            if owned_only and not quantity:
                continue
            rows.append({
                "card": card, "variant": variant, "quantity": quantity, "held": held,
                "price": _aud(prices.get((card.id, variant))),
            })

    if sort == "value_desc":
        rows.sort(key=lambda r: r["price"] or 0.0, reverse=True)
    elif sort == "quantity_desc":
        rows.sort(key=lambda r: (-r["quantity"], _sort_number(r["card"].number)))
    else:
        rows.sort(key=lambda r: (_sort_number(r["card"].number), _VARIANT_ORDER.get(r["variant"], 9)))

    distinct_owned = len({cid for (cid, _) in held_by})
    total_copies = sum(i.quantity for i in items)
    total_value = sum((r["price"] or 0.0) * r["quantity"] for r in rows)

    tiles = []
    for row in rows:
        card, variant, quantity, held = row["card"], row["variant"], row["quantity"], row["held"]
        face = (
            f"<img src='/inventory/card-thumb/{escape(card.id)}' alt='' loading='lazy' decoding='async'>"
            if card.local_image_path else "<span class='no-art'>no image</span>"
        )
        # An owned card's face links to your own scan; an unowned one has
        # nothing to show, so it stays inert rather than a dead link. The
        # tile itself is never a link -- it holds the edit controls, and
        # buttons inside an anchor is not valid markup.
        scan_href = f"/inventory/image/{held[0].id}" if held and held[0].scan_image_path else None
        face_block = (
            f"<a class='card-face' href='{scan_href}'>{face}"
            f"{f'<span class=qty-badge>x{quantity}</span>' if quantity > 1 else ''}</a>"
            if scan_href else
            f"<div class='card-face'>{face}"
            f"{f'<span class=qty-badge>x{quantity}</span>' if quantity > 1 else ''}</div>"
        )
        # Searchable without a round trip: the whole set is already on the
        # page, so filtering it is a keystroke rather than a request.
        haystack = f"{card.name} {card.number} {card.rarity or ''} {variant.value}".lower()

        tiles.append(
            f"<div class='card-tile{'' if quantity else ' unowned'}' data-find='{escape(haystack)}'>"
            f"{face_block}"
            f"<div class='card-name'>{escape(card.name)}</div>"
            f"<div class='card-sub'>{escape(card_set.name)}</div>"
            f"<div class='card-sub'>{escape(card.rarity or 'Unknown')} &bull; {escape(card.number)}/{printed}</div>"
            f"<div class='card-sub'>{escape(variant.value.replace('_', ' ').title())}</div>"
            f"<div class='card-price'>{_money(row['price'])}</div>"
            f"<div class='card-qty{'' if quantity else ' zero'}'>Qty: {quantity}</div>"
            + _edit_controls(set_id, card, variant, quantity, sort, owned_only)
            + "</div>"
        )

    header = (
        "<div class='set-header'>"
        + (f"<img class='set-header-logo' src='{escape(card_set.logo_url)}' alt=''>" if card_set.logo_url else "")
        + "<div class='set-header-text'>"
        + f"<div class='set-header-name'>{escape(card_set.name)}</div>"
        + f"<div class='set-meta'>Progress: {distinct_owned}/{printed}</div>"
        + f"<div class='set-meta'>Total Value: <strong>{_money(total_value)}</strong> "
          f"<span class='unit'>AUD</span> &middot; {total_copies} copies</div>"
        + "</div></div>"
    )

    toggle_label = "Show all cards" if owned_only else "Owned only"
    toggle_href = f"/inventory/set/{escape(set_id)}?sort={escape(sort)}" + ("" if owned_only else "&owned_only=1")

    body = (
        brand_header("Inventory")
        + f"<p class='subtitle'><a href='/inventory'>&larr; All sets</a></p>"
        + header
        + "<div class='toolbar'>"
        + "<input id='card-search' class='search-box' type='search' "
          "placeholder='Find a card by name or number…' autocomplete='off'>"
        + f"<a class='chip' href='{toggle_href}'>{toggle_label}</a>"
        + _sort_control(CARD_SORTS, sort)
        + "</div>"
        + "<div class='found' id='found'></div>"
        + (f"<div class='card-grid' id='card-grid'>{''.join(tiles)}</div>" if tiles
           else "<div class='panel'><p>No cards to show.</p></div>")
        + _STYLE
        + _CARD_SEARCH_SCRIPT
    )
    return HTMLResponse(page(f"EzBay — {card_set.name}", body))


def _sort_number(number: str) -> tuple[int, str]:
    """Sort card numbers numerically ('2' before '10') with a text fallback
    for the non-numeric ones some sets have (promos, 'SWSH001')."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return (int(digits) if digits else 0, number)



@router.post("/adjust")
def inventory_adjust(
    card_id: str = Form(...),
    variant: str = Form(...),
    action: str = Form(...),
    back: str = Form("/inventory"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Change how many of a card you hold.

    The only writing this router does. Kept deliberately narrow: it adjusts
    quantity on an existing holding, creates one for a card you own but
    never scanned, or removes one entirely -- it cannot change which card a
    holding *is*, because getting that wrong is what put a Joltik in the
    inventory as a Gengar. Re-identifying is the review queue's job.
    """
    card = session.get(CatalogCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"unknown card {card_id!r}")
    try:
        printing = CardVariant(variant)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown variant: {variant!r}")

    held = list(session.scalars(select(InventoryItem).where(
        InventoryItem.card_id == card.id, InventoryItem.variant == printing)))
    if len(held) > 1:
        # Several conditions of the same printing: which one to change isn't
        # ours to guess, and guessing would silently edit the wrong holding.
        raise HTTPException(
            status_code=400,
            detail=f"{card.name} is held in {len(held)} conditions -- adjust it from the CLI so the right one is chosen",
        )
    item = held[0] if held else None

    if action == "add":
        if item is None:
            item = InventoryItem(card_id=card.id, variant=printing, condition=DEFAULT_CONDITION, quantity=1)
            session.add(item)
            outcome, quantity = "created", 1
        else:
            item.quantity += 1
            outcome, quantity = "incremented", item.quantity
    elif action == "dec":
        if item is None:
            raise HTTPException(status_code=400, detail="nothing to decrement")
        item.quantity -= 1
        outcome, quantity = ("removed", 0) if item.quantity <= 0 else ("decremented", item.quantity)
        if item.quantity <= 0:
            session.delete(item)
    elif action == "remove":
        if item is None:
            raise HTTPException(status_code=400, detail="nothing to remove")
        session.delete(item)
        outcome, quantity = "removed", 0
    else:
        raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")

    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="inventory.adjust",
        resource_type="inventory_item", resource_id=card.id, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"card_id": card.id, "card_name": card.name, "number": card.number,
                 "set_id": card.set_id, "variant": printing.value,
                 "change": action, "result": outcome, "quantity": quantity},
    )
    # Only our own set pages are acceptable destinations -- `back` comes
    # from a form field, and an open redirect is not worth the convenience.
    destination = back if back.startswith("/inventory") else "/inventory"
    return RedirectResponse(destination, status_code=303)


# --- images ----------------------------------------------------------------

def _thumb_dir() -> Path:
    directory = Path(get_settings().scan_media_dir) / "thumbs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _serve_thumb(source: Path, cache_name: str) -> FileResponse:
    """Resize once to THUMB_WIDTH and cache on disk.

    Every tile in a grid is the same width regardless of whether it came
    from a 750x1050 scan or 245x342 reference art, and a page of them costs
    kilobytes rather than megabytes. Never fails the page: an unreadable
    source falls back to serving the original.
    """
    thumb = _thumb_dir() / cache_name
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
            return FileResponse(source, headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(thumb, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/card-thumb/{card_id}")
def inventory_card_thumb(card_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Reference art for a catalog card, resized. Used for every tile on a
    set page so they're uniform whether or not the card is owned."""
    card = session.get(CatalogCard, card_id)
    if not card or not card.local_image_path:
        raise HTTPException(status_code=404, detail="no reference image for this card")
    source = Path(card.local_image_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="reference image missing")
    return _serve_thumb(source, f"card-{card_id.replace('/', '_')}-{THUMB_WIDTH}.jpg")


@router.get("/thumb/{item_id}")
def inventory_thumb(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """A small JPEG of your own scan."""
    item = session.get(InventoryItem, item_id)
    if not item or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="no image for this item")
    source = Path(item.scan_image_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return _serve_thumb(source, f"{item_id}-{THUMB_WIDTH}.jpg")


@router.get("/image/{item_id}")
def inventory_image(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if not item or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="no image for this item")
    path = Path(item.scan_image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="image file missing")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


_STYLE = """<style>
.toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:0 0 20px}
.search-box{flex:1 1 240px;max-width:420px;background:#0a0f1c;border:1px solid var(--panel-border);
  border-radius:10px;padding:11px 14px;color:var(--text);font-size:14px}
.search-box:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(94,234,255,.15)}
.sort-control{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-dim);
  text-transform:uppercase;letter-spacing:.06em}
.sort-control select{background:#0a0f1c;border:1px solid var(--panel-border);border-radius:9px;
  padding:9px 11px;color:var(--text);font-size:13px;text-transform:none;letter-spacing:normal}
.chip{display:inline-flex;align-items:center;padding:9px 14px;border-radius:999px;font-size:13px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:#0a0f1c}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.unit{font-size:12px;color:var(--text-dim)}

.set-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:18px;align-items:start}
a.set-tile{display:flex;flex-direction:column;gap:3px;text-decoration:none;color:var(--text);min-width:0}
a.set-tile>*{min-width:0;max-width:100%}
.set-art{position:relative;display:grid;place-items:center;height:104px;border-radius:12px;
  background:#f4f5f7;border:1px solid var(--panel-border);overflow:hidden;
  transition:transform .15s ease,box-shadow .15s ease}
.set-art img{max-width:82%;max-height:64px;object-fit:contain}
.set-fallback{font-weight:700;font-size:15px;color:#12161c;padding:0 8px;text-align:center}
a.set-tile:hover .set-art{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.35)}
.set-date{position:absolute;top:7px;right:7px;background:rgba(255,255,255,.9);color:#12161c;
  font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:999px}
.set-pct{position:absolute;bottom:0;left:0;background:var(--accent);color:#04101a;
  font-size:10.5px;font-weight:700;padding:1px 6px;border-top-right-radius:6px}
.set-name{font-weight:600;font-size:14px;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.set-meta{font-size:12.5px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.set-meta strong{color:var(--text)}

.set-header{display:flex;gap:16px;align-items:center;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:14px;padding:16px 18px;margin-bottom:20px}
.set-header-logo{width:120px;max-height:62px;object-fit:contain;background:#f4f5f7;
  border-radius:9px;padding:7px;flex:none}
.set-header-name{font-size:17px;font-weight:650;margin-bottom:3px}

.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:18px;align-items:start}
.card-tile{display:flex;flex-direction:column;gap:2px;text-decoration:none;color:var(--text);min-width:0;overflow:hidden;
  background:var(--panel);border:1px solid var(--panel-border);border-radius:14px;padding:12px}
a.card-tile:hover{border-color:var(--accent)}
.card-tile>*{min-width:0;max-width:100%}
.card-tile.unowned{opacity:.5}
.card-face{position:relative;border-radius:9px;overflow:hidden;background:#0a0f1c;width:100%;
  aspect-ratio:245/342;display:grid;place-items:center;margin-bottom:9px}
.card-face img{width:100%;height:100%;object-fit:contain;display:block}
.no-art{color:var(--text-dim);font-size:12px}
.qty-badge{position:absolute;top:6px;right:6px;background:var(--accent);color:#04101a;
  font-size:11px;font-weight:700;padding:1px 7px;border-radius:999px}
.card-name{font-weight:650;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-sub{font-size:12px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-price{font-size:15px;font-weight:600;margin-top:6px}
.card-qty{font-size:12px;color:var(--text-dim)}
.card-qty.zero{color:var(--text-dim);opacity:.75}

.card-edit{display:flex;align-items:center;gap:6px;margin-top:9px;max-width:none;min-width:0}
.card-edit button{flex:0 0 auto;min-width:34px;padding:7px 9px;border-radius:8px;font-size:14px;
  font-weight:700;cursor:pointer;border:1px solid var(--panel-border);background:#0a0f1c;color:var(--text)}
.card-edit button:hover{border-color:var(--accent);color:var(--accent)}
.card-edit button.add{flex:1 1 auto;background:linear-gradient(120deg,var(--accent),var(--accent-2));
  color:#04101a;border:none}
.card-edit button.ghost{flex:1 1 auto;font-size:12px;font-weight:600;color:var(--text-dim)}
.card-edit button.ghost:hover{border-color:var(--status-critical,#f87171);color:var(--status-critical,#f87171)}
.card-edit .qty{flex:1 1 auto;text-align:center;font-size:14px;font-weight:650;font-variant-numeric:tabular-nums}
.found{font-size:13px;color:var(--text-dim);margin:-8px 0 14px;min-height:1em}

@media (max-width:560px){
  .set-grid{grid-template-columns:repeat(2,1fr);gap:14px}
  .set-art{height:84px}
  .card-grid{grid-template-columns:repeat(2,1fr);gap:12px}
  .set-header-logo{width:92px}
}
</style>"""

_CARD_SEARCH_SCRIPT = """<script>
(function(){
  var box=document.getElementById('card-search'), grid=document.getElementById('card-grid'),
      found=document.getElementById('found');
  if(!box||!grid)return;
  var tiles=[].slice.call(grid.querySelectorAll('.card-tile'));
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase(), shown=0;
    tiles.forEach(function(t){
      var hit=!q||(t.getAttribute('data-find')||'').indexOf(q)>-1;
      t.style.display=hit?'':'none';
      if(hit)shown++;
    });
    found.textContent=q?(shown+' of '+tiles.length+' cards'):'';
  });
})();
</script>"""

_SEARCH_SCRIPT = """<script>
(function(){
  var box=document.getElementById('set-search');
  if(!box)return;
  box.addEventListener('input',function(){
    var q=box.value.trim().toLowerCase();
    document.querySelectorAll('#set-grid .set-tile').forEach(function(t){
      var n=(t.querySelector('.set-name')||{}).textContent||'';
      t.style.display=n.toLowerCase().includes(q)?'':'none';
    });
  });
})();
</script>"""
