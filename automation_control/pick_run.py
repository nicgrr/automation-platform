"""Pick run -- every open eBay order on one list, in the order you walk the drawers.

The Pack Assistant (/ebay-sales) works one order at a time, which means
opening the same drawer once per order. This page flattens every open
order into a single pick list sorted by physical location: numbered tray
slots first (drawer code, then slot -- "OP-01-009" is drawer OP-01,
slot 9), then any other named location, then cards with no recorded
location (Pokemon singles live unslotted in the bulk drawers), then lines
that EZBay has no stock record for at all. Each line carries its order's
letter so picked cards can be dropped straight into that order's pile.

Labels: "Print all labels" sends every open order's address label to the
4x6 printer in one go, but skips any order whose label was already printed
(recorded as an audit event), so pressing it twice never prints a duplicate.
A single order can still be reprinted deliberately from its own button.

Nothing here changes stock or eBay. Ticks on the list are browser-only;
completing an order still happens on the Pack Assistant.
"""

import re
import uuid
from dataclasses import dataclass
from html import escape
from string import ascii_uppercase

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_locate import line_detail, thumb_src
from .database import get_session
from .ebay_sales import (
    UNLISTED_SKU_PREFIX,
    _bulk_lines,
    _line_items_by_sku,
    _ship_to,
    line_item_sku,
    open_orders,
)
from .labels import LABEL_PRINTED_ACTION, ebay_label_lines, label_buttons_html, print_label
from .models import AuditEvent, EbayImportedOrder, ShopListing
from .ui import brand_header, page

router = APIRouter(tags=["pick-run"])

# A numbered tray slot as Listing Studio's photo batches create them:
# <drawer code>-<slot>, e.g. OP-01-009 or DB-01-122.
_SLOT = re.compile(r"^(?P<drawer>[A-Z][A-Z0-9]*-\d+)-(?P<slot>\d+)$")

SLOTTED, NAMED, UNSLOTTED, UNLISTED, BULK = "slotted", "named", "unslotted", "unlisted", "bulk"
_SECTIONS = (
    (SLOTTED, "Drawer slots", "Walk the drawers in this order."),
    (NAMED, "Other locations", ""),
    (UNSLOTTED, "No slot recorded — check the bulk drawers",
     "These have no drawer slot in EZBay (Pokémon singles and older stock live in the bulk drawers). "
     "Sorted by set and number to make the search quicker."),
    (UNLISTED, "Pick by hand — listed directly on eBay",
     "EZBay has no stock record for these listings. Pick them from the eBay listing's description."),
    (BULK, "Bulk lots", "Picked through each order's bulk checklist."),
)


@dataclass
class PickLine:
    kind: str
    tag: str
    ebay_order_id: str
    title: str
    quantity: int
    detail: dict | None = None
    location: str | None = None
    drawer: str = ""
    slot: int = 0

    @property
    def sort_key(self) -> tuple:
        detail = self.detail or {}
        number = str(detail.get("number") or "")
        digits = int(re.match(r"\d*", number).group() or 0)
        return (self.drawer, self.slot, self.location or "", detail.get("set_name") or "", digits, number,
                self.title, self.tag)


def order_tags(orders: dict[str, list[EbayImportedOrder]]) -> dict[str, str]:
    """A, B, C... oldest order first, so the letters stay put as new orders arrive."""
    by_age = sorted(orders, key=lambda oid: min(r.observed_at for r in orders[oid]))
    return {oid: _tag(i) for i, oid in enumerate(by_age)}


def _tag(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = ascii_uppercase[rem] + letters
    return letters


def _card_line(session: Session, row: EbayImportedOrder, tag: str, title: str) -> PickLine:
    listing = session.get(ShopListing, row.shop_listing_id) if row.shop_listing_id else None
    detail = line_detail(session, listing.inventory_item_id if listing else None)
    line = PickLine(UNSLOTTED, tag, row.ebay_order_id, title, row.quantity, detail)
    names = (detail or {}).get("location_names") or []
    slotted = [m for m in (_SLOT.match(n) for n in names) if m]
    if slotted:
        line.kind, line.location = SLOTTED, ", ".join(names)
        line.drawer, line.slot = slotted[0]["drawer"], int(slotted[0]["slot"])
    elif names:
        line.kind, line.location = NAMED, ", ".join(names)
    return line


def pick_lines(session: Session, orders: dict[str, list[EbayImportedOrder]],
               tags: dict[str, str]) -> list[PickLine]:
    lines: list[PickLine] = []
    for oid, rows in orders.items():
        snapshot = rows[0].raw_snapshot or {}
        titles = _line_items_by_sku(snapshot)
        bulk = _bulk_lines(snapshot)
        bulk_skus = {line_item_sku(li) for _, li, _ in bulk}
        for _, line_item, quantity in bulk:
            lines.append(PickLine(BULK, tags[oid], oid, line_item.get("title") or "Bulk lot", quantity))
        for row in rows:
            if row.sku in bulk_skus:
                continue
            title = (titles.get(row.sku) or {}).get("title") or row.sku
            if row.sku.startswith(UNLISTED_SKU_PREFIX):
                lines.append(PickLine(UNLISTED, tags[oid], oid, title, row.quantity))
            else:
                lines.append(_card_line(session, row, tags[oid], title))
    return sorted(lines, key=lambda line: line.sort_key)


def printed_order_ids(session: Session, order_ids) -> set[str]:
    return set(session.scalars(
        select(AuditEvent.resource_id).where(
            AuditEvent.action == LABEL_PRINTED_ACTION,
            AuditEvent.resource_id.in_(list(order_ids)),
        )
    ))


def _line_html(line: PickLine) -> str:
    detail = line.detail or {}
    src = thumb_src(detail) if line.detail else ""
    photo = f"<img class='pr-photo' src='{src}' alt=''>" if src else "<div class='pr-photo noart'></div>"
    name = detail.get("name") or line.title
    meta = " · ".join(escape(b) for b in (detail.get("set_name"), f"#{detail['number']}" if detail.get("number") else "",
                                          detail.get("variant"), detail.get("condition")) if b)
    if line.kind == SLOTTED:
        where = (f"<div class='pr-where'><span class='pr-drawer'>{escape(line.drawer)}</span>"
                 f"<span class='pr-slot'>slot {line.slot:03d}</span></div>")
    elif line.location:
        where = f"<div class='pr-where'><span class='pr-drawer'>{escape(line.location)}</span></div>"
    else:
        where = ""
    if line.kind == BULK:
        action = "<a class='btn small' href='/ebay-sales'>Open bulk checklist on the Pack Assistant</a>"
    else:
        action = ""
    qty = f"<span class='pr-qty'>×{line.quantity}</span>" if line.quantity > 1 else ""
    return (
        "<li class='pr-line'><label>"
        "<input type='checkbox' class='pr-tick'>"
        f"<span class='pr-tag' title='Order {escape(line.ebay_order_id)}'>{escape(line.tag)}</span>"
        f"{photo}<span class='pr-body'><span class='pr-name'>{escape(name)}{qty}</span>"
        f"<span class='pr-meta'>{meta}</span>{where}{action}</span>"
        "</label></li>"
    )


def _orders_html(orders: dict[str, list[EbayImportedOrder]], tags: dict[str, str], printed: set[str]) -> str:
    rows = []
    for oid in sorted(orders, key=lambda o: tags[o]):
        group = orders[oid]
        ship_to = _ship_to(group[0].raw_snapshot or {})
        items = sum(r.quantity for r in group)
        badge = "<span class='pr-printed'>Label printed</span>" if oid in printed else ""
        rows.append(
            "<div class='pr-order'>"
            f"<span class='pr-tag big'>{escape(tags[oid])}</span>"
            f"<div class='pr-order-body'><strong>{escape(ship_to['name'])}</strong>"
            f"<span class='muted'> · {escape(ship_to['locality'])} · order {escape(oid)} · {items} item(s)</span>"
            f"{badge}<div class='pr-order-actions'>{label_buttons_html('ebay', oid, 'btn small')}</div></div>"
            "</div>"
        )
    return "".join(rows)


@router.get("/ebay-sales/pick-run", response_class=HTMLResponse)
def pick_run_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    orders = open_orders(session)
    tags = order_tags(orders)
    lines = pick_lines(session, orders, tags)
    printed = printed_order_ids(session, orders)
    unprinted = len(orders) - len(printed)

    sections = []
    for kind, heading, hint in _SECTIONS:
        chosen = [line for line in lines if line.kind == kind]
        if not chosen:
            continue
        sections.append(
            f"<section class='panel pr-section'><h3>{escape(heading)} <span class='muted'>({sum(l.quantity for l in chosen)})</span></h3>"
            + (f"<p class='muted'>{escape(hint)}</p>" if hint else "")
            + f"<ul class='pr-list'>{''.join(_line_html(line) for line in chosen)}</ul></section>"
        )

    print_all = (
        f"<button type='button' class='btn' id='pr-print-all'{'' if unprinted else ' disabled'}>"
        f"Print all labels ({unprinted} not printed yet)</button><span id='pr-print-msg' class='muted'></span>"
    )
    body = (
        brand_header("Pick run")
        + f"<p class='subtitle'>{len(orders)} open order(s), {sum(l.quantity for l in lines)} item(s) to pick. "
          "Complete each order on the <a href='/ebay-sales'>Pack Assistant</a> once it's packed.</p>"
        + (f"<div class='pr-toolbar'>{print_all}</div>"
           f"<div class='panel'>{_orders_html(orders, tags, printed)}</div>" if orders else
           "<div class='panel'><p class='muted'>Nothing to pick. New eBay orders appear here within ten minutes.</p></div>")
        + "".join(sections)
        + _STYLE + _SCRIPT
    )
    return HTMLResponse(page("EzBay — Pick run", body))


@router.post("/ebay-sales/pick-run/print-labels")
def print_all_labels(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    """Print each open order's address label once. Orders whose label was
    already printed are skipped; the first printer failure stops the run so
    a jammed or offline printer doesn't silently eat the rest."""
    orders = open_orders(session)
    already = printed_order_ids(session, orders)
    tags = order_tags(orders)
    printed: list[str] = []
    for oid in sorted(orders, key=lambda o: tags[o]):
        if oid in already:
            continue
        try:
            print_label(ebay_label_lines(orders[oid][0].raw_snapshot or {}))
        except RuntimeError as exc:
            return JSONResponse(
                {"printed": printed, "skipped": len(already), "error": f"stopped at order {oid}: {exc}"},
                status_code=502,
            )
        record_event(session, actor_type="user", actor_id=user, action=LABEL_PRINTED_ACTION,
                     resource_type="ebay_order", resource_id=oid, outcome="success",
                     correlation_id=str(uuid.uuid4()), details={"via": "pick_run"})
        printed.append(oid)
    return JSONResponse({"printed": printed, "skipped": len(already)})


_STYLE = """<style>
.pr-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:0 0 14px}
.pr-order{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--panel-border)}
.pr-order:last-child{border-bottom:0}
.pr-order-body{flex:1;min-width:0}
.pr-order-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
.pr-printed{margin-left:8px;font-size:12px;font-weight:650;color:var(--text-dim);border:1px solid var(--panel-border);
  border-radius:999px;padding:1px 8px}
.pr-tag{display:inline-flex;align-items:center;justify-content:center;min-width:28px;height:28px;padding:0 6px;
  border-radius:8px;background:var(--text);color:var(--panel);font-weight:800;font-size:15px;flex:none}
.pr-tag.big{min-width:38px;height:38px;font-size:19px}
.pr-section h3{margin:0 0 4px}
.pr-list{list-style:none;margin:8px 0 0;padding:0}
.pr-line label{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--panel-border);cursor:pointer}
.pr-tick{width:22px;height:22px;flex:none}
.pr-photo{width:46px;height:64px;object-fit:cover;border-radius:4px;flex:none;background:var(--surface-sunken)}
.pr-body{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.pr-name{font-weight:650}
.pr-qty{margin-left:6px;font-weight:800}
.pr-meta{color:var(--text-dim);font-size:13px}
.pr-where{display:flex;gap:8px;align-items:baseline;margin-top:2px}
.pr-drawer{font-weight:800;font-size:16px;letter-spacing:.02em}
.pr-slot{font-weight:800;font-size:16px}
.pr-tick:checked ~ .pr-body{opacity:.45;text-decoration:line-through}
@media (max-width:640px){.pr-photo{width:38px;height:53px}.pr-drawer,.pr-slot{font-size:15px}}
</style>"""

_SCRIPT = """<script>
(function(){
  var b = document.getElementById('pr-print-all'); if (!b) return;
  var msg = document.getElementById('pr-print-msg');
  b.addEventListener('click', async function(){
    b.disabled = true; msg.textContent = 'Printing…';
    try {
      var r = await fetch('/ebay-sales/pick-run/print-labels', {method:'POST', body:new FormData()});
      var j = await r.json().catch(function(){return {};});
      var n = (j.printed || []).length;
      msg.textContent = r.ok ? ('Printed ' + n + ' label(s); ' + (j.skipped || 0) + ' already printed.')
                             : ((j.error || ('Failed: ' + r.status)) + (n ? ' (' + n + ' printed first)' : ''));
      if (r.ok) setTimeout(function(){ location.reload(); }, 1500); else b.disabled = false;
    } catch (err) { msg.textContent = 'Failed: ' + err; b.disabled = false; }
  });
})();
</script>"""
