"""Browse and settle the cards unattended scanning set aside.

The background service never guesses: a card it isn't confident about is
copied to `scan_media_dir/needs_review/` instead of being committed (see
scan_ingest/session.py). Until now the only way to work that queue was the
`scan-ingest review` CLI, which means being at a terminal. This is the same
decision, in the browser: see the card, see what the matcher thinks, and
accept, correct, or discard it.

Every commit here goes through `commit_card` exactly as the CLI does, so the
audit trail and dedup behaviour are identical. Nothing is ever committed
without an explicit click -- the whole point of the queue is that a human
decides.
"""

from html import escape
from pathlib import Path

import imagehash
import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image
from sqlalchemy.orm import Session

from .auth import require_dashboard_user
from .config import get_settings
from .database import get_session
from .models import CardVariant
from .scan_ingest import catalog
from .scan_ingest.commit import CommitRefused, commit_card
from .scan_ingest.identify import Identification, Source, _phash_confidence
from .scan_ingest.session import _load_all_catalogs, start_or_resume
from .ui import brand_header, page, pill

router = APIRouter(prefix="/review", tags=["review"])

# Matching a crop against every cached set costs real time (one hash of the
# crop, then a compare against each cached card), and the catalogue grows.
# A page of cards is plenty to work through in one sitting.
PAGE_SIZE = 12

# How many possible cards to offer per crop. Enough that the right one is
# almost always present when the top match is wrong, few enough to scan.
CANDIDATES = 3


class _WebPrompter:
    """`start_or_resume` takes a Prompter; in unattended mode it never asks
    anything, so this only has to absorb the notifications."""

    def ask(self, question: str, default: str = "") -> str:
        return default

    def confirm(self, question: str, default: bool = True) -> bool:
        return default

    def notify(self, message: str) -> None:
        pass


def _suggestions(crops: list[Path], catalogs, totals: dict, settings) -> dict[Path, tuple | None]:
    """Best (set, identification) for each crop on the page.

    Same decision as `session._best_across_sets`, but built for a page at a
    time rather than a card at a time -- that version took 11s for twelve
    crops once the catalogue reached ~14k cards.
    """
    # Every reference hash as one boolean bit-matrix, so a crop is compared
    # to the entire catalogue in a single vectorised operation instead of a
    # Python loop per card. With ~14k cached cards the loop version took
    # seconds per page; this is milliseconds.
    entries, bits = [], []
    for set_id, references in catalogs.items():
        for reference in references:
            if reference.phash:
                entries.append((set_id, reference))
                bits.append(imagehash.hex_to_hash(reference.phash).hash.flatten())
    if not entries:
        return {crop: None for crop in crops}
    matrix = np.array(bits, dtype=bool)

    # No OCR here, deliberately: identify_card would run tesseract per crop
    # (measured at several seconds a page) to reconcile a printed number the
    # operator can simply read off the image in front of them. The art match
    # alone is what this page needs -- and the number field is editable.
    out: dict[Path, tuple | None] = {}
    for crop in crops:
        try:
            with Image.open(crop) as image:
                scan = imagehash.phash(image.convert("RGB")).hash.flatten()
        except Exception:
            out[crop] = None
            continue

        distances = np.count_nonzero(matrix != scan, axis=1)
        order = np.argsort(distances, kind="stable")
        best = int(distances[order[0]])
        if best > settings.scan_phash_max_distance:
            out[crop] = None
            continue

        # Several candidates, not one. Across ~18k cached cards an unrelated
        # card can genuinely hash closer than the right one -- measured on a
        # Cosmic Eclipse Flabebe whose nearest match was a Guardians Rising
        # Gothorita at distance 6, with the correct card second at 8. One
        # confident-looking wrong answer is worse than a short list, and
        # picking from a list is a single click either way.
        seen: set[str] = set()
        candidates = []
        for i in order[: CANDIDATES * 4]:
            distance = int(distances[i])
            if distance > settings.scan_phash_max_distance:
                break
            set_id, reference = entries[i]
            if reference.card_id in seen:
                continue
            seen.add(reference.card_id)
            margin = distance - best
            candidates.append((
                set_id,
                Identification(
                    card_id=reference.card_id, number=reference.number, name=reference.name,
                    source=Source.PHASH,
                    confidence=_phash_confidence(distance, margin, settings.scan_phash_max_distance),
                    phash_distance=distance, phash_margin=margin,
                ),
            ))
            if len(candidates) == CANDIDATES:
                break
        out[crop] = candidates or None
    return out


def _set_for_printed_total(session, printed_total: int, number: str, crop: Path, catalogs) -> str:
    """Which cached set a "N/TOTAL" refers to.

    The printed total usually identifies a set outright. A handful share one
    (Chilling Reign and Scarlet & Violet are both /198), so where it's
    ambiguous the crop's own art breaks the tie -- comparing only the
    candidate sets' card #N, which is a far easier question than searching
    the whole catalogue.
    """
    candidates = [
        s.id for s in catalog.cached_sets(session)
        if s.printed_total == printed_total and s.id in catalogs
    ]
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"no cached set has {printed_total} printed cards -- cache it first "
                   f"(scan-ingest cache-set) and it'll be matchable",
        )
    if len(candidates) == 1:
        return candidates[0]

    try:
        with Image.open(crop) as image:
            scan = imagehash.phash(image.convert("RGB"))
    except Exception:
        return candidates[0]

    best, best_distance = candidates[0], None
    for set_id in candidates:
        reference = next((r for r in catalogs[set_id] if r.number == number and r.phash), None)
        if reference is None:
            continue
        distance = scan - imagehash.hex_to_hash(reference.phash)
        if best_distance is None or distance < best_distance:
            best, best_distance = set_id, distance
    return best


def _review_dir() -> Path:
    return Path(get_settings().scan_media_dir) / "needs_review"


def _queued() -> list[Path]:
    directory = _review_dir()
    return sorted(directory.glob("*.jpg")) if directory.exists() else []


def _safe_crop(name: str) -> Path:
    """Resolve a queued crop by name, refusing anything that escapes the
    review directory -- the name arrives from a form field."""
    directory = _review_dir().resolve()
    candidate = (directory / name).resolve()
    if not candidate.is_file() or candidate.parent != directory:
        raise HTTPException(status_code=404, detail="not in the review queue")
    return candidate


@router.get("", response_class=HTMLResponse)
def review_page(
    request: Request,
    page_number: int = Query(1, alias="page", ge=1),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    crops = _queued()
    total = len(crops)
    if not total:
        body = (
            brand_header("Review queue")
            + "<p class='subtitle'>Cards the scanner wasn't confident about.</p>"
            + "<div class='panel'><p>Queue is empty — everything scanned so far has been "
              "committed or discarded.</p><p><a class='btn' href='/inventory'>Browse inventory</a></p></div>"
        )
        return HTMLResponse(page("EzBay — Review queue", body))

    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page_number = min(page_number, pages)
    window = crops[(page_number - 1) * PAGE_SIZE : page_number * PAGE_SIZE]

    settings = get_settings()
    catalogs = _load_all_catalogs(session)
    totals = {s.id: s.printed_total for s in catalog.cached_sets(session)}
    names = {s.id: s.name for s in catalog.cached_sets(session)}

    suggested = _suggestions(window, catalogs, totals, settings) if catalogs else {}
    cards = [_card_html(crop, suggested.get(crop), names) for crop in window]

    nav = []
    if page_number > 1:
        nav.append(f"<a class='chip' href='/review?page={page_number - 1}'>&larr; Previous</a>")
    nav.append(f"<span class='muted'>Page {page_number} of {pages}</span>")
    if page_number < pages:
        nav.append(f"<a class='chip' href='/review?page={page_number + 1}'>Next &rarr;</a>")

    body = (
        brand_header("Review queue")
        + "<p class='subtitle'>Cards the scanner wasn't confident about. Nothing here is in your "
          "inventory yet — accept, correct, or discard each one.</p>"
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Awaiting review</div><div class='value'>{total}</div></div>"
        + f"<div class='stat-card'><div class='label'>Cached sets to match against</div><div class='value'>{len(catalogs)}</div></div>"
        + "</div>"
        + f"<div class='toolbar'>{''.join(nav)}</div>"
        + f"<div class='review-grid'>{''.join(cards)}</div>"
        + _STYLE
    )
    return HTMLResponse(page("EzBay — Review queue", body))


def _card_html(crop: Path, candidates, set_names: dict[str, str]) -> str:
    name = crop.name
    thumb = f"<img src='/review/image/{escape(name)}' alt='' loading='lazy' decoding='async'>"

    if not candidates:
        options = (
            "<div class='no-match'>No confident match in any cached set.</div>"
            "<div class='hint'>Type the number as printed on the card (e.g. 166/236) &mdash; "
            "the total identifies the set.</div>"
        )
        default_set = ""
    else:
        default_set = candidates[0][0]
        rows = []
        for set_id, identification in candidates:
            confidence = identification.confidence
            kind = "ok" if confidence >= 0.75 else "warn"
            rows.append(
                f"<button class='pick' name='pick' value='{escape(set_id)}:{escape(identification.number or '')}'>"
                f"<span class='pick-name'>#{escape(identification.number or '')} {escape(identification.name or '')}</span>"
                f"<span class='pick-meta'>{escape(set_names.get(set_id, set_id))} &middot; "
                f"d{identification.phash_distance} {pill(f'{confidence:.2f}', kind)}</span>"
                "</button>"
            )
        options = "<div class='picks'>" + "".join(rows) + "</div>"

    return (
        "<div class='review-card'>"
        f"<a class='review-face' href='/review/image/{escape(name)}' target='_blank' rel='noopener'>{thumb}</a>"
        "<form method='post' action='/review/decide' class='review-form'>"
        f"<input type='hidden' name='crop' value='{escape(name)}'>"
        f"<input type='hidden' name='set_id' value='{escape(default_set)}'>"
        f"{options}"
        f"<div class='filename' title='{escape(name)}'>{escape(name)}</div>"
        "<div class='manual'>"
        "<input name='number' placeholder='or type 166/236' autocomplete='off' inputmode='numeric'>"
        "<button name='action' value='accept'>Accept</button>"
        "<button name='action' value='discard' class='ghost'>Discard</button>"
        "</div></form></div>"
    )


@router.post("/decide")
def review_decide(
    crop: str = Form(...),
    action: str = Form(""),
    pick: str = Form(""),
    number: str = Form(""),
    set_id: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    path = _safe_crop(crop)

    if action == "discard":
        path.unlink(missing_ok=True)
        return RedirectResponse("/review", status_code=303)

    # A pick button carries its own set, so choosing a card from a set other
    # than the top match is one click rather than a retype.
    if pick:
        chosen_set, _, chosen_number = pick.partition(":")
        set_id, number = chosen_set, chosen_number
    elif action != "accept":
        raise HTTPException(status_code=400, detail=f"unknown action: {action!r}")

    typed = (number or "").strip()
    catalogs = _load_all_catalogs(session)

    # "166/236" -- exactly what's printed on the card. The denominator names
    # the set, which matters because the typed number otherwise applies to
    # whichever set the top match happened to be in; when every suggestion is
    # wrong (the common case for anything reaching this queue) that set is
    # wrong too, and there was no way to say otherwise.
    if "/" in typed:
        raw_number, _, raw_total = typed.partition("/")
        number = raw_number.strip().lstrip("0") or ""
        try:
            printed_total = int(raw_total.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"couldn't read a set size from {typed!r} -- try e.g. 166/236")
        set_id = _set_for_printed_total(session, printed_total, number, path, catalogs)
    else:
        number = typed.lstrip("0") or ""

    if not set_id or not number:
        raise HTTPException(
            status_code=400,
            detail="a card number is required -- type it as it's printed (e.g. 166/236) to also identify the set",
        )

    references = catalogs.get(set_id)
    if not references:
        raise HTTPException(status_code=400, detail=f"set {set_id!r} is not cached")
    match = next((r for r in references if r.number == number), None)
    if match is None:
        raise HTTPException(
            status_code=400,
            detail=f"no card #{number} in {set_id} -- if that's the wrong set, type the number as printed (e.g. {number}/236)",
        )

    settings = get_settings()
    scan_session = start_or_resume(session, set_id, CardVariant.NORMAL, _WebPrompter(), user=user, unattended=True)
    identification = Identification(
        card_id=match.card_id, number=match.number, name=match.name,
        source=Source.PHASH, confidence=1.0, note="confirmed in web review",
    )
    try:
        commit_card(
            session, identification, path, Path(settings.scan_media_dir),
            variant=CardVariant.NORMAL, condition="Near Mint",
            scan_session=scan_session, user=user, allow_unconfirmed=True,
        )
    except CommitRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path.unlink(missing_ok=True)
    return RedirectResponse("/review", status_code=303)


@router.get("/image/{name}")
def review_image(name: str, user: str = Depends(require_dashboard_user)):
    return FileResponse(_safe_crop(name), headers={"Cache-Control": "private, max-age=300"})


_STYLE = """<style>
.toolbar{display:flex;gap:12px;align-items:center;margin:0 0 20px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;font-size:13px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:#0a0f1c}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.muted{color:var(--text-dim);font-size:13px}
.review-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px}
.review-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:14px;padding:12px;
  display:flex;flex-direction:column;gap:5px}
.review-face{display:block;border-radius:9px;overflow:hidden;background:#0a0f1c;aspect-ratio:5/7}
.review-face img{width:100%;height:100%;object-fit:contain;display:block}
.suggest{font-size:14px;margin-top:7px}
.no-match{font-size:13px;color:var(--status-warn,#d4a527);margin-top:7px}
.hint{font-size:12px;color:var(--text-dim)}
.filename{font-size:10.5px;color:var(--text-dim);opacity:.7;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.review-form{display:flex;gap:6px;margin-top:8px;flex-direction:column;max-width:none}
.picks{display:flex;flex-direction:column;gap:5px;margin-top:7px}
.pick{display:flex;flex-direction:column;align-items:flex-start;gap:2px;text-align:left;width:100%;
  background:#0a0f1c;border:1px solid var(--panel-border);border-radius:9px;padding:7px 9px;cursor:pointer;color:var(--text)}
.pick:hover{border-color:var(--accent)}
.pick-name{font-size:13px;font-weight:600}
.pick-meta{font-size:11px;color:var(--text-dim)}
.manual{display:flex;gap:6px;margin-top:4px}
.review-form input{flex:1 1 68px;min-width:0;background:#0a0f1c;border:1px solid var(--panel-border);
  border-radius:8px;padding:8px;color:var(--text);font-size:13px}
.review-form button{flex:0 0 auto;padding:8px 12px;border-radius:8px;font-size:13px;cursor:pointer;border:none;
  background:linear-gradient(120deg,var(--accent),var(--accent-2));color:#04101a;font-weight:700}
.review-form button.ghost{background:none;border:1px solid var(--panel-border);color:var(--text-dim);font-weight:600}
.review-form button.ghost:hover{border-color:var(--status-critical,#f87171);color:var(--status-critical,#f87171)}
@media (max-width:560px){.review-grid{grid-template-columns:repeat(2,1fr);gap:12px}}
</style>"""
