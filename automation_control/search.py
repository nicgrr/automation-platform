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
        results_html = "<div class='panel'><p>Search across every game's catalogue -- type a card name, character, or set above.</p></div>"

    body = brand_header("Search") + results_html + _STYLE
    return HTMLResponse(page("EzBay — Search", body, search_value=query))


_STYLE = """<style>
.muted{color:var(--text-dim);font-size:13px}
</style>"""
