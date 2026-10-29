"""Shared "where is this physical card" lookup and rendering -- used by
both shop_orders.py's pack assistant (website checkout sales) and
ebay_sales.py's pack assistant (eBay sales), so the same photo/set/
rarity/location lookup and the same location-editing form exist in
exactly one place rather than two copies that drift.

Both pages ultimately just need: given an InventoryItem id, show its
photo, its set/number/rarity, and wherever it's currently marked as
living -- with a one-field form to set or correct that.
"""

from html import escape

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CardSet, CatalogItem, InventoryItem, InventoryPlacement, StorageLocation, TcgCard

# Where a card's real photo lives, from automation_control/inventory_review.py
# -- both routes are already dashboard-auth-gated the same way this page is,
# so no new image route is needed here.
THUMB_ROUTE = "/inventory/thumb/{item_id}"
CARD_THUMB_ROUTE = "/inventory/card-thumb/{card_id}"


def line_detail(session: Session, inventory_item_id: str | None) -> dict | None:
    """Everything needed to physically find one held card, or None if
    there's no real inventory row behind the id at all."""
    if not inventory_item_id:
        return None
    inv = session.get(InventoryItem, inventory_item_id)
    if inv is None:
        return None

    name = set_name = number = rarity = ""
    if inv.catalog_item_id:
        item = session.get(CatalogItem, inv.catalog_item_id)
        name = item.name if item else ""
        tcg = session.get(TcgCard, inv.catalog_item_id)
        if tcg is not None:
            rarity = tcg.rarity or ""
            number = tcg.number or ""
            if tcg.set_id:
                card_set = session.get(CardSet, tcg.set_id)
                set_name = card_set.name if card_set else ""

    placement_locations = list(session.scalars(
        select(StorageLocation.name)
        .join(InventoryPlacement, InventoryPlacement.storage_location_id == StorageLocation.id)
        .where(
            InventoryPlacement.inventory_item_id == inv.id,
            InventoryPlacement.confirmed_at.is_not(None),
        )
        .order_by(StorageLocation.name)
    ))
    if not placement_locations and inv.storage_location:
        placement_locations = [inv.storage_location.name]

    return {
        "inventory_item_id": inv.id,
        "card_id": inv.catalog_item_id,
        "name": name,
        "variant": inv.variant.value.replace("_", " ").title(),
        "condition": inv.condition,
        "set_name": set_name,
        "number": number,
        "rarity": rarity,
        "location_name": placement_locations[0] if placement_locations else None,
        "location_names": placement_locations,
        "has_scan": bool(inv.scan_image_path),
    }


def thumb_src(detail: dict) -> str:
    if detail["has_scan"]:
        return THUMB_ROUTE.format(item_id=escape(detail["inventory_item_id"]))
    if detail["card_id"]:
        return CARD_THUMB_ROUTE.format(card_id=escape(detail["card_id"]))
    return ""


def location_pill_html(detail: dict) -> str:
    names = detail.get("location_names") or ([detail["location_name"]] if detail["location_name"] else [])
    if names:
        return " ".join(f"<span class='loc-pill'>📍 {escape(name)}</span>" for name in names)
    return "<span class='loc-pill loc-missing'>Not located yet</span>"


def pack_location_html(detail: dict) -> str:
    """Make the pick location impossible to miss while packing.

    The compact location pill is still useful on order summaries, but on the
    packing assistant the physical slot is the operator's primary instruction.
    Give it a labelled, full-width row instead of leaving it as small metadata.
    """
    return (
        "<div class='pack-location'>"
        "<span class='pack-location-label'>Pick location</span>"
        f"<div>{location_pill_html(detail)}</div>"
        "</div>"
    )


def location_form_html(detail: dict, back_url: str) -> str:
    value = escape(detail["location_name"] or "")
    return (
        "<form method='post' action='/inventory/set-location' class='loc-form'>"
        f"<input type='hidden' name='inventory_item_id' value='{escape(detail['inventory_item_id'])}'>"
        f"<input type='hidden' name='back' value='{escape(back_url)}'>"
        f"<input name='location' placeholder='Where is it? (e.g. Binder A)' value='{value}'>"
        "<button class='btn small'>Save</button>"
        "</form>"
    )


def pack_card_html(session: Session, inventory_item_id: str | None, *, title: str, qty: int,
                    price_html: str, back_url: str, empty_note: str) -> str:
    """One photo+details+location card, the shared unit both pack
    assistants are built from. `title` is a fallback only, used when
    there's no resolved card to name itself (or no physical holding at
    all) -- shop_orders.py's own ShopOrderItem.title is already
    card-specific, but ebay_sales.py's line title is the whole listing
    group's name ("151 - Complete Your Master Set") repeated across every
    card in the order, so the actual card's own name wins whenever one
    resolves. `empty_note` covers a line with no single physical holding
    to locate (a bulk lot line, a discount line, or an eBay sale whose
    sku never resolved to a ShopListing)."""
    detail = line_detail(session, inventory_item_id)
    if detail is None:
        return (
            "<div class='panel pack-card'>"
            f"<div class='pack-card-title'>{escape(title)} × {qty} &mdash; {price_html}</div>"
            f"<p class='muted'>{escape(empty_note)}</p></div>"
        )

    src = thumb_src(detail)
    photo = f"<img class='pack-photo' src='{src}' alt=''>" if src else "<div class='pack-photo noart'>No photo</div>"
    meta_bits = [b for b in (detail["set_name"], f"#{detail['number']}" if detail["number"] else "",
                              detail["rarity"], detail["variant"], detail["condition"]) if b]
    meta = " · ".join(escape(b) for b in meta_bits)
    display_title = detail["name"] or title

    return (
        "<div class='panel pack-card'>"
        f"{photo}"
        "<div class='pack-card-body'>"
        f"<div class='pack-card-title'>{escape(display_title)}</div>"
        f"<div class='muted'>{meta}</div>"
        f"<div class='pack-card-qty'>Qty {qty} &mdash; {price_html}</div>"
        f"{pack_location_html(detail)}"
        f"{location_form_html(detail, back_url)}"
        "</div></div>"
    )


LOCATE_STYLE = """<style>
.loc-pill{display:inline-block;font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px;
  background:var(--surface-sunken);color:var(--text-dim)}
.loc-pill.loc-missing{background:transparent;border:1px dashed var(--panel-border);color:var(--text-dim)}
.loc-form{display:flex;gap:6px;margin-top:4px}
.loc-form input{flex:1;padding:7px 9px;border-radius:6px;border:1px solid var(--panel-border);font-size:13px}
</style>"""

PACK_STYLE = """<style>
.pack-grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(240px, 1fr));gap:16px;margin:16px 0}
.pack-card{display:flex;flex-direction:column;gap:10px}
.pack-photo{width:100%;aspect-ratio:5/7;object-fit:cover;border-radius:8px;border:1px solid var(--panel-border);background:var(--surface-sunken)}
.pack-photo.noart{display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:13px}
.pack-card-title{font-weight:600}
.pack-card-qty{font-size:13.5px;color:var(--text-dim)}
.pack-location{margin-top:6px;padding:9px 10px;border:1px solid var(--panel-border);border-radius:9px;
  background:var(--surface-sunken)}
.pack-location-label{display:block;margin-bottom:5px;font-size:10.5px;font-weight:750;letter-spacing:.06em;
  text-transform:uppercase;color:var(--text-dim)}
.pack-location .loc-pill{font-size:15px;padding:4px 10px;background:var(--panel);color:var(--text);font-weight:750}
.pack-location .loc-pill.loc-missing{background:transparent;color:var(--text-dim)}
.pack-footer{display:flex;justify-content:flex-end;margin-top:4px}
</style>"""
