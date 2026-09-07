"""Turn "is this actually foil?" into labelled data instead of a one-off fix.

`assess_foil` runs on every card as it's scanned and logs a guess, but
deliberately never picks the committed variant -- there's no confirmed
sample yet to say its threshold means anything (see that module's
docstring). The first real check of that threshold (2026-09-06, against 758
items) found no separation between "normal" and its flagged candidates: a
single noise-centred bell curve, not two clusters. Two confirmed
reverse-holos that same night sat at body_bias 6 and 12, clearly outside
that noise band -- real signal, but two points is not a calibration.

This page is where more of those points come from. It surfaces one
candidate at a time -- an inventory item filed as one printing whose photo
the weak signal thinks looks like another -- and asks a human to confirm or
reject it. Every answer is kept as a `FoilLabel` row (metrics included) so
the threshold can eventually be set from real confirmed examples across
every set, not a guess. A "yes" also re-files the item immediately, using
the same merge-or-create logic `commit_card` uses for a normal scan.
"""

import json
import uuid
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .models import CardSet, CardVariant, CatalogCard, FoilLabel, InventoryItem
from .scan_ingest.commit import _image_quality, _store_scan_image
from .scan_ingest.foil import assess_foil
from .ui import brand_header, page

router = APIRouter(prefix="/foil-review", tags=["foil-review"])

# A photo whose own distance from its filed card is this high isn't a foil
# question at all -- the photo doesn't match the card it's filed under in
# the first place. Confirmed against a real item (2026-09-06): a "151
# Butterfree" whose stored photo was actually an unsplit 2x3 block of six
# different cards (an old merge-detection miss, the same class of bug
# `_split_merged_cards` exists for) scored whole_d=32/art_d=26 against its
# own Butterfree reference -- `assess_foil` still dutifully reported a
# body-bias "reverse holo" suggestion on top of that garbage, which is
# nonsense on a photo that isn't reliably even the right card. That's
# audit_inventory_photos.py's problem to flag, not a foil-variant question;
# matches that script's own default mismatch threshold.
IDENTITY_MISMATCH_THRESHOLD = 12

# Rescanning every item's photo against its reference takes real time
# (~60s at 758 items) -- too slow to redo on every page view of what's meant
# to be a quick tap-through. Cached to a file rather than an in-process
# variable so `scripts.refresh_foil_candidates` (run on a schedule, see
# inventory-audit.timer's sibling) can keep it current from outside the web
# process -- an in-memory cache would never see writes from that separate
# process, only ever refreshing on this one's next cold start.
def _cache_path() -> Path:
    return Path(get_settings().scan_media_dir) / "foil_candidates_cache.json"


def _save_candidates(candidates: list[dict]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candidates))


def _load_or_compute(session: Session) -> list[dict]:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    candidates = _compute_candidates(session)
    _save_candidates(candidates)
    return candidates


def _labeled_item_ids(session: Session) -> set[str]:
    return set(session.scalars(select(FoilLabel.inventory_item_id)))


def _compute_candidates(session: Session) -> list[dict]:
    set_names = {s.id: s.name for s in session.scalars(select(CardSet))}
    cards_by_id = {c.id: c for c in session.scalars(select(CatalogCard))}
    items = session.scalars(select(InventoryItem)).all()

    candidates = []
    for item in items:
        if not item.scan_image_path:
            continue
        path = Path(item.scan_image_path)
        if not path.exists():
            continue
        card = cards_by_id.get(item.card_id)
        if card is None:
            continue
        assessment = assess_foil(path, card)
        if assessment.suggestion is None or assessment.suggestion == item.variant:
            continue
        own_distance = min(
            assessment.metrics.get("whole_d", float("inf")),
            assessment.metrics.get("art_d", float("inf")),
        )
        if own_distance > IDENTITY_MISMATCH_THRESHOLD:
            continue
        bias = assessment.metrics.get("body_bias")
        candidates.append({
            "item_id": item.id,
            "card_id": card.id,
            "label": f"{set_names.get(card.set_id, card.set_id)} #{card.number} {card.name}",
            "filed": item.variant.value,
            "suggested": assessment.suggestion.value,
            "confidence": assessment.confidence,
            "reason": assessment.reason,
            "metrics": assessment.metrics,
            "magnitude": abs(bias) if bias is not None else 0.0,
        })
    candidates.sort(key=lambda c: -c["magnitude"])
    return candidates


def _pending(session: Session) -> list[dict]:
    candidates = _load_or_compute(session)
    labeled = _labeled_item_ids(session)
    return [c for c in candidates if c["item_id"] not in labeled]


@router.get("", response_class=HTMLResponse)
def foil_review_page(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)) -> HTMLResponse:
    pending = _pending(session)
    all_labels = session.scalars(select(FoilLabel)).all()
    labeled_count = len(all_labels)
    confirmed_count = sum(1 for label in all_labels if label.confirmed)

    header = (
        brand_header("Foil review")
        + "<p class='subtitle'>Cards the weak foil signal thinks are filed under the wrong printing. "
          "Pick what it actually is from the photo -- every answer helps calibrate detection across every set.</p>"
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Awaiting your call</div><div class='value'>{len(pending)}</div></div>"
        + f"<div class='stat-card'><div class='label'>Labelled so far</div><div class='value'>{labeled_count} <span class='value-sub'>({confirmed_count} confirmed)</span></div></div>"
        + "</div>"
        + "<div class='toolbar'><form method='post' action='/foil-review/rescan'>"
          "<button class='chip' type='submit'>Rescan inventory for new candidates</button></form></div>"
    )

    if not pending:
        body = header + "<div class='panel'><p>Nothing left to check right now.</p></div>"
        return HTMLResponse(page("EzBay — Foil review", body + _STYLE))

    c = pending[0]
    thumb = f"<img src='/foil-review/image/{escape(c['item_id'])}' alt='' loading='lazy' decoding='async'>"
    metrics_line = " ".join(f"{k}={v:.2f}" for k, v in c["metrics"].items())

    # All three printings are offered, not just a yes/no on the one the
    # signal guessed -- a card can genuinely be foil while the signal named
    # the wrong *kind* (confirmed live: an Articuno the signal called
    # reverse_holo that was actually a plain holo, which a binary choice
    # had no way to record).
    variant_buttons = "".join(
        "<button name='actual_variant' value='{value}' class='variant-btn{current}'>{label}{tags}</button>".format(
            value=v.value,
            current=" current" if v.value == c["filed"] else "",
            label=escape(v.value.replace("_", " ").title()),
            tags="".join([
                "<span class='tag'>filed as</span>" if v.value == c["filed"] else "",
                "<span class='tag'>signal thinks</span>" if v.value == c["suggested"] else "",
            ]),
        )
        for v in CardVariant
    )

    body = (
        header
        + "<div class='foil-card'>"
        + f"<a class='foil-face' href='/foil-review/image/{escape(c['item_id'])}' target='_blank' rel='noopener'>{thumb}</a>"
        + f"<div class='foil-name'>{escape(c['label'])}</div>"
        + f"<div class='foil-reason'>{escape(c['reason'])}</div>"
        + f"<div class='foil-metrics'>{escape(metrics_line)}</div>"
        + "<div class='foil-prompt'>What is it really?</div>"
        + "<form method='post' action='/foil-review/decide' class='foil-form'>"
        + f"<input type='hidden' name='item_id' value='{escape(c['item_id'])}'>"
        + f"<input type='hidden' name='suggested_variant' value='{escape(c['suggested'])}'>"
        + variant_buttons
        + "</form></div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Foil review", body))


@router.post("/rescan")
def foil_review_rescan(user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    _save_candidates(_compute_candidates(session))
    return RedirectResponse("/foil-review", status_code=303)


@router.post("/decide")
def foil_review_decide(
    item_id: str = Form(...),
    suggested_variant: str = Form(...),
    actual_variant: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="inventory item not found")
    card = session.get(CatalogCard, item.card_id)
    if card is None or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="card or photo missing")

    assessment = assess_foil(Path(item.scan_image_path), card)
    suggested = CardVariant(suggested_variant)
    correct_variant = CardVariant(actual_variant)

    session.add(FoilLabel(
        id=str(uuid.uuid4()), inventory_item_id=item.id, card_id=card.id,
        filed_variant=item.variant, suggested_variant=suggested, actual_variant=correct_variant,
        confirmed=(correct_variant == suggested), metrics=assessment.metrics, labeled_by=user,
    ))

    if item.variant != correct_variant:
        media_dir = Path(get_settings().scan_media_dir)
        set_code = (card.card_set.code or card.set_id) if card.card_set else card.set_id
        existing = session.scalar(
            select(InventoryItem).where(
                InventoryItem.card_id == item.card_id,
                InventoryItem.variant == correct_variant,
                InventoryItem.condition == item.condition,
            )
        )
        if existing:
            existing.quantity += item.quantity
            if item.scan_image_path and (
                not existing.scan_image_path
                or _image_quality(Path(item.scan_image_path)) > _image_quality(Path(existing.scan_image_path))
            ):
                stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, card.number, correct_variant, item.condition)
                existing.scan_image_path = str(stored)
            session.delete(item)
        else:
            stored = _store_scan_image(Path(item.scan_image_path), media_dir, set_code, card.number, correct_variant, item.condition)
            item.scan_image_path = str(stored)
            item.variant = correct_variant

    session.commit()
    return RedirectResponse("/foil-review", status_code=303)


@router.get("/image/{item_id}")
def foil_review_image(item_id: str, user: str = Depends(require_dashboard_user), session: Session = Depends(get_session)):
    item = session.get(InventoryItem, item_id)
    if item is None or not item.scan_image_path:
        raise HTTPException(status_code=404, detail="no photo for that item")
    path = Path(item.scan_image_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="photo file missing")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})


_STYLE = """<style>
.toolbar{display:flex;gap:10px;align-items:center;margin:14px 0 22px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;padding:10px 16px;border-radius:999px;font-size:14px;
  border:1px solid var(--panel-border);color:var(--text-dim);background:var(--surface-sunken);cursor:pointer}
.chip:hover{border-color:var(--accent);color:var(--accent)}

.foil-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:16px;
  padding:20px;display:flex;flex-direction:column;gap:10px;max-width:480px;margin:0 auto}
.foil-face{display:block;border-radius:12px;overflow:hidden;background:var(--surface-sunken);
  aspect-ratio:5/7;max-height:56vh;margin:0 auto}
.foil-face img{width:100%;height:100%;object-fit:contain;display:block}
.foil-name{font-size:17px;font-weight:650;text-align:center}
.foil-reason{font-size:13px;color:var(--text-dim);text-align:center}
.foil-metrics{font-size:11px;color:var(--text-dim);opacity:.7;text-align:center;font-family:monospace}
.foil-prompt{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-dim);
  text-align:center;margin-top:6px}
.foil-form{display:flex;gap:10px;margin-top:2px;flex-wrap:wrap}
.variant-btn{flex:1 1 140px;padding:14px 10px;border-radius:10px;font-size:15px;font-weight:700;
  cursor:pointer;border:1px solid var(--panel-border);background:none;color:var(--text);
  display:flex;flex-direction:column;align-items:center;gap:5px}
.variant-btn:hover{border-color:var(--accent);color:var(--accent)}
.variant-btn.current{border-color:var(--accent-2)}
.variant-btn .tag{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
  color:var(--text-dim);background:var(--surface-sunken);padding:2px 7px;border-radius:999px}
</style>"""
