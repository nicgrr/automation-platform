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
from .models import CatalogItem, SealedProduct, TcgCard
from .ui import brand_header, page, pill

router = APIRouter(prefix="/deal-check", tags=["deal-check"])

# Words too generic to count as a real match signal on their own -- every
# Pokemon listing says "Pokemon" and "TCG", so scoring on raw word overlap
# would let two unrelated products both called "Pokemon TCG ... Box" match
# each other.
_SEALED_STOPWORDS = {
    "pokemon", "pokémon", "tcg", "one", "piece", "the", "and", "box", "sealed",
    "new", "factory", "trading", "card", "game", "set",
}


def _sealed_name_tokens(text: str) -> set[str]:
    return {w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split() if w not in _SEALED_STOPWORDS}


def match_sealed_product(session: Session, description: str) -> CatalogItem | None:
    """Elite Trainer Boxes, booster boxes, displays -- these have no set
    code or card number to key off (match_listing_item's usual approach),
    so this matches on the listing's free-text title instead: whichever
    sealed product shares the most of its own distinctive name-words with
    the listing wins, as long as it clears a real bar (at least 2 words,
    and most of the product's own name accounted for) rather than a loose
    guess. Deliberately conservative -- a wrong sealed-product match would
    show a shopper someone else's price."""
    listing_words = _sealed_name_tokens(description)
    if not listing_words:
        return None

    best: tuple[float, CatalogItem] | None = None
    rows = session.execute(select(CatalogItem, SealedProduct).join(SealedProduct, SealedProduct.catalog_item_id == CatalogItem.id))
    for catalog_item, _sealed in rows:
        product_words = _sealed_name_tokens(catalog_item.name)
        if len(product_words) < 2:
            continue
        overlap = product_words & listing_words
        if len(overlap) < 2:
            continue
        coverage = len(overlap) / len(product_words)
        if coverage < 0.6:
            continue
        if best is None or coverage > best[0]:
            best = (coverage, catalog_item)
    return best[1] if best else None


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
        market_value_is_rrp = False
        catalog_name = None
        catalog_item_id = None
        matched_item: CatalogItem | None = None

        if match is not None:
            matched_item, _tcg_card = match
            prices = _latest_prices(session, {matched_item.id})
            price = next(iter(prices.values()), None)
            market_value = _aud(price)
        else:
            # No card fields to key off at all (set_code/card_number are
            # meaningless on a box) -- try matching the listing's title
            # against sealed products instead. There's no market-price
            # feed for sealed product yet (see this module's docstring),
            # so RRP stands in as a labelled reference rather than a bare
            # "no data" a shopper has nothing to weigh a box deal against.
            sealed_item = match_sealed_product(session, item.description)
            if sealed_item is not None:
                matched_item = sealed_item
                prices = _latest_prices(session, {sealed_item.id})
                price = next(iter(prices.values()), None)
                if price is not None:
                    market_value = _aud(price)
                else:
                    sealed = session.get(SealedProduct, sealed_item.id)
                    if sealed is not None and sealed.rrp is not None:
                        market_value = float(sealed.rrp)
                        market_value_is_rrp = True

        if matched_item is not None:
            catalog_item_id = matched_item.id
            catalog_name = matched_item.name

        acquisition_pct = (Decimal(str(item.asking_price)) / Decimal(str(market_value)) * 100) if market_value else None
        kind, label = traffic_light(acquisition_pct, config)

        results.append({
            "description": item.description, "set_code": item.set_code, "card_number": item.card_number,
            "condition": item.condition, "asking_price": item.asking_price if "asking_price" not in item.unreadable_fields else None,
            "matched": matched_item is not None, "catalog_item_id": catalog_item_id, "catalog_name": catalog_name,
            "market_value": market_value, "market_value_is_rrp": market_value_is_rrp,
            "verdict": label, "verdict_kind": kind,
        })

    record_event(session, actor_type="user", actor_id=user, action="deal_check.extract", resource_type="deal_check", resource_id=None, outcome="success", correlation_id=str(uuid.uuid4()), details={"count": len(results), "matched": sum(1 for r in results if r["matched"])})
    return {"results": results}


_STYLE = """<style>
#deal-check-results{margin-top:14px;display:flex;flex-direction:column;gap:8px;max-width:100%;overflow-x:auto}
.dc-row{display:grid;grid-template-columns:1fr 100px 90px 90px 70px;gap:10px;align-items:center;
  background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:8px;padding:10px 12px;font-size:13px}
.dc-row .dc-desc{font-weight:600}
.dc-row .dc-code{color:var(--text-dim);font-size:12px}
.dc-head{display:grid;grid-template-columns:1fr 100px 90px 90px 70px;gap:10px;font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;color:var(--text-faint);padding:0 12px}
.muted{color:var(--text-dim);font-size:13px}

/* Narrow phone screens: the 5-column grid has no room to breathe (this is
   exactly the bug that broke the topbar search box earlier -- fixed
   columns plus a flexible one don't shrink below their content on their
   own), so stack each result into a small card of label:value lines
   instead of trying to force a table into ~360px. */
@media (max-width: 640px) {
  .dc-head { display: none; }
  .dc-row { grid-template-columns: 1fr; gap: 4px; }
  .dc-row > [data-label] { display: flex; justify-content: space-between; gap: 10px; }
  .dc-row > [data-label]::before { content: attr(data-label); color: var(--text-faint); font-weight: 600; flex: none; }
}
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
    if (window.EzBayProgress) window.EzBayProgress.begin('POST /deal-check/extract');
    var failed = false;
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
          code.setAttribute('data-label', 'Set/No.');
          code.textContent = (item.set_code || item.card_number)
            ? (item.set_code || '?') + '-' + (item.card_number || '?')
            : (item.catalog_name || 'no card no.');
          row.appendChild(code);

          var asking = document.createElement('span');
          asking.setAttribute('data-label', 'Asking');
          asking.textContent = item.asking_price != null ? '$' + item.asking_price.toFixed(2) : '—';
          row.appendChild(asking);

          var market = document.createElement('span');
          market.setAttribute('data-label', 'Market');
          market.textContent = item.market_value != null
            ? '$' + item.market_value.toFixed(2) + (item.market_value_is_rrp ? ' (RRP)' : '')
            : 'no data';
          row.appendChild(market);

          var verdict = document.createElement('span');
          verdict.setAttribute('data-label', 'Verdict');
          var pillEl = document.createElement('span');
          pillEl.className = 'pill ' + item.verdict_kind;
          pillEl.textContent = KIND_LABEL[item.verdict_kind] || item.verdict;
          verdict.appendChild(pillEl);
          row.appendChild(verdict);

          results.appendChild(row);
        });
      })
      .catch(function() { failed = true; status.textContent = 'Reading the listing failed -- try again.'; })
      // A request that failed fast is not evidence of how long a real read
      // takes, so only a successful one updates the estimate.
      .then(function(){ if (window.EzBayProgress) window.EzBayProgress.done(failed ? null : 'POST /deal-check/extract'); });
  });
})();
</script>"""
