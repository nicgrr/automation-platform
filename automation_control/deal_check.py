"""Read a marketplace listing (photo, pasted text, or both) and check it
against the catalogue -- Module 22. Distinct from quick_price.py (which
reads a photo of a physical card you're holding) and market_search.py
(which searches eBay for a term): this reads a *seller's description* of
what they're offering, at whatever price they've already named, and turns
it into a structured, priced list rather than a wall of text.

Matching is by (set_code, card_number) against tcg_cards -- the same
natural key scripts/import_tcg_catalog_csv.py uses -- not by name, since a
listing's free-text title ("Divine Departure", "The Time of Battle") won't
reliably match a stored card name the way a set code + number does.

Market value is only ever shown when the matched item's catalog_item_id
also has card_prices rows -- true for Pokémon today, honestly absent for
any other game until a real price feed exists for it (see DATABASE.md).
Nothing here writes anything; it's a read-only check, same contract as
quick_price.py.
"""

import uuid
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .buying import _default_thresholds, traffic_light
from .card_recognition import ExtractedListingItem, RecognitionError, extract_listing_items
from .config import get_settings
from .database import get_session
from .inventory_review import _aud, _latest_prices
from .models import CatalogItem, TcgCard
from .ui import brand_header, page, pill

router = APIRouter(prefix="/deal-check", tags=["deal-check"])


def _normalized_number(raw: str) -> str:
    return raw.split("/")[0].strip().lstrip("0") or raw.strip()


def match_listing_item(session: Session, item: ExtractedListingItem) -> tuple[CatalogItem, TcgCard] | None:
    if "set_code" in item.unreadable_fields or "card_number" in item.unreadable_fields:
        return None
    if not item.set_code or not item.card_number:
        return None
    # tcg_cards.number preserves whatever zero-padding its source used
    # (e.g. CSV-imported One Piece cards keep "062"), so match either the
    # number as given or with leading zeros stripped rather than assuming
    # either convention.
    candidates = {item.card_number.strip(), _normalized_number(item.card_number)}
    row = session.execute(
        select(CatalogItem, TcgCard).join(TcgCard, TcgCard.catalog_item_id == CatalogItem.id)
        .where(TcgCard.set_code.ilike(item.set_code), TcgCard.number.in_(candidates))
        .limit(1)
    ).first()
    return tuple(row) if row else None


@router.get("", response_class=HTMLResponse)
def deal_check_page(user: str = Depends(require_dashboard_user)) -> HTMLResponse:
    body = (
        brand_header("Deal check")
        + "<p class='subtitle'>Module 22 -- paste a listing's text, upload a screenshot, or both, and see each "
          "item's asking price next to this app's own catalogue.</p>"
        + "<div class='panel'><form id='deal-check-form' class='calc-form'>"
        + "<label>Listing text (optional if you upload a photo)<textarea name='text' rows='6' placeholder='Paste the listing description here…'></textarea></label>"
        + "<label>Screenshot or photo (optional if you paste text)<input type='file' name='photo' accept='image/*'></label>"
        + "<button type='button' id='deal-check-submit'>Check this listing</button>"
        + "</form>"
        + "<div id='deal-check-status' class='muted' style='margin-top:10px'></div>"
        + "<div id='deal-check-results'></div>"
        + "</div>"
        + _STYLE + _SCRIPT
    )
    return HTMLResponse(page("EzBay — Deal check", body))


@router.post("/extract")
async def deal_check_extract(
    text: str = Form(""), photo: UploadFile | None = File(None),
    user: str = Depends(require_dashboard_user), session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="listing recognition is not configured (ANTHROPIC_API_KEY missing)")

    has_photo = bool(photo and photo.filename)
    if not has_photo and not text.strip():
        raise HTTPException(status_code=400, detail="provide listing text, a photo, or both")

    photo_path: Path | None = None
    tmp_dir_ctx = None
    if has_photo:
        import tempfile
        tmp_dir_ctx = tempfile.TemporaryDirectory()
        suffix = Path(photo.filename or "listing.jpg").suffix or ".jpg"
        photo_path = Path(tmp_dir_ctx.name) / f"listing{suffix}"
        photo_path.write_bytes(await photo.read())

    try:
        try:
            extracted_items = extract_listing_items(settings.anthropic_api_key, photo_path=photo_path, text=text)
        except RecognitionError as exc:
            record_event(session, actor_type="user", actor_id=user, action="deal_check.extract", resource_type="deal_check", resource_id=None, outcome="failed", correlation_id=str(uuid.uuid4()), details={"error": str(exc)})
            raise HTTPException(status_code=502, detail="listing recognition failed") from exc
    finally:
        if tmp_dir_ctx is not None:
            tmp_dir_ctx.cleanup()

    config = _default_thresholds(session)
    results = []
    for item in extracted_items:
        match = match_listing_item(session, item)
        market_value = None
        catalog_name = None
        catalog_item_id = None
        if match is not None:
            catalog_item, tcg_card = match
            catalog_item_id = catalog_item.id
            catalog_name = catalog_item.name
            prices = _latest_prices(session, {catalog_item.id})
            price = next(iter(prices.values()), None)
            market_value = _aud(price)

        acquisition_pct = (Decimal(str(item.asking_price)) / Decimal(str(market_value)) * 100) if market_value else None
        kind, label = traffic_light(acquisition_pct, config)

        results.append({
            "description": item.description, "set_code": item.set_code, "card_number": item.card_number,
            "condition": item.condition, "asking_price": item.asking_price if "asking_price" not in item.unreadable_fields else None,
            "matched": match is not None, "catalog_item_id": catalog_item_id, "catalog_name": catalog_name,
            "market_value": market_value, "verdict": label, "verdict_kind": kind,
        })

    record_event(session, actor_type="user", actor_id=user, action="deal_check.extract", resource_type="deal_check", resource_id=None, outcome="success", correlation_id=str(uuid.uuid4()), details={"count": len(results), "matched": sum(1 for r in results if r["matched"])})
    return {"results": results}


_STYLE = """<style>
#deal-check-results{margin-top:14px;display:flex;flex-direction:column;gap:8px}
.dc-row{display:grid;grid-template-columns:1fr 100px 90px 90px 70px;gap:10px;align-items:center;
  background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;padding:10px 12px;font-size:13px}
.dc-row .dc-desc{font-weight:600}
.dc-row .dc-code{color:var(--text-dim);font-size:12px}
.dc-head{display:grid;grid-template-columns:1fr 100px 90px 90px 70px;gap:10px;font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--text-faint);padding:0 12px}
.muted{color:var(--text-dim);font-size:13px}
</style>"""

_SCRIPT = """<script>
(function() {
  var form = document.getElementById('deal-check-form');
  var submitBtn = document.getElementById('deal-check-submit');
  var status = document.getElementById('deal-check-status');
  var results = document.getElementById('deal-check-results');
  if (!form || !submitBtn) return;

  var KIND_LABEL = { ok: 'GREEN', warn: 'YELLOW', bad: 'RED', neutral: 'UNKNOWN' };

  submitBtn.addEventListener('click', function() {
    var text = form.querySelector('[name="text"]').value;
    var photoInput = form.querySelector('[name="photo"]');
    var photo = photoInput.files[0];
    if (!text.trim() && !photo) { status.textContent = 'Paste some text or choose a photo first.'; return; }

    status.textContent = 'Reading listing…';
    results.innerHTML = '';
    var data = new FormData();
    data.append('text', text);
    if (photo) data.append('photo', photo);

    fetch('/deal-check/extract', { method: 'POST', body: data })
      .then(function(r) {
        if (!r.ok) throw new Error('request failed');
        return r.json();
      })
      .then(function(payload) {
        var items = payload.results || [];
        if (!items.length) { status.textContent = 'No cards recognized in that listing.'; return; }
        status.textContent = items.length + ' item(s) found:';

        var head = document.createElement('div');
        head.className = 'dc-head';
        ['Item', 'Set/No.', 'Asking', 'Market', 'Verdict'].forEach(function(label) {
          var span = document.createElement('span');
          span.textContent = label;
          head.appendChild(span);
        });
        results.appendChild(head);

        items.forEach(function(item) {
          var row = document.createElement('div');
          row.className = 'dc-row';

          var desc = document.createElement('div');
          var name = document.createElement('div');
          name.className = 'dc-desc';
          name.textContent = item.description;
          desc.appendChild(name);
          if (item.matched) {
            var matchLine = document.createElement('div');
            matchLine.className = 'dc-code';
            matchLine.textContent = 'matches: ' + item.catalog_name;
            desc.appendChild(matchLine);
          }
          row.appendChild(desc);

          var code = document.createElement('span');
          code.className = 'dc-code';
          code.textContent = (item.set_code || '?') + '-' + (item.card_number || '?');
          row.appendChild(code);

          var asking = document.createElement('span');
          asking.textContent = item.asking_price != null ? '$' + item.asking_price.toFixed(2) : '—';
          row.appendChild(asking);

          var market = document.createElement('span');
          market.textContent = item.market_value != null ? '$' + item.market_value.toFixed(2) : 'no data';
          row.appendChild(market);

          var verdict = document.createElement('span');
          var pillEl = document.createElement('span');
          pillEl.className = 'pill ' + item.verdict_kind;
          pillEl.textContent = KIND_LABEL[item.verdict_kind] || item.verdict;
          verdict.appendChild(pillEl);
          row.appendChild(verdict);

          results.appendChild(row);
        });
      })
      .catch(function() { status.textContent = 'Reading the listing failed -- try again.'; });
  });
})();
</script>"""
