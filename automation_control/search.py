"""Global search across the generic catalogue (Module 17).

The first real feature built on top of catalog_items/tcg_cards rather than
the Pokémon-specific catalog_cards -- searches every game's cards in one
place (Pokémon today, One Piece as soon as a set is imported via
scripts/import_tcg_catalog_csv.py) and cross-references ownership from
inventory_items.catalog_item_id.

Deliberately scoped to what actually exists yet: customers, suppliers,
sales, and purchase lots aren't built (Phase 5+), so they aren't searched --
extend the UNION below as those tables land rather than searching tables
that don't exist.
"""

from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .database import get_session
from .models import CatalogItem, InventoryItem, TcgCard
from .ui import brand_header, page, pill

router = APIRouter(prefix="/search", tags=["search"])

MAX_RESULTS = 50
SUGGEST_LIMIT = 8
SUGGEST_MIN_CHARS = 2


def _owned_quantities(session: Session, catalog_item_ids: list[str]) -> dict[str, int]:
    if not catalog_item_ids:
        return {}
    rows = session.execute(
        select(InventoryItem.catalog_item_id, func.sum(InventoryItem.quantity))
        .where(InventoryItem.catalog_item_id.in_(catalog_item_ids))
        .group_by(InventoryItem.catalog_item_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def _matching_items(session: Session, query: str, limit: int):
    return session.execute(
        select(CatalogItem, TcgCard)
        .join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id, isouter=True)
        .where(CatalogItem.name.ilike(f"%{query}%"))
        .order_by(CatalogItem.name)
        .limit(limit)
    ).all()


def _detail_text(tcg: TcgCard | None, item: CatalogItem) -> str:
    if tcg is None:
        return item.item_type.value
    return f"{tcg.game} · {tcg.set_code or tcg.set_id or '?'} #{tcg.number or '?'}"


@router.get("/suggest")
def search_suggest(
    q: str = Query("", max_length=200),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    """JSON autocomplete feed for the search box -- same underlying query
    as the full results page, capped much smaller and returned as data
    instead of rendered rows, for a debounced client-side dropdown."""
    query = q.strip()
    if len(query) < SUGGEST_MIN_CHARS:
        return {"results": []}
    matches = _matching_items(session, query, SUGGEST_LIMIT)
    owned = _owned_quantities(session, [item.id for item, _ in matches])
    return {
        "results": [
            {"id": item.id, "name": item.name, "detail": _detail_text(tcg, item), "owned": owned.get(item.id, 0)}
            for item, tcg in matches
        ]
    }


@router.get("", response_class=HTMLResponse)
def search_page(
    q: str = Query("", max_length=200),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    query = q.strip()
    results_html = ""

    if query:
        matches = _matching_items(session, query, MAX_RESULTS)

        owned = _owned_quantities(session, [item.id for item, _ in matches])

        if not matches:
            results_html = "<div class='panel'><p>No catalogue items match that search.</p></div>"
        else:
            rows = []
            for item, tcg in matches:
                qty = owned.get(item.id)
                detail = escape(_detail_text(tcg, item))
                rarity = f" &middot; {escape(tcg.rarity)}" if tcg and tcg.rarity else ""
                owned_badge = pill(f"own {qty}", "ok") if qty else pill("not owned", "neutral")
                rows.append(
                    "<tr>"
                    f"<td><a href='/prices/{escape(item.id)}'>{escape(item.name)}</a></td>"
                    f"<td class='muted'>{detail}{rarity}</td>"
                    f"<td>{owned_badge}</td>"
                    "</tr>"
                )
            results_html = (
                f"<div class='panel'><div class='table-wrap'><table>"
                f"<thead><tr><th>Name</th><th>Details</th><th>Owned</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></div>"
                f"<p class='subtitle'>{len(matches)} result(s){' (capped at ' + str(MAX_RESULTS) + ')' if len(matches) == MAX_RESULTS else ''}</p>"
                f"</div>"
            )
    else:
        results_html = "<div class='panel'><p>Search across every game's catalogue -- type a card name, character, or set.</p></div>"

    body = (
        brand_header("Search")
        + "<form method='get' action='/search' class='search-form'>"
        + "<div class='autocomplete-wrap'>"
        + f"<input id='global-search-input' name='q' value='{escape(query)}' placeholder='Search cards…' autocomplete='off' autofocus>"
        + "<div id='global-search-results' class='autocomplete-dropdown' hidden></div>"
        + "</div>"
        + "<button type='submit'>Search</button>"
        + "</form>"
        + results_html
        + _STYLE
        + AUTOCOMPLETE_SCRIPT
    )
    return HTMLResponse(page("EzBay — Search", body))


_STYLE = """<style>
.search-form{display:flex;gap:10px;margin:0 0 22px;align-items:flex-start}
.autocomplete-wrap{position:relative;flex:1 1 auto}
.autocomplete-wrap input{width:100%;background:#0a0f1c;border:1px solid var(--panel-border);
  border-radius:10px;padding:12px 14px;color:var(--text);font-size:15px}
.search-form button{padding:12px 22px;border-radius:10px;border:none;font-weight:700;cursor:pointer;
  background:linear-gradient(120deg,var(--accent),var(--accent-2));color:#04101a}
.muted{color:var(--text-dim);font-size:13px}

.autocomplete-dropdown{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:20;
  background:var(--panel);border:1px solid var(--panel-border);border-radius:10px;
  box-shadow:0 12px 28px rgba(0,0,0,.35);overflow:hidden}
.ac-item{display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;font-size:13.5px}
.ac-item.active,.ac-item:hover{background:rgba(94,234,255,.08)}
.ac-name{font-weight:650;flex:0 1 auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ac-detail{color:var(--text-dim);font-size:12px;flex:1}
.ac-owned{font-size:11px;color:var(--success);background:rgba(52,211,153,.12);padding:2px 8px;border-radius:999px}
</style>"""

# Vanilla JS, no framework -- matches this project's convention throughout
# (see ui.py's own docstring). Fixed element ids since this only ever
# appears once per page. DOM nodes are built with textContent, never
# innerHTML with interpolated data, so a card name can't be mistaken for
# markup regardless of where it originated (pokemontcg.io import, a CSV
# import, or free text).
AUTOCOMPLETE_SCRIPT = """<script>
(function() {
  var input = document.getElementById('global-search-input');
  var box = document.getElementById('global-search-results');
  if (!input || !box) return;
  var timer = null;
  var items = [];
  var activeIndex = -1;

  function clearBox() {
    box.innerHTML = '';
    box.hidden = true;
    items = [];
    activeIndex = -1;
  }

  function setActive(index) {
    var rows = box.querySelectorAll('.ac-item');
    rows.forEach(function(row) { row.classList.remove('active'); });
    if (index >= 0 && index < rows.length) {
      rows[index].classList.add('active');
      rows[index].scrollIntoView({ block: 'nearest' });
    }
    activeIndex = index;
  }

  function render(results) {
    items = results;
    activeIndex = -1;
    box.innerHTML = '';
    if (!results.length) { box.hidden = true; return; }
    results.forEach(function(r, i) {
      var row = document.createElement('div');
      row.className = 'ac-item';
      row.dataset.index = String(i);

      var name = document.createElement('span');
      name.className = 'ac-name';
      name.textContent = r.name;
      row.appendChild(name);

      var detail = document.createElement('span');
      detail.className = 'ac-detail';
      detail.textContent = r.detail;
      row.appendChild(detail);

      if (r.owned) {
        var owned = document.createElement('span');
        owned.className = 'ac-owned';
        owned.textContent = 'own ' + r.owned;
        row.appendChild(owned);
      }

      row.addEventListener('mousedown', function(e) {
        e.preventDefault();
        window.location.href = '/prices/' + encodeURIComponent(r.id);
      });
      box.appendChild(row);
    });
    box.hidden = false;
  }

  input.addEventListener('input', function() {
    var q = input.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { clearBox(); return; }
    timer = setTimeout(function() {
      fetch('/search/suggest?q=' + encodeURIComponent(q))
        .then(function(r) { return r.json(); })
        .then(function(data) { render(data.results || []); })
        .catch(function() { clearBox(); });
    }, 180);
  });

  input.addEventListener('keydown', function(e) {
    if (box.hidden) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(Math.min(activeIndex + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(Math.max(activeIndex - 1, 0));
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && items[activeIndex]) {
        e.preventDefault();
        window.location.href = '/prices/' + encodeURIComponent(items[activeIndex].id);
      }
    } else if (e.key === 'Escape') {
      clearBox();
    }
  });

  document.addEventListener('click', function(e) {
    if (e.target !== input) clearBox();
  });
})();
</script>"""
