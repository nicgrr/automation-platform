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

from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from .audit import record_event
from .auth import require_dashboard_user
from .database import get_session
from .images import serve_image, serve_thumb
from .models import (
    CardPrice, CardSet, CardVariant, CatalogCard, CatalogItem, CollectibleProduct, InventoryItem, SealedProduct,
    StorageLocation, TcgCard,
)
from .scan_ingest import fx
from .scan_ingest.commit import _catalog_item_id_for
from .scan_ingest.games import GAME_LABELS
from .ui import brand_header, page

router = APIRouter(prefix="/inventory", tags=["inventory"])

# Scans are 750x1050; reference art is 245x342. Both are resized to one
# width so every tile in a grid is identical regardless of source.
THUMB_WIDTH = 220
THUMB_QUALITY = 78

# The tab bar Nic asked for: One Piece / Dragon Ball / Pokémon, each
# possibly spanning several CardSet.game values (all four Dragon Ball
# games collapse into one tab; English and Japanese Pokémon collapse into
# one). Collectibles and Misc aren't here -- they're plain links out to a
# different page (see inventory_sets' docstring), not a `group` value.
# logo: for the category tile on the /inventory landing page only (not
# used per-set) -- one small, stable, direct file link per franchise via
# Wikimedia's Special:FilePath redirect (https://commons.wikimedia.org/wiki/
# Special:FilePath/<file>), so it survives that file's actual storage path
# changing. Dragon Ball uses the dragon-ball-orb icon rather than a
# Z-branded wordmark since the group spans several Dragon Ball games, not
# just Z.
GAME_GROUPS: dict[str, dict] = {
    "one_piece": {
        "label": "One Piece", "games": {"one_piece"},
        "logo": "https://upload.wikimedia.org/wikipedia/commons/6/6c/One_piece_logo.svg",
    },
    "dragon_ball": {
        "label": "Dragon Ball",
        "games": {
            "dragon_ball_fusion_world", "dragon_ball_super", "dragon_ball_z",
            "super_dragon_ball_heroes", "dragon_ball_super_divers",
        },
        "logo": "https://upload.wikimedia.org/wikipedia/commons/9/96/Dragon_ball.svg",
    },
    "pokemon": {
        "label": "Pokémon", "games": {"pokemon", "pokemon_japan"},
        "logo": "https://upload.wikimedia.org/wikipedia/commons/9/98/International_Pok%C3%A9mon_logo.svg",
    },
}

SET_SORTS = {
    "released": "Newest first",
    "released_asc": "Oldest first",
    "name": "Name (A-Z)",
    "value": "Value",
    "progress": "Most collected",
}
CARD_SORTS = {
    "number": "Card number (low–high)",
    "number_desc": "Card number (high–low)",
    "name": "Name (A–Z)",
    "name_desc": "Name (Z–A)",
    "value_desc": "Value (high–low)",
    "value_asc": "Value (low–high)",
    "quantity_desc": "Most copies",
    "recent": "Recently added",
    "rarity": "Rarity",
}
ALL_CARD_SORTS = {
    "recent": "Recently added",
    "name": "Name (A–Z)",
    "name_desc": "Name (Z–A)",
    "value_desc": "Value (high–low)",
    "value_asc": "Value (low–high)",
    "quantity_desc": "Most copies",
    "number": "Card number (low–high)",
    "number_desc": "Card number (high–low)",
    "set": "Set name (A–Z)",
    "rarity": "Rarity",
}

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
_VARIANT_ORDER = {
    CardVariant.NORMAL: 0, CardVariant.REVERSE_HOLO: 1, CardVariant.HOLO: 2,
    CardVariant.POKEBALL_PATTERN: 3, CardVariant.MASTERBALL_PATTERN: 4,
}


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
    """Most recent CardPrice per (card_id, variant), picked in SQL via
    ROW_NUMBER() rather than pulling every historical row into Python and
    picking first-seen-wins by hand. That Python-side pass was ~73% of
    dashboard render time once card_prices passed 40k+ rows (diagnosed
    2026-09-15, never fixed until now) -- this is a drop-in replacement:
    same signature, same return shape, callers unchanged. Tie-breaking on
    fetched_at was already arbitrary before this change (no id tiebreaker
    existed either); ROW_NUMBER() preserves that, doesn't newly introduce it."""
    if not card_ids:
        return {}
    rn = func.row_number().over(
        partition_by=(CardPrice.card_id, CardPrice.variant), order_by=CardPrice.fetched_at.desc()
    ).label("rn")
    subq = select(CardPrice, rn).where(CardPrice.card_id.in_(card_ids)).subquery()
    latest_price = aliased(CardPrice, subq)
    rows = session.scalars(select(latest_price).where(subq.c.rn == 1))
    return {(row.card_id, row.variant): row for row in rows}


EBAY_PRICE_SOURCE = "ebay_active_low"


def _latest_ebay_prices(session: Session, card_ids: set[str]) -> dict[str, CardPrice]:
    """Most recent eBay active-listing price per card, keyed by card alone
    rather than (card, variant) like _latest_prices.

    An eBay search can't reliably tell a reverse holo from a normal print
    -- listings often don't say -- so these are deliberately stored with
    variant=None rather than claiming a precision they don't have. That
    also means they can't be looked up the variant-accurate way, hence
    this separate helper. `ebay_active_low` (the cheapest active listing)
    is used rather than the median: it's the more conservative figure to
    value your own holdings against.
    """
    if not card_ids:
        return {}
    rn = func.row_number().over(
        partition_by=CardPrice.card_id, order_by=CardPrice.fetched_at.desc()
    ).label("rn")
    subq = (
        select(CardPrice, rn)
        .where(CardPrice.card_id.in_(card_ids), CardPrice.source == EBAY_PRICE_SOURCE)
        .subquery()
    )
    latest_price = aliased(CardPrice, subq)
    rows = session.scalars(select(latest_price).where(subq.c.rn == 1))
    return {row.card_id: row for row in rows}


def _aud(price: CardPrice | None, quantity: int = 1) -> float | None:
    if price is None:
        return None
    converted = fx.to_aud(price.price, price.currency)
    return None if converted is None else converted * quantity


def _money(value: float | None) -> str:
    return "&mdash;" if value is None else f"${value:,.2f}"


def _sort_card_rows(rows: list[dict], sort: str) -> None:
    """Sort both the one-set and all-inventory card views consistently.

    Missing prices always sink to the bottom. A reverse sort on the raw
    value would otherwise put "no price" ahead of a real $0.50 card in the
    ascending view, which reads as though unknown means free.
    """
    if sort == "value_desc":
        rows.sort(key=lambda r: (r["price"] is None, -(r["price"] or 0.0), r["card"].name.lower()))
    elif sort == "value_asc":
        rows.sort(key=lambda r: (r["price"] is None, r["price"] or 0.0, r["card"].name.lower()))
    elif sort == "quantity_desc":
        rows.sort(key=lambda r: (-r["quantity"], r["card"].name.lower()))
    elif sort == "rarity":
        rows.sort(key=lambda r: ((r["card"].rarity or "Unknown").lower(), r["card"].name.lower()))
    elif sort == "name":
        rows.sort(key=lambda r: (r["card"].name.lower(), _sort_number(r["card"].number)))
    elif sort == "name_desc":
        rows.sort(key=lambda r: (r["card"].name.lower(), _sort_number(r["card"].number)), reverse=True)
    elif sort == "number_desc":
        rows.sort(key=lambda r: (_sort_number(r["card"].number), r["card"].name.lower()), reverse=True)
    elif sort == "recent":
        rows.sort(key=lambda r: (str(r.get("added_at") or ""), r["card"].name.lower()), reverse=True)
    elif sort == "set":
        rows.sort(key=lambda r: (r.get("set_name", "").lower(), _sort_number(r["card"].number)))
    else:
        rows.sort(key=lambda r: (_sort_number(r["card"].number), _VARIANT_ORDER.get(r["variant"], 9)))


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
                   quantity: int, sort: str, show_owned_only: bool,
                   back: str | None = None) -> str:
    """Per-card quantity controls.

    Every button is its own form post rather than JavaScript, so the page
    keeps working the way the rest of this app does and a mis-tap is a
    normal browser action. `back` carries the current view so a change
    doesn't dump you at the top of an unfiltered set.
    """
    back = back or f"/inventory/set/{escape(set_id)}?sort={escape(sort)}&owned_only={'1' if show_owned_only else '0'}"
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
        # onclick, not the form's own onsubmit -- this one form also holds
        # the +/- quantity buttons, which must never trigger a confirm
        # dialog; returning false here cancels only this button's own
        # submission, the same way the other two buttons' clicks are
        # otherwise unaffected.
        "<button name='action' value='remove' class='ghost' aria-label='Remove all' "
        "onclick=\"return confirm('Remove all copies of this card?');\">Remove</button>"
        "</form>"
    )


def _details_edit_html(set_id: str, card: CatalogCard, variant: CardVariant, held: list[InventoryItem],
                       sort: str, show_owned_only: bool, cost: float | None) -> str:
    """Rarity/Cost/Condition, editable -- deliberately NOT which card a
    holding is (see this module's own docstring on why that stays out of
    scope here). Rarity is a catalogue fact shared by every copy of this
    card, so it's always editable; Cost adds a "manual" CardPrice row,
    which PREFERRED_SOURCES now ranks above every live-market source (see
    personal_collection.py). Condition is a specific copy's own attribute,
    so it only makes sense -- and is only offered -- when there's exactly
    one held item for this (card, variant), the same ambiguity guard
    /adjust already uses for quantity.
    """
    if not held:
        return ""
    back = f"/inventory/set/{escape(set_id)}?sort={escape(sort)}&owned_only={'1' if show_owned_only else '0'}"
    condition_field = (
        f"<label>Condition<input name='condition' value='{escape(held[0].condition or '')}'></label>"
        if len(held) == 1 else
        "<p class='muted'>Condition isn't editable here -- this card is held in "
        f"{len(held)} different conditions, and guessing which one you mean risks editing the wrong copy.</p>"
    )
    rarity_value = escape(card.rarity or "")
    cost_value = str(cost) if cost is not None else ""
    return (
        "<details class='details-edit'><summary>Edit details</summary>"
        "<form method='post' action='/inventory/edit-details'>"
        f"<input type='hidden' name='card_id' value='{escape(card.id)}'>"
        f"<input type='hidden' name='variant' value='{escape(variant.value)}'>"
        f"<input type='hidden' name='back' value='{escape(back)}'>"
        f"<label>Rarity<input name='rarity' value='{rarity_value}' placeholder='Unknown'></label>"
        f"<label>Cost<input name='cost' type='number' step='0.01' min='0' value='{cost_value}' "
        "placeholder='no price on file'></label>"
        + condition_field
        + "<button type='submit'>Save</button>"
        + "</form></details>"
    )


# --- set grid (landing) ----------------------------------------------------

@router.get("", response_class=HTMLResponse)
def inventory_sets(
    request: Request,
    sort: str = Query("released"),
    group: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Bare /inventory (no `group`) is a category-picker: one big tile per
    GAME_GROUPS entry (One Piece / Dragon Ball / Pokémon -- each may span
    several CardSet.game values, e.g. Dragon Ball covers all four Dragon
    Ball games at once) plus Collectibles and Misc, matching the same
    tile you tap into a set from. `?group=<key>` is the page that used to
    be the whole thing: every set you own in that category, as tiles,
    loading no card images -- set artwork is a handful of small cached
    logos rather than one scan per card.

    Collectibles (Sonny Angels etc -- a different shape of "owned stock",
    no card/set at all) and Misc (a TCG card with no catalogue match to
    build a set tile from -- see inventory_uncatalogued) aren't GAME_GROUPS
    at all; their landing tile links straight to their own page.
    """
    if sort not in SET_SORTS:
        sort = "released"
    if group not in GAME_GROUPS:
        group = ""

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
        # `owned` only ever covers card_id-linked holdings (real CardSet
        # tiles); a holding with no catalogue match at all -- see
        # inventory_uncatalogued -- would otherwise report "nothing
        # scanned yet" even with real, correctly game-tagged stock sitting
        # in Misc.
        any_uncatalogued = session.scalar(select(InventoryItem.id).where(InventoryItem.card_id.is_(None)).limit(1))
        if not any_uncatalogued:
            body = (
                brand_header("Inventory")
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
    ebay_prices = _latest_ebay_prices(session, {i.card_id for i in items})
    set_of = {c.id: c.set_id for c in session.scalars(select(CatalogCard).where(CatalogCard.id.in_({i.card_id for i in items})))}

    value_by_set: dict[str, float] = {}
    for item in items:
        # same fallback as the per-card grid, so set totals and card tiles
        # can't disagree about what a holding is worth
        line = _aud(prices.get((item.card_id, item.variant)), item.quantity)
        if line is None:
            line = _aud(ebay_prices.get(item.card_id), item.quantity)
        if line:
            value_by_set[set_of[item.card_id]] = value_by_set.get(set_of[item.card_id], 0.0) + line

    if not group:
        return _category_landing(session, owned, sets, value_by_set)

    games = GAME_GROUPS[group]["games"]
    owned = [row for row in owned if sets.get(row.set_id) and sets[row.set_id].game in games]

    # Holdings in this group with no catalogue match at all -- no CardSet
    # to build a tile from, so they never show up in `owned` above. Shown
    # as one more tile here rather than only in the cross-game Misc bucket
    # on the category landing page -- otherwise a card correctly filed
    # under this game's TcgCard.game still reads as misplaced, sitting
    # among every *other* game's uncatalogued cards instead of its own.
    uncatalogued_count, uncatalogued_qty = session.execute(
        select(func.count(InventoryItem.id), func.sum(InventoryItem.quantity))
        .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .where(InventoryItem.card_id.is_(None), TcgCard.game.in_(games))
    ).one()

    if not owned and not uncatalogued_count:
        body = (
            brand_header("Inventory")
            + "<p><a class='btn ghost' href='/inventory'>&larr; Categories</a></p>"
            + f"<div class='panel'><p>Nothing owned yet in {escape(GAME_GROUPS[group]['label'])}.</p></div>"
        )
        return HTMLResponse(page("EzBay — Inventory", body))
    set_ids = {row.set_id for row in owned}

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

    if uncatalogued_count:
        tiles.append(
            f"<a class='set-tile' href='/inventory/uncatalogued?group={group}'>"
            "<div class='set-art'><span class='set-fallback'>Uncatalogued</span></div>"
            f"<div class='set-name'>{escape(GAME_GROUPS[group]['label'])} — uncatalogued</div>"
            f"<div class='set-meta'>{uncatalogued_count} holding(s), {uncatalogued_qty or 0} total</div>"
            "<div class='set-meta muted'>No catalogue match yet</div>"
            "</a>"
        )

    rate_note = "" if fx.is_live() else (
        "<p class='subtitle'>Values use an approximate AUD rate — live FX unavailable.</p>"
    )
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
        brand_header(f"Inventory — {GAME_GROUPS[group]['label']}")
        + "<p><a class='btn ghost' href='/inventory'>&larr; Categories</a></p>"
        + rate_note
        + stat_grid
        + "<div class='toolbar'>"
        + "<input id='set-search' class='search-box' type='search' placeholder='Search sets…' autocomplete='off'>"
        + f"<a class='chip' href='/inventory/cards?group={escape(group)}'>All owned cards</a>"
        + _sort_control(SET_SORTS, sort)
        + "</div>"
        + f"<div class='set-grid' id='set-grid'>{''.join(tiles)}</div>"
        + _STYLE
        + _SEARCH_SCRIPT
    )
    return HTMLResponse(page("EzBay — Inventory", body))


def _category_landing(session: Session, owned, sets: dict, value_by_set: dict) -> HTMLResponse:
    """The tile Nic actually asked for: tap a category, land on its sets.
    One tile per GAME_GROUPS entry plus Collectibles and Misc, each
    showing the same kind of summary a set tile does (a count, a value)
    so this reads as one consistent tile system, not a menu bolted onto it.
    """
    all_distinct = sum(row.distinct_cards or 0 for row in owned)
    all_copies = sum(row.copies or 0 for row in owned)
    all_value = sum(value_by_set.values())
    tiles = [
        "<a class='set-tile all-cards-tile' href='/inventory/cards'>"
        "<div class='set-art'><span class='set-fallback'>All Cards</span></div>"
        "<div class='set-name'>All cards</div>"
        f"<div class='set-meta'>{all_distinct} card(s), {all_copies} total copies</div>"
        f"<div class='set-meta'>Total Value: <strong>{_money(all_value if all_value else None)}</strong></div>"
        "</a>"
    ]
    for key, info in GAME_GROUPS.items():
        rows = [r for r in owned if sets.get(r.set_id) and sets[r.set_id].game in info["games"]]
        set_count = len(rows)
        distinct = sum(r.distinct_cards or 0 for r in rows)
        value = sum(value_by_set.get(r.set_id, 0.0) for r in rows)
        logo = info.get("logo")
        art = (
            f"<img src='{escape(logo)}' alt='' loading='lazy' decoding='async'>"
            if logo else f"<span class='set-fallback'>{escape(info['label'])}</span>"
        )
        tiles.append(
            f"<a class='set-tile' href='/inventory?group={key}'>"
            f"<div class='set-art'>{art}</div>"
            f"<div class='set-name'>{escape(info['label'])}</div>"
            f"<div class='set-meta'>{set_count} set(s), {distinct} card(s)</div>"
            f"<div class='set-meta'>Total Value: <strong>{_money(value if value else None)}</strong></div>"
            "</a>"
        )

    collectible_count, collectible_qty = session.execute(
        select(func.count(func.distinct(InventoryItem.catalog_item_id)), func.sum(InventoryItem.quantity))
        .join(CollectibleProduct, CollectibleProduct.catalog_item_id == InventoryItem.catalog_item_id)
    ).one()
    tiles.append(
        "<a class='set-tile' href='/collectibles'>"
        "<div class='set-art'><span class='set-fallback'>Collectibles</span></div>"
        "<div class='set-name'>Collectibles</div>"
        f"<div class='set-meta'>{collectible_count or 0} item(s), {collectible_qty or 0} total</div>"
        "</a>"
    )

    sealed_count, sealed_qty = session.execute(
        select(func.count(func.distinct(InventoryItem.catalog_item_id)), func.sum(InventoryItem.quantity))
        .join(SealedProduct, SealedProduct.catalog_item_id == InventoryItem.catalog_item_id)
        .where(InventoryItem.quantity > 0)
    ).one()
    tiles.append(
        "<a class='set-tile' href='/sealed-products?owned=1'>"
        "<div class='set-art'><span class='set-fallback'>Sealed</span></div>"
        "<div class='set-name'>Sealed products</div>"
        f"<div class='set-meta'>{sealed_count or 0} product(s), {sealed_qty or 0} total</div>"
        "<div class='set-meta muted'>Includes Retailer Goals purchases</div>"
        "</a>"
    )

    misc_count, misc_qty = session.execute(
        select(func.count(InventoryItem.id), func.sum(InventoryItem.quantity))
        .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .where(InventoryItem.card_id.is_(None))
    ).one()
    tiles.append(
        "<a class='set-tile' href='/inventory/uncatalogued'>"
        "<div class='set-art'><span class='set-fallback'>Misc</span></div>"
        "<div class='set-name'>Misc</div>"
        f"<div class='set-meta'>{misc_count or 0} holding(s), {misc_qty or 0} total</div>"
        "<div class='set-meta muted'>No catalogue match yet</div>"
        "</a>"
    )

    body = (
        brand_header("Inventory")
        + f"<div class='set-grid'>{''.join(tiles)}</div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Inventory", body))


# --- every owned card across every set ------------------------------------

@router.get("/cards", response_class=HTMLResponse)
def inventory_all_cards(
    sort: str = Query("recent"),
    group: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """One searchable list of every catalogue-linked card in inventory.

    The category/set hierarchy remains the fast way to browse a collection,
    but it should not be the only way to answer "show me all my cards".
    This view deliberately includes owned holdings only; the all/unowned
    switch inside a set is for completion tracking against that set's full
    checklist.
    """
    if sort not in ALL_CARD_SORTS:
        sort = "recent"
    if group not in GAME_GROUPS:
        group = ""

    items = list(session.scalars(
        select(InventoryItem).where(
            InventoryItem.card_id.is_not(None),
            InventoryItem.quantity > 0,
        )
    ))
    card_ids = {item.card_id for item in items if item.card_id}
    cards = {
        card.id: card
        for card in session.scalars(select(CatalogCard).where(CatalogCard.id.in_(card_ids)))
    } if card_ids else {}
    set_ids = {card.set_id for card in cards.values()}
    sets = {
        card_set.id: card_set
        for card_set in session.scalars(select(CardSet).where(CardSet.id.in_(set_ids)))
    } if set_ids else {}

    if group:
        games = GAME_GROUPS[group]["games"]
        items = [
            item for item in items
            if item.card_id in cards
            and sets.get(cards[item.card_id].set_id)
            and sets[cards[item.card_id].set_id].game in games
        ]

    held_by: dict[tuple[str, CardVariant], list[InventoryItem]] = {}
    for item in items:
        if item.card_id in cards:
            held_by.setdefault((item.card_id, item.variant), []).append(item)

    visible_ids = {card_id for card_id, _ in held_by}
    prices = _latest_prices(session, visible_ids)
    ebay_prices = _latest_ebay_prices(session, visible_ids)
    rows = []
    for (card_id, printing), held in held_by.items():
        card = cards[card_id]
        card_set = sets.get(card.set_id)
        quantity = sum(item.quantity for item in held)
        variant_price = _aud(prices.get((card.id, printing)))
        ebay_price = _aud(ebay_prices.get(card.id))
        rows.append({
            "card": card,
            "variant": printing,
            "quantity": quantity,
            "held": held,
            "price": variant_price if variant_price is not None else ebay_price,
            "from_ebay": variant_price is None and ebay_price is not None,
            "ebay_price": ebay_price,
            "set_name": card_set.name if card_set else card.set_id,
            "card_set": card_set,
            "added_at": max((item.added_at for item in held if item.added_at), default=None),
        })
    _sort_card_rows(rows, sort)

    total_copies = sum(row["quantity"] for row in rows)
    total_value = sum((row["price"] or 0.0) * row["quantity"] for row in rows)
    distinct_cards = len({row["card"].id for row in rows})
    back = f"/inventory/cards?sort={sort}" + (f"&group={group}" if group else "")

    card_rows = []
    for row in rows:
        card, printing, quantity, held = row["card"], row["variant"], row["quantity"], row["held"]
        own_scan = next((item for item in held if item.scan_image_path), None)
        if own_scan is not None:
            face = (
                f"<img src='/inventory/thumb/{escape(own_scan.id)}' alt='' loading='lazy' decoding='async'>"
                "<span class='face-tag'>Your scan</span>"
            )
            face_block = (
                f"<a class='card-face' href='/inventory/image/{escape(own_scan.id)}'>{face}"
                f"{f'<span class=qty-badge>x{quantity}</span>' if quantity > 1 else ''}</a>"
            )
        elif card.local_image_path:
            face_block = (
                "<div class='card-face'>"
                f"<img src='/inventory/card-thumb/{escape(card.id)}' alt='' loading='lazy' decoding='async'>"
                f"{f'<span class=qty-badge>x{quantity}</span>' if quantity > 1 else ''}</div>"
            )
        else:
            face_block = "<div class='card-face'><span class='no-art'>no image</span></div>"

        printed = row["card_set"].printed_total if row["card_set"] else None
        number = f"#{card.number}/{printed}" if printed else f"#{card.number}"
        haystack = (
            f"{card.name} {card.number} {card.rarity or ''} {printing.value} {row['set_name']}"
        ).lower()
        price_block = (
            f"<span class='card-price'>{_money(row['price'])}</span>"
            + ("<span class='price-src'>eBay</span>" if row["from_ebay"] else "")
            + (
                f"<span class='card-ebay'>eBay low {_money(row['ebay_price'])}</span>"
                if row["ebay_price"] is not None and not row["from_ebay"] else ""
            )
        )
        card_rows.append(
            f"<div class='card-row' data-find='{escape(haystack)}'>"
            f"{face_block}"
            "<div class='card-info'>"
            f"<div class='card-name'>{escape(card.name)}</div>"
            f"<div class='card-set-link'><a href='/inventory/set/{escape(card.set_id)}'>{escape(row['set_name'])}</a></div>"
            f"<div class='card-sub'>{escape(number)} &bull; {escape(card.rarity or 'Unknown')} "
            f"&bull; {escape(printing.value.replace('_', ' ').title())}</div>"
            f"<div class='card-price-row'>{price_block}<span class='card-qty'>Qty: {quantity}</span></div>"
            "</div>"
            f"<div class='card-edit-wrap'>{_edit_controls(card.set_id, card, printing, quantity, sort, True, back=back)}</div>"
            "</div>"
        )

    filter_links = [
        f"<a class='chip{' active' if not group else ''}' href='/inventory/cards?sort={escape(sort)}'>All games</a>"
    ]
    filter_links.extend(
        f"<a class='chip{' active' if group == key else ''}' "
        f"href='/inventory/cards?sort={escape(sort)}&group={escape(key)}'>{escape(info['label'])}</a>"
        for key, info in GAME_GROUPS.items()
    )

    body = (
        brand_header("Inventory — All cards")
        + "<p class='subtitle'><a href='/inventory'>&larr; Categories</a> &bull; "
          "All catalogue-linked cards you own, across every set. "
          "<a href='/inventory/uncatalogued'>Uncatalogued cards are in Misc.</a></p>"
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Distinct cards</div><div class='value'>{distinct_cards}</div></div>"
        + f"<div class='stat-card'><div class='label'>Card printings</div><div class='value'>{len(rows)}</div></div>"
        + f"<div class='stat-card'><div class='label'>Total copies</div><div class='value'>{total_copies}</div></div>"
        + f"<div class='stat-card'><div class='label'>Total value</div><div class='value'>{_money(total_value)}"
          "<div class='value-sub'>AUD</div></div></div>"
        + "</div>"
        + f"<div class='filter-bar'>{''.join(filter_links)}</div>"
        + "<div class='toolbar'>"
        + "<input id='card-search' class='search-box' type='search' "
          "placeholder='Find any card, set or number…' autocomplete='off'>"
        + _sort_control(ALL_CARD_SORTS, sort)
        + "</div>"
        + "<div class='found' id='found'></div>"
        + (f"<div class='card-list' id='card-grid'>{''.join(card_rows)}</div>" if card_rows
           else "<div class='panel'><p>No owned cards match this view.</p></div>")
        + _STYLE
        + _CARD_SEARCH_SCRIPT
    )
    return HTMLResponse(page("EzBay — All cards", body))


# --- holdings with no catalogue match -------------------------------------

@router.get("/uncatalogued", response_class=HTMLResponse)
def inventory_uncatalogued(
    group: str = Query(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """"Misc" -- every TCG-card holding with no CatalogCard match at all
    (no reference photo, no set to build a tile from): a personal-collection
    import with no clean catalogue hit, a hand-catalogued Super Dragon Ball
    Heroes card cardotaku.com doesn't stock, .... Bare (no `group`) shows
    every game at once, matching the Misc tab on /inventory.

    Read-only: there's no catalogue entry to adjust a quantity against the
    way /inventory/adjust does, so these are listed as-is (edit them from
    /personal-collection, which works from CatalogItem/TcgCard directly,
    or the CLI).
    """
    stmt = (
        select(InventoryItem, TcgCard, CatalogItem)
        .join(TcgCard, TcgCard.catalog_item_id == InventoryItem.catalog_item_id)
        .join(CatalogItem, CatalogItem.id == InventoryItem.catalog_item_id)
        .where(InventoryItem.card_id.is_(None))
        .order_by(TcgCard.game, CatalogItem.name)
    )
    if group in GAME_GROUPS:
        stmt = stmt.where(TcgCard.game.in_(GAME_GROUPS[group]["games"]))
    rows = session.execute(stmt).all()

    table_rows = "".join(
        "<tr>"
        f"<td class='muted opt'>{escape(GAME_LABELS.get(tcg.game, tcg.game))}</td>"
        f"<td>{escape(item.name)}</td>"
        f"<td class='muted'>{escape(tcg.set_code or tcg.set_id or '—')}</td>"
        f"<td class='muted'>{escape(tcg.number or '—')}</td>"
        f"<td class='muted opt'>{escape(tcg.rarity or '—')}</td>"
        f"<td>{inv.quantity}</td>"
        f"<td class='muted opt'>{escape(inv.condition or '—')}</td>"
        f"<td class='muted opt'>{escape(inv.status.value if inv.status else '—')}</td>"
        + (
            "<td><span class='pill warn'>No supported price source</span>"
            + (f"<small class='set-hint'>{escape(item.pricing_policy_reason)}</small>"
               if item.pricing_policy_reason else "")
            + "</td>"
            if item.pricing_policy == "manual_only" else
            "<td class='muted'>Awaiting automated price</td>"
        )
        + "</tr>"
        for inv, tcg, item in rows
    ) or "<tr><td colspan=9>Nothing here.</td></tr>"

    title = f"Misc — {GAME_GROUPS[group]['label']}" if group in GAME_GROUPS else "Misc"
    body = (
        brand_header(f"Inventory — {escape(title)}")
        + f"<p class='subtitle'>{len(rows)} holding(s) with no catalogue entry to match them against yet — "
          "no reference photo, no set, no auto price. Edit these from "
          "<a href='/personal-collection'>Personal Collection</a> or the CLI.</p>"
        + "<div class='panel'><div class='table-wrap'><table><thead><tr>"
        + "<th class='opt'>Game</th><th>Name</th><th>Set/code</th><th>Number</th><th class='opt'>Rarity</th>"
          "<th>Qty</th><th class='opt'>Condition</th><th class='opt'>Status</th><th>Pricing</th>"
        + f"</tr></thead><tbody>{table_rows}</tbody></table></div></div>"
        + "<p><a class='btn ghost' href='/inventory'>&larr; Back to Inventory</a></p>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Inventory", body))


# --- one set's cards -------------------------------------------------------

@router.get("/set/{set_id}", response_class=HTMLResponse)
def inventory_set(
    set_id: str,
    request: Request,
    variant: str = Query(""),
    sort: str = Query("number"),
    owned_only: str = Query("1"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    card_set = session.get(CardSet, set_id)
    if card_set is None:
        raise HTTPException(status_code=404, detail=f"unknown set: {set_id!r}")
    if sort not in CARD_SORTS:
        sort = "number"
    # Defaults to owned-only: seeing your own 15 cards against an unfiltered
    # 200-card set is the common case, and "Show all cards" is one tap away.
    # "0" must mean off, not just an empty string -- Query("1") makes an
    # untouched param the string "1", so a bare `if owned_only:` would also
    # treat an explicit ?owned_only=0 as on.
    show_owned_only = owned_only not in ("", "0")

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
    ebay_prices = _latest_ebay_prices(session, {c.id for c in catalog_cards})
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
            if show_owned_only and not quantity:
                continue
            # The variant-accurate price wins when there is one; the eBay
            # figure fills the gap when there isn't, rather than showing
            # nothing for a card we do have a market signal for. Which one
            # is being shown is always labelled, so a variant-agnostic
            # number is never mistaken for a variant-specific one.
            variant_price = _aud(prices.get((card.id, variant)))
            ebay_price = _aud(ebay_prices.get(card.id))
            rows.append({
                "card": card, "variant": variant, "quantity": quantity, "held": held,
                "price": variant_price if variant_price is not None else ebay_price,
                "from_ebay": variant_price is None and ebay_price is not None,
                "ebay_price": ebay_price,
                "added_at": max((item.added_at for item in held if item.added_at), default=None),
            })

    _sort_card_rows(rows, sort)

    distinct_owned = len({cid for (cid, _) in held_by})
    total_copies = sum(i.quantity for i in items)
    total_value = sum((r["price"] or 0.0) * r["quantity"] for r in rows)

    card_rows = []
    for row in rows:
        card, variant, quantity, held = row["card"], row["variant"], row["quantity"], row["held"]
        # Your own scan is the face when you own the card, reference art
        # only when you don't. The set page's job is "what do I have and
        # what condition is it in", and a publisher's studio render answers
        # neither -- it looks identical for a mint copy and a battered one.
        own_scan = held[0] if (held and held[0].scan_image_path) else None
        if own_scan is not None:
            face = (
                f"<img src='/inventory/thumb/{escape(own_scan.id)}' alt='' loading='lazy' decoding='async'>"
                "<span class='face-tag'>Your scan</span>"
            )
        elif card.local_image_path:
            face = f"<img src='/inventory/card-thumb/{escape(card.id)}' alt='' loading='lazy' decoding='async'>"
        else:
            face = "<span class='no-art'>no image</span>"
        # An owned card's face links to your own scan; an unowned one has
        # nothing to show, so it stays inert rather than a dead link. The
        # row itself is never a link -- it holds the edit controls, and
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

        price_block = (
            f"<span class='card-price'>{_money(row['price'])}</span>"
            + ("<span class='price-src'>eBay</span>" if row["from_ebay"] else "")
            + (
                f"<span class='card-ebay'>eBay low {_money(row['ebay_price'])}</span>"
                if row["ebay_price"] is not None and not row["from_ebay"] else ""
            )
        )

        card_rows.append(
            f"<div class='card-row{'' if quantity else ' unowned'}' data-find='{escape(haystack)}'>"
            f"{face_block}"
            "<div class='card-info'>"
            f"<div class='card-name'>{escape(card.name)}</div>"
            f"<div class='card-sub'>#{escape(card.number)}/{printed} &bull; {escape(card.rarity or 'Unknown')} "
            f"&bull; {escape(variant.value.replace('_', ' ').title())}</div>"
            f"<div class='card-price-row'>{price_block}"
            f"<span class='card-qty{'' if quantity else ' zero'}'>Qty: {quantity}</span></div>"
            "</div>"
            f"<div class='card-edit-wrap'>{_edit_controls(set_id, card, variant, quantity, sort, show_owned_only)}"
            f"{_details_edit_html(set_id, card, variant, held, sort, show_owned_only, row['price'])}</div>"
            "</div>"
        )

    missing = printed - distinct_owned
    header = (
        "<div class='set-header'>"
        + (f"<img class='set-header-logo' src='{escape(card_set.logo_url)}' alt=''>" if card_set.logo_url else "")
        + f"<div class='set-header-name'>{escape(card_set.name)}</div>"
        + "</div>"
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Owned</div><div class='value'>{distinct_owned}/{printed}</div></div>"
        + f"<div class='stat-card'><div class='label'>Missing</div><div class='value'>{missing}</div></div>"
        + f"<div class='stat-card'><div class='label'>Total copies</div><div class='value'>{total_copies}</div></div>"
        + f"<div class='stat-card'><div class='label'>Total value</div><div class='value'>{_money(total_value)}"
          f"<div class='value-sub'>AUD</div></div></div>"
        + "</div>"
    )

    variant_query = f"&variant={escape(variant)}" if variant else ""
    owned_href = f"/inventory/set/{escape(set_id)}?sort={escape(sort)}&owned_only=1{variant_query}"
    all_href = f"/inventory/set/{escape(set_id)}?sort={escape(sort)}&owned_only=0{variant_query}"
    view_switch = (
        "<div class='view-switch' aria-label='Cards shown'>"
        f"<a class='chip{' active' if show_owned_only else ''}' href='{owned_href}'>Owned only</a>"
        f"<a class='chip{' active' if not show_owned_only else ''}' href='{all_href}'>Show all cards</a>"
        "</div>"
    )

    body = (
        brand_header("Inventory")
        + f"<p class='subtitle'><a href='/inventory'>&larr; All sets</a></p>"
        + header
        + "<div class='toolbar'>"
        + "<input id='card-search' class='search-box' type='search' "
          "placeholder='Find a card by name or number…' autocomplete='off'>"
        + view_switch
        + _sort_control(CARD_SORTS, sort)
        + "</div>"
        + "<div class='found' id='found'></div>"
        + (f"<div class='card-list' id='card-grid'>{''.join(card_rows)}</div>" if card_rows
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
            # catalog_item_id, not just card_id: this page's own totals are
            # computed from it, so a row added here without it would be
            # counted as a holding and valued at nothing. Same helper the
            # scan-ingest commit path uses, for the same reason.
            item = InventoryItem(card_id=card.id, variant=printing, condition=DEFAULT_CONDITION, quantity=1,
                                 catalog_item_id=_catalog_item_id_for(session, card))
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


@router.post("/edit-details")
def inventory_edit_details(
    card_id: str = Form(...),
    variant: str = Form(...),
    rarity: str = Form(""),
    cost: str = Form(""),
    condition: str = Form(""),
    back: str = Form("/inventory"),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Rarity/Cost/Condition, editable -- see _details_edit_html's own
    docstring for why this stays deliberately short of "which card a
    holding is" (this module's one hard boundary, stated at the top of the
    file). Rarity writes straight to the catalogue card; Cost appends a
    "manual" CardPrice row (PREFERRED_SOURCES ranks it above every live
    source, see personal_collection.py); Condition only ever touches a
    single held item, refusing exactly the same multi-condition ambiguity
    /adjust already refuses rather than guessing which physical copy is
    meant.
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

    changed: dict[str, object] = {}

    rarity = rarity.strip()
    if rarity and card.rarity != rarity:
        changed["rarity"] = {"from": card.rarity, "to": rarity}
        card.rarity = rarity

    cost = cost.strip()
    if cost:
        try:
            new_price = Decimal(cost).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise HTTPException(status_code=400, detail=f"couldn't read a price from {cost!r}")
        if new_price <= 0:
            raise HTTPException(status_code=400, detail="price must be greater than zero")
        latest = session.scalars(
            select(CardPrice).where(CardPrice.card_id == card_id).order_by(CardPrice.fetched_at.desc())
        ).first()
        already_this_value = (
            latest is not None and latest.source == "manual"
            and Decimal(str(latest.price)).quantize(Decimal("0.01")) == new_price
        )
        if not already_this_value:
            session.add(CardPrice(card_id=card_id, source="manual", price=new_price, currency="AUD"))
            changed["cost"] = str(new_price)

    condition = condition.strip()
    if condition:
        if len(held) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"{card.name} is held in {len(held)} conditions -- can't tell which one you mean here",
            )
        if held and held[0].condition != condition:
            changed["condition"] = {"from": held[0].condition, "to": condition}
            held[0].condition = condition

    session.commit()
    record_event(
        session, actor_type="user", actor_id=user, action="inventory.edit_details",
        resource_type="catalog_card", resource_id=card.id, outcome="success" if changed else "no_change",
        correlation_id=str(uuid.uuid4()), details={"card_id": card.id, "variant": printing.value, "changed": changed},
    )
    destination = back if back.startswith("/inventory") else "/inventory"
    return RedirectResponse(destination, status_code=303)


# Legitimate callers of set_location's `back` redirect -- both pack
# assistants (see card_locate.py, shared by shop_orders.py and
# ebay_sales.py) plus this module's own inventory pages.
_SET_LOCATION_SAFE_PREFIXES = ("/inventory", "/shop-orders", "/ebay-sales")


@router.post("/set-location")
def inventory_set_location(
    inventory_item_id: str = Form(...), location: str = Form(""), back: str = Form("/inventory"),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
):
    """Where a held card actually lives -- the one write both pack
    assistants need. Get-or-create by name so 'Binder A' typed on two
    different orders is one StorageLocation row, not two. Shared across
    callers (rather than living in each pack assistant) so this is the
    single place that writes InventoryItem.storage_location_id."""
    item = session.get(InventoryItem, inventory_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inventory item not found")

    name = location.strip()
    if name:
        loc = session.scalar(select(StorageLocation).where(StorageLocation.name == name))
        if loc is None:
            loc = StorageLocation(name=name)
            session.add(loc)
            session.flush()
        item.storage_location_id = loc.id
    else:
        item.storage_location_id = None
    session.commit()
    record_event(session, actor_type="user", actor_id=user, action="inventory.set_location",
                resource_type="inventory_item", resource_id=inventory_item_id, outcome="success",
                correlation_id=str(uuid.uuid4()), details={"location": name or None})

    destination = back if back.startswith(_SET_LOCATION_SAFE_PREFIXES) else "/inventory"
    return RedirectResponse(destination, status_code=303)


# --- images ----------------------------------------------------------------

def _serve_thumb(source: Path, cache_name: str) -> FileResponse:
    """Reference art and own-scan tiles, at THUMB_WIDTH. Same cache
    directory as before, so nothing already generated is invalidated."""
    return serve_thumb(source, cache_name, THUMB_WIDTH)


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
    return serve_image(item.scan_image_path if item else None)


_STYLE = """<style>
.muted{color:var(--text-dim);font-size:13px}
@media (max-width:760px){.opt{display:none}}
.toolbar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:0 0 20px}
.search-box{flex:1 1 240px;max-width:420px;background:var(--surface-sunken);border:1px solid var(--panel-border);
  border-radius:10px;padding:11px 14px;color:var(--text);font-size:14px}
.search-box:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(var(--accent-rgb),.15)}
.sort-control{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text-dim);
  text-transform:uppercase;letter-spacing:.06em}
.sort-control select{background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:9px;
  padding:9px 11px;color:var(--text);font-size:13px;text-transform:none;letter-spacing:normal}
.chip{display:inline-flex;align-items:center;padding:9px 14px;border-radius:999px;font-size:13px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:var(--surface-sunken)}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.chip.active{border-color:var(--accent);background:var(--accent);color:#fff}
.view-switch{display:inline-flex;gap:4px;padding:3px;border:1px solid var(--panel-border);
  border-radius:999px;background:var(--surface-sunken)}
.view-switch .chip{border:none;background:transparent;padding:7px 11px}
.view-switch .chip.active{background:var(--accent);color:#fff}
.filter-bar{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}
.unit{font-size:12px;color:var(--text-dim)}

.set-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:18px;align-items:start}
a.set-tile{display:flex;flex-direction:column;gap:3px;text-decoration:none;color:var(--text);min-width:0}
a.set-tile>*{min-width:0;max-width:100%}
.set-art{position:relative;display:grid;place-items:center;height:104px;border-radius:12px;
  background:#f4f5f7;border:1px solid var(--panel-border);overflow:hidden;
  transition:transform .15s ease,box-shadow .15s ease}
.set-art img{max-width:82%;max-height:64px;object-fit:contain}
.set-fallback{font-weight:700;font-size:15px;color:#12161c;padding:0 8px;text-align:center}
a.set-tile:hover .set-art{transform:translateY(-2px);box-shadow:0 8px 20px rgba(16,24,40,.16)}
.set-date{position:absolute;top:7px;right:7px;background:rgba(255,255,255,.9);color:#12161c;
  font-size:10.5px;font-weight:600;padding:2px 7px;border-radius:999px}
.set-pct{position:absolute;bottom:0;left:0;background:var(--accent);color:#fff;
  font-size:10.5px;font-weight:700;padding:1px 6px;border-top-right-radius:6px}
.set-name{font-weight:600;font-size:14px;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.set-meta{font-size:12.5px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.set-meta strong{color:var(--text)}

.set-header{display:flex;gap:16px;align-items:center;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:14px;padding:16px 18px;margin-bottom:14px}
.set-header-logo{width:120px;max-height:62px;object-fit:contain;background:#f4f5f7;
  border-radius:9px;padding:7px;flex:none}
.set-header-name{font-size:17px;font-weight:650}

.card-list{display:flex;flex-direction:column;gap:9px}
.card-row{display:flex;align-items:center;gap:13px;text-decoration:none;color:var(--text);min-width:0;
  background:var(--panel);border:1px solid var(--panel-border);border-radius:13px;padding:10px 12px}
.card-row>*{min-width:0}
.card-row.unowned{opacity:.55}
.card-face{position:relative;flex:0 0 62px;width:62px;aspect-ratio:245/342;border-radius:8px;overflow:hidden;
  background:var(--surface-sunken);display:grid;place-items:center}
.card-face img{width:100%;height:100%;object-fit:contain;display:block}
.no-art{color:var(--text-dim);font-size:10px;text-align:center;padding:0 4px}
.face-tag{position:absolute;left:3px;bottom:3px;background:rgba(20,24,29,.7);color:#fff;
  font-size:8.5px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;
  padding:1px 5px;border-radius:999px;pointer-events:none}
.qty-badge{position:absolute;top:3px;right:3px;background:var(--accent);color:#fff;
  font-size:10px;font-weight:700;padding:1px 6px;border-radius:999px}

.card-info{flex:1 1 auto;min-width:0}
.card-name{font-weight:650;font-size:14.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-set-link{font-size:11.5px;margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-set-link a{color:var(--accent);text-decoration:none}
.card-set-link a:hover{text-decoration:underline}
.card-sub{font-size:12px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px}
.card-price-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:5px}
.card-price{font-size:14.5px;font-weight:650}
.price-src{font-size:9.5px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
  color:var(--accent);background:var(--neutral-bg);padding:1px 6px;border-radius:999px;vertical-align:middle}
.card-ebay{font-size:11px;color:var(--text-dim)}
.card-qty{font-size:12px;color:var(--text-dim)}
.card-qty.zero{opacity:.75}

.card-edit-wrap{flex:0 0 auto}
.card-edit{display:flex;align-items:center;gap:5px;max-width:none;min-width:0}
.card-edit button{flex:0 0 auto;min-width:30px;padding:6px 8px;border-radius:8px;font-size:13px;
  font-weight:700;cursor:pointer;border:1px solid var(--panel-border);background:var(--surface-sunken);color:var(--text)}
.card-edit button:hover{border-color:var(--accent);color:var(--accent)}
.card-edit button.add{background:linear-gradient(120deg,var(--accent),var(--accent-2));
  color:#fff;border:none;padding:8px 14px}
.card-edit button.ghost{font-size:11px;font-weight:600;color:var(--text-dim)}
.card-edit button.ghost:hover{border-color:var(--danger);color:var(--danger)}
.card-edit .qty{min-width:20px;text-align:center;font-size:14px;font-weight:650;font-variant-numeric:tabular-nums}
.found{font-size:13px;color:var(--text-dim);margin:-8px 0 14px;min-height:1em}

@media (max-width:560px){
  .toolbar{align-items:stretch}
  .search-box{max-width:none;flex-basis:100%}
  .sort-control{width:100%;justify-content:space-between}
  .sort-control select{flex:1 1 auto;min-width:0}
  .set-grid{grid-template-columns:repeat(2,1fr);gap:14px}
  .set-art{height:84px}
  .set-header-logo{width:92px}
  .card-row{padding:8px 9px;gap:10px}
  .card-face{flex-basis:52px;width:52px}
  .card-edit button.ghost{display:none}
}
</style>"""

_CARD_SEARCH_SCRIPT = """<script>
(function(){
  var box=document.getElementById('card-search'), grid=document.getElementById('card-grid'),
      found=document.getElementById('found');
  if(!box||!grid)return;
  var tiles=[].slice.call(grid.querySelectorAll('.card-row'));
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
