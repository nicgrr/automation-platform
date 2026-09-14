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

import re
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
from .scan_feed import _archived_sheet
from .scan_ingest import catalog
from .scan_ingest.commit import CommitRefused, commit_card
from .scan_ingest.identify import Identification, Source, _phash_confidence, art_phash
from .scan_ingest.session import _load_all_catalogs, start_or_resume
from .ui import brand_header, page, pill

# A queued crop is named "{sheet stem}-card{N}-{label}.jpg" (see
# session._set_aside_for_review) -- recovering the stem and position from it
# is what lets the review page show the whole sheet next to the single crop,
# so a genuinely ambiguous or truncated card can be placed by eye against its
# neighbours instead of judged from an isolated square.
_CROP_NAME_RE = re.compile(r"^(sheet-\d{8}-\d{6}-\d+)-card(\d+)-")

router = APIRouter(prefix="/review", tags=["review"])

# One card per page. A grid of them was cramped on a phone -- the card image
# is the thing you actually judge from, and shrinking it to fit two columns
# made the number unreadable, which is the whole decision. One card gets the
# full width, and paging through is a single tap either way.
PAGE_SIZE = 1

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
    entries, bits, art_bits = [], [], []
    for set_id, references in catalogs.items():
        for reference in references:
            if reference.phash:
                entries.append((set_id, reference))
                bits.append(imagehash.hex_to_hash(reference.phash).hash.flatten())
                art = getattr(reference, "art_phash", None)
                art_bits.append(imagehash.hex_to_hash(art).hash.flatten() if art else None)
    if not entries:
        return {crop: None for crop in crops}
    matrix = np.array(bits, dtype=bool)
    # Reverse holos barely resemble their flat reference art anywhere except
    # the artwork window, so that window is matched as its own signal rather
    # than blended into the whole-card distance. Blending was tried first --
    # taking the smaller of the two distances -- and is actively wrong: a
    # wrong card's good whole-card score then beats the right card's good
    # art score (measured on a Passimian, correct card rank 1 on art alone
    # but rank 7 once the two were combined by minimum).
    has_art = [i for i, b in enumerate(art_bits) if b is not None]
    art_matrix = np.array([art_bits[i] for i in has_art], dtype=bool) if has_art else None

    # No OCR here, deliberately: identify_card would run tesseract per crop
    # (measured at several seconds a page) to reconcile a printed number the
    # operator can simply read off the image in front of them. The art match
    # alone is what this page needs -- and the number field is editable.
    out: dict[Path, tuple | None] = {}
    for crop in crops:
        try:
            with Image.open(crop) as image:
                rgb = image.convert("RGB")
                scan = imagehash.phash(rgb).hash.flatten()
                scan_art = art_phash(rgb).hash.flatten()
        except Exception:
            out[crop] = None
            continue

        distances = np.count_nonzero(matrix != scan, axis=1)
        # Each signal ranks independently; the merged shortlist below takes
        # the best of both, so a card only one of them finds still appears.
        ranked = list(np.argsort(distances, kind="stable"))
        if art_matrix is not None:
            art_distances = np.count_nonzero(art_matrix != scan_art, axis=1)
            art_order = [has_art[i] for i in np.argsort(art_distances, kind="stable")]
            art_lookup = {has_art[i]: int(art_distances[i]) for i in range(len(has_art))}
            merged, seen_index = [], set()
            for a, b in zip(ranked[:CANDIDATES * 4], art_order[:CANDIDATES * 4]):
                for i in (a, b):
                    if i not in seen_index:
                        seen_index.add(i)
                        merged.append(i)
            order = merged
        else:
            art_lookup = {}
            order = ranked[: CANDIDATES * 4]

        def score(i: int) -> int:
            """A card's distance on whichever signal found it best."""
            return min(int(distances[i]), art_lookup.get(i, 64))

        order = sorted(order, key=score)
        best = score(order[0]) if order else 64
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
        for i in order:
            distance = score(i)
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
                    note=reference.rarity or None,
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
        + _STYLE + _SCRIPT
    )
    return HTMLResponse(page("EzBay — Review queue", body))



def _printing_picker(selected: CardVariant = CardVariant.NORMAL) -> str:
    """Which printing this copy is.

    Foil is not reliably detectable from a flatbed scan -- it mostly reads
    as glare -- so it has to be stated. It matters: a reverse holo is a
    different holding at a different price, and everything accepted here
    was previously filed as a normal print regardless.
    """
    options = "".join(
        f"<option value='{v.value}'{' selected' if v is selected else ''}>"
        f"{v.value.replace('_', ' ').title()}</option>"
        for v in CardVariant
    )
    return (
        "<label class='printing'>Printing"
        f"<select name='variant'>{options}</select></label>"
    )


def _card_html(crop: Path, candidates, set_names: dict[str, str]) -> str:
    name = crop.name
    # Cache-busted by mtime: a recrop overwrites this same filename in
    # place (see review_recrop), and the image is served with a 5-minute
    # Cache-Control -- without this, reloading right after a recrop would
    # keep showing the browser's cached copy of the old crop.
    image_url = f"/review/image/{escape(name)}?t={int(crop.stat().st_mtime)}"
    thumb = f"<img src='{image_url}' alt='' loading='lazy' decoding='async'>"

    sheet_html = ""
    match = _CROP_NAME_RE.match(name)
    if match:
        stem, index = match.group(1), match.group(2)
        if _archived_sheet(stem) is not None:
            sheet_html = (
                "<div class='recrop'>"
                f"<div class='recrop-stage' data-stem='{escape(stem)}' data-crop='{escape(name)}'>"
                f"<img src='/feed/sheet/{escape(stem)}' alt='' loading='lazy' decoding='async'>"
                "<div class='recrop-box' hidden></div>"
                "</div>"
                f"<div class='recrop-row'><span class='recrop-hint'>Full scan &mdash; this crop is card "
                f"{escape(index)} on it. If it's wrong, drag a box around the real card here.</span>"
                "<button type='button' class='recrop-go' disabled>Recrop from selection</button></div>"
                "</div>"
            )

    if not candidates:
        options = (
            "<div class='no-match'>No confident match in any cached set.</div>"
            "<div class='hint'>Type the number <b>with its total</b> (e.g. 52/72) &mdash; "
            "the total is what identifies the set.</div>"
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
                f"<span class='pick-meta'>{escape(set_names.get(set_id, set_id))}"
                f"{' &middot; ' + escape(identification.note) if identification.note else ''} &middot; "
                f"d{identification.phash_distance} {pill(f'{confidence:.2f}', kind)}</span>"
                "</button>"
            )
        options = "<div class='picks'>" + "".join(rows) + "</div>"

    placeholder = "52/72 (with total)" if not candidates else "or type a no."
    return (
        "<div class='review-card'>"
        + f"<a class='review-face' href='{image_url}' target='_blank' rel='noopener'>{thumb}</a>"
        + sheet_html
        + "<form method='post' action='/review/decide' class='review-form'>"
        + f"<input type='hidden' name='crop' value='{escape(name)}'>"
        + f"<input type='hidden' name='set_id' value='{escape(default_set)}'>"
        + options
        + _printing_picker()
        + f"<div class='filename' title='{escape(name)}'>{escape(name)}</div>"
        + "<div class='manual'>"
        + f"<input name='number' placeholder='{placeholder}' autocomplete='off'>"
        + "<button name='action' value='accept'>Accept</button>"
        + "<button name='action' value='discard' class='ghost'>Discard</button>"
        + "</div></form></div>"
    )


@router.post("/decide")
def review_decide(
    crop: str = Form(...),
    action: str = Form(""),
    pick: str = Form(""),
    number: str = Form(""),
    set_id: str = Form(""),
    variant: str = Form(""),
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
    try:
        printing = CardVariant(variant) if variant else CardVariant.NORMAL
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown printing: {variant!r}")

    scan_session = start_or_resume(session, set_id, printing, _WebPrompter(), user=user, unattended=True)
    identification = Identification(
        card_id=match.card_id, number=match.number, name=match.name,
        source=Source.PHASH, confidence=1.0, note="confirmed in web review",
    )
    try:
        commit_card(
            session, identification, path, Path(settings.scan_media_dir),
            variant=printing, condition="Near Mint",
            scan_session=scan_session, user=user, allow_unconfirmed=True,
        )
    except CommitRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path.unlink(missing_ok=True)
    return RedirectResponse("/review", status_code=303)


# Below this, a selection smaller than this fraction of the sheet in either
# dimension is almost certainly a stray tap/click rather than an intended
# card-sized box -- refusing it outright beats silently producing a sliver
# crop nothing can identify.
MIN_RECROP_FRACTION = 0.02


@router.post("/recrop")
def review_recrop(
    crop: str = Form(...),
    sheet: str = Form(...),
    x0: float = Form(...),
    y0: float = Form(...),
    x1: float = Form(...),
    y1: float = Form(...),
    user: str = Depends(require_dashboard_user),
):
    """Replace a queued crop with a fresh one cut from the archived sheet at
    an operator-drawn box, for the case the automatic detector split the
    grid wrong (a truncated or otherwise misshapen crop). The box arrives as
    fractions of the sheet (0..1 on each axis), not pixels, since that's
    resolution-independent -- the thumbnail shown in the browser and the
    full-resolution archived sheet are different sizes.

    Overwrites the crop file in place rather than creating a new one: the
    filename is what the accept/discard form already references, and the
    original auto-detected crop it replaces has no value once it's been
    manually corrected (mirrors `_store_scan_image`'s overwrite-on-rescan
    behaviour in commit.py).
    """
    path = _safe_crop(crop)
    if not re.fullmatch(r"sheet-\d{8}-\d{6}-\d+", sheet):
        raise HTTPException(status_code=404, detail="unknown sheet")
    sheet_path = _archived_sheet(sheet)
    if sheet_path is None:
        raise HTTPException(status_code=404, detail="sheet not archived")

    lo_x, hi_x = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    lo_y, hi_y = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if hi_x - lo_x < MIN_RECROP_FRACTION or hi_y - lo_y < MIN_RECROP_FRACTION:
        raise HTTPException(status_code=400, detail="selection too small -- drag a box around the whole card")

    with Image.open(sheet_path) as sheet_image:
        width, height = sheet_image.size
        box = (int(lo_x * width), int(lo_y * height), int(hi_x * width), int(hi_y * height))
        sheet_image.convert("RGB").crop(box).save(path, quality=92)

    # Unlike accept/discard, this card doesn't leave the queue -- it's still
    # here, just with new content -- so land back on its own page (PAGE_SIZE
    # is 1, so that's its index in the sorted queue) instead of resetting to
    # page 1, which would show the reviewer an unrelated card instead of the
    # crop they just fixed.
    queued = _queued()
    page_number = queued.index(path) + 1 if path in queued else 1
    return RedirectResponse(f"/review?page={page_number}", status_code=303)


@router.get("/image/{name}")
def review_image(name: str, user: str = Depends(require_dashboard_user)):
    return FileResponse(_safe_crop(name), headers={"Cache-Control": "private, max-age=300"})


_STYLE = """<style>
.toolbar{display:flex;gap:10px;align-items:center;margin:0 0 18px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;padding:10px 16px;border-radius:999px;font-size:14px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:var(--surface-sunken)}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.muted{color:var(--text-dim);font-size:13px}

.review-grid{display:block;max-width:560px;margin:0 auto}
.review-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:16px;
  padding:16px;display:flex;flex-direction:column;gap:8px;min-width:0}
.review-card>*{min-width:0;max-width:100%}

.review-face{display:block;border-radius:12px;overflow:hidden;background:var(--surface-sunken);
  aspect-ratio:5/7;max-height:62vh;margin:0 auto}
.review-face img{width:100%;height:100%;object-fit:contain;display:block}

.suggest{font-size:15px;margin-top:6px}
.no-match{font-size:14px;color:var(--status-warn,#d4a527);margin-top:6px}
.hint{font-size:13px;color:var(--text-dim);line-height:1.45}
.filename{font-size:11px;color:var(--text-dim);opacity:.65;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;min-width:0}

.recrop{display:flex;flex-direction:column;gap:8px;border:1px solid var(--panel-border);
  border-radius:12px;padding:8px;background:var(--surface-sunken)}
.recrop-stage{position:relative;touch-action:none;user-select:none;-webkit-user-select:none}
.recrop-stage img{width:100%;border-radius:8px;display:block;pointer-events:none}
.recrop-box{position:absolute;border:2px solid var(--accent);
  background:color-mix(in srgb, var(--accent) 18%, transparent);pointer-events:none}
.recrop-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.recrop-hint{font-size:12px;color:var(--text-dim);flex:1 1 160px}
.recrop-go{flex:0 0 auto;padding:9px 14px;border-radius:9px;font-size:13px;cursor:pointer;
  border:1px solid var(--panel-border);background:var(--panel);color:var(--text);font-weight:600}
.recrop-go:not(:disabled):hover{border-color:var(--accent);color:var(--accent)}
.recrop-go:disabled{opacity:.45;cursor:default}

.picks{display:flex;flex-direction:column;gap:7px;margin-top:8px}
.pick{display:flex;flex-direction:column;align-items:flex-start;gap:3px;text-align:left;
  width:100%;min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);
  border-radius:11px;padding:11px 13px;cursor:pointer;color:var(--text)}
.pick:hover{border-color:var(--accent)}
.pick-name{font-size:15px;font-weight:650;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;max-width:100%}
.pick-meta{font-size:12.5px;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;max-width:100%}

.review-form{display:flex;gap:8px;margin-top:6px;flex-direction:column;max-width:none}
/* wraps rather than overflowing: on a narrow phone the input and both
   buttons don't fit on one line, and overflow hid the Discard button. */
.printing{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12px;
  color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em}
.printing select{flex:1 1 auto;min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);
  border-radius:10px;padding:10px;color:var(--text);font-size:14px;text-transform:none;letter-spacing:normal}
.manual{display:flex;gap:8px;margin-top:2px;flex-wrap:wrap;min-width:0}
.manual input{flex:1 1 140px;min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);
  border-radius:10px;padding:12px;color:var(--text);font-size:15px}
.manual button{flex:0 0 auto;padding:12px 18px;border-radius:10px;font-size:15px;cursor:pointer;
  border:none;background:linear-gradient(120deg,var(--accent),var(--accent-2));
  color:#fff;font-weight:700}
.manual button.ghost{background:none;border:1px solid var(--panel-border);
  color:var(--text-dim);font-weight:600}
.manual button.ghost:hover{border-color:var(--danger);color:var(--danger)}
</style>"""

# Drag-to-select on the full sheet thumbnail, then post the selection (as
# fractions of the image, not pixels) to /review/recrop. Fractions are
# resolution-independent, so no client-side scaling math is needed even
# though the displayed thumbnail and the archived sheet it's cropped from
# are different sizes -- the server just multiplies by the real sheet's
# width/height.
_SCRIPT = """<script>
(function () {
  document.querySelectorAll('.recrop-stage').forEach(function (stage) {
    var box = stage.querySelector('.recrop-box');
    var row = stage.parentElement.querySelector('.recrop-row');
    var goBtn = row.querySelector('.recrop-go');
    var dragging = false, frac = null;

    function pos(evt) {
      var rect = stage.getBoundingClientRect();
      var x = (evt.clientX - rect.left) / rect.width;
      var y = (evt.clientY - rect.top) / rect.height;
      return [Math.min(1, Math.max(0, x)), Math.min(1, Math.max(0, y))];
    }

    function draw(x0, y0, x1, y1) {
      var rect = stage.getBoundingClientRect();
      box.style.left = (Math.min(x0, x1) * rect.width) + 'px';
      box.style.top = (Math.min(y0, y1) * rect.height) + 'px';
      box.style.width = (Math.abs(x1 - x0) * rect.width) + 'px';
      box.style.height = (Math.abs(y1 - y0) * rect.height) + 'px';
      box.hidden = false;
    }

    stage.addEventListener('pointerdown', function (evt) {
      dragging = true;
      goBtn.disabled = true;
      var p = pos(evt);
      frac = [p[0], p[1], p[0], p[1]];
      stage.setPointerCapture(evt.pointerId);
      evt.preventDefault();
    });
    stage.addEventListener('pointermove', function (evt) {
      if (!dragging) return;
      var p = pos(evt);
      frac[2] = p[0]; frac[3] = p[1];
      draw(frac[0], frac[1], frac[2], frac[3]);
    });
    stage.addEventListener('pointerup', function () {
      if (!dragging) return;
      dragging = false;
      var big = Math.abs(frac[2] - frac[0]) > 0.03 && Math.abs(frac[3] - frac[1]) > 0.03;
      goBtn.disabled = !big;
    });

    goBtn.addEventListener('click', function () {
      if (!frac) return;
      var form = document.createElement('form');
      form.method = 'post';
      form.action = '/review/recrop';
      var fields = {
        crop: stage.dataset.crop, sheet: stage.dataset.stem,
        x0: Math.min(frac[0], frac[2]), y0: Math.min(frac[1], frac[3]),
        x1: Math.max(frac[0], frac[2]), y1: Math.max(frac[1], frac[3])
      };
      Object.keys(fields).forEach(function (key) {
        var input = document.createElement('input');
        input.type = 'hidden'; input.name = key; input.value = fields[key];
        form.appendChild(input);
      });
      document.body.appendChild(form);
      form.submit();
    });
  });
})();
</script>"""
