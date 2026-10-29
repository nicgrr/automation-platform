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

import json
import re
import tempfile
import uuid
from difflib import SequenceMatcher
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote_plus

import imagehash
import numpy as np
import pytesseract
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .auth import require_dashboard_user
from .card_recognition import (
    ExtractedCard,
    ExtractedCardDetailsScreenshot,
    RecognitionError,
    extract_card_details,
    extract_card_details_screenshot,
)
from .config import get_settings
from .database import get_session
from .models import (
    CardPrice,
    CardSet,
    CardVariant,
    CatalogCard,
    CatalogItem,
    ScanSession,
    ScanSessionStatus,
    TcgCard,
)
from .personal_collection import _best_prices
from .rarity import normalize_star
from .reverse_image_search import ReverseImageSearchError, search_image
from .scan_feed import _archived_sheet
from .scan_ingest import catalog
from .scan_ingest.commit import CommitRefused, commit_card
from .scan_ingest.foil import catalogue_variant
from .scan_ingest.games import GAME_LABELS
from .scan_ingest.identify import (
    Identification, Source, _phash_confidence, art_phash,
    detect_card_code, detect_card_game, detect_card_language, reference_language,
    reference_matches_game,
)
from .scan_ingest.session import _load_all_catalogs, start_or_resume
from .ui import brand_header, page, pill

# A queued crop is named "{sheet stem}-card{N}-{label}.jpg" (see
# session._set_aside_for_review) -- recovering the stem and position from it
# is what lets the review page show the whole sheet next to the single crop,
# so a genuinely ambiguous or truncated card can be placed by eye against its
# neighbours instead of judged from an isolated square.
_CROP_NAME_RE = re.compile(r"^(sheet-\d{8}-\d{6}-\d+)-card(\d+)-")

router = APIRouter(prefix="/review", tags=["review"])

# A batch at a time, in a responsive grid (see .review-grid): one column on
# a narrow phone -- still full-width, still the single-card-per-screen
# layout that mattered when this was fixed at PAGE_SIZE=1 -- expanding to
# several side by side on anything wider, which is what a sheet's worth of
# cards reviewed at a desk actually wants (2026-09-22, sketched by hand: a
# row of numbered cards with their own fields and a Confirm each, not one
# card taking the whole screen on a laptop). 12 is roughly a sheet and a
# half, enough to see a scan batch's shape without the page getting heavy.
PAGE_SIZE = 12

# How many possible cards to offer per crop. Enough that the right one is
# almost always present when the top match is wrong, few enough to scan.
CANDIDATES = 3

# Phone screenshots are generally under 2 MB. Eight leaves ample room for a
# high-resolution PNG without letting an accidental video/document sit in a
# web worker's memory while the recognition request runs.
MAX_DETAILS_SCREENSHOT_BYTES = 8 * 1024 * 1024


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
    catalog_languages = {reference_language(reference) for _, reference in entries} - {None}
    by_printed_code: dict[str, list[int]] = {}
    for index, (_, reference) in enumerate(entries):
        number = (reference.number or "").upper().replace(" ", "")
        if re.fullmatch(r"[A-Z0-9]+-\d{2,3}", number):
            by_printed_code.setdefault(number, []).append(index)
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

        detected_game = detect_card_game(crop)
        if detected_game:
            matching_game = [i for i in order if reference_matches_game(entries[i][1], detected_game)]
            if matching_game:
                order = matching_game

        detected_language = detect_card_language(crop) if len(catalog_languages) > 1 else None
        if detected_language:
            matching = [
                i for i in order
                if reference_language(entries[i][1]) == detected_language
            ]
            if matching and any(
                reference_language(entries[i][1]) not in {None, detected_language}
                for i in order
            ):
                order = matching

        printed_code = detect_card_code(crop)
        exact_code_indices = list(by_printed_code.get(printed_code or "", []))
        if detected_game:
            exact_code_indices = [
                i for i in exact_code_indices
                if reference_matches_game(entries[i][1], detected_game)
            ]
        if detected_language:
            same_language_codes = [
                i for i in exact_code_indices
                if reference_language(entries[i][1]) == detected_language
            ]
            if same_language_codes:
                exact_code_indices = same_language_codes
        exact_code_set = set(exact_code_indices)
        if exact_code_indices:
            exact_code_indices.sort(key=lambda i: (score(i), int(distances[i])))
            order = exact_code_indices + [i for i in order if i not in exact_code_set]

        # Whole-card distance breaks equal art-window scores. That matters
        # for same-art language printings even when OCR was inconclusive.
        order = sorted(
            order,
            key=lambda i: (0 if i in exact_code_set else 1, score(i), int(distances[i])),
        )
        best = score(order[0]) if order else 64
        if best > settings.scan_phash_max_distance and not exact_code_indices:
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
            if distance > settings.scan_phash_max_distance and i not in exact_code_set:
                break
            set_id, reference = entries[i]
            if reference.card_id in seen:
                continue
            seen.add(reference.card_id)
            # _phash_confidence expects the winning card's lead over its
            # nearest rival, not this candidate's distance behind the
            # winner.  The old subtraction made an exact best match look
            # ambiguous (margin 0) while giving weaker runner-ups a larger,
            # backwards confidence bonus.
            rival_distances = [
                score(other)
                for other in order
                if entries[other][1].card_id != reference.card_id
            ]
            margin = (min(rival_distances) - distance) if rival_distances else 6
            candidates.append((
                set_id,
                Identification(
                    card_id=reference.card_id, number=reference.number, name=reference.name,
                    source=Source.OCR if i in exact_code_set else Source.PHASH,
                    confidence=(0.9 if i in exact_code_set else _phash_confidence(
                        distance, margin, settings.scan_phash_max_distance
                    )),
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


def _crop_candidate(name: str) -> Path:
    """Resolve a crop name without allowing it to escape the review dir."""
    directory = _review_dir().resolve()
    candidate = (directory / name).resolve()
    if candidate.parent != directory:
        raise HTTPException(status_code=404, detail="not in the review queue")
    return candidate


def _safe_crop(name: str) -> Path:
    """Resolve an existing queued crop for routes that need its contents."""
    candidate = _crop_candidate(name)
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="not in the review queue")
    return candidate


@router.get("", response_class=HTMLResponse)
def review_page(
    request: Request,
    page_number: int = Query(1, alias="page", ge=1),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    stale_notice = (
        "<div class='panel'><p>That crop was already processed in another tab. "
        "The queue below is up to date.</p></div>"
        if request.query_params.get("notice") == "already-processed" else ""
    )
    crops = _queued()
    total = len(crops)
    if not total:
        body = (
            brand_header("Review queue")
            + stale_notice
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
    sets = catalog.cached_sets(session)
    names = {s.id: s.name for s in sets}

    suggested = _suggestions(window, catalogs, totals, settings) if catalogs else {}

    # One batched query for every candidate shown on the page, rather than
    # a CardPrice lookup per card -- same reasoning _suggestions' own
    # docstring gives for vectorising the phash match itself (11s for
    # twelve crops, one card at a time, was the thing that made this a
    # page instead of a form).
    candidate_card_ids = [
        identification.card_id
        for candidates in suggested.values() if candidates
        for _, identification in candidates
        if identification.card_id
    ]
    prices = _best_prices(session, candidate_card_ids)
    ebay_stats = _ebay_listing_stats(session, candidate_card_ids)
    languages = dict(
        session.execute(
            select(TcgCard.catalog_item_id, TcgCard.language)
            .where(TcgCard.catalog_item_id.in_(candidate_card_ids))
        ).all()
    ) if candidate_card_ids else {}
    candidate_cards = {
        card.id: card for card in session.scalars(
            select(CatalogCard).where(CatalogCard.id.in_(candidate_card_ids))
        )
    } if candidate_card_ids else {}
    variants = {
        card_id: known.value
        for card_id, card in candidate_cards.items()
        if (known := catalogue_variant(card)[0]) is not None
    }

    def _form_context_for(crop: Path, candidates) -> tuple[str, str]:
        # Priority: (1) this crop's own top guess -- strongest signal when
        # it exists; (2) the set its own sheet actually matched, from the
        # log -- correct regardless of when that sheet was scanned, unlike
        # (3); (3) whatever's most recently been picked up for scanning, a
        # last resort for a crop whose sheet never resolved a set at all.
        set_id = candidates[0][0] if candidates else None
        if set_id is None:
            match = _CROP_NAME_RE.match(crop.name)
            if match:
                set_id = _sheet_set_id(match.group(1))
        if set_id is None:
            set_id = _likely_set_id(session)
        return set_id or "", _number_options_json(set_id, catalogs, totals) if set_id else ""

    cards = []
    for position, crop in enumerate(window, start=1):
        candidates = suggested.get(crop)
        seeded_set, number_options = _form_context_for(crop, candidates)
        cards.append(
            _card_html(
                crop, candidates, names, number_options, position, prices,
                seeded_set=seeded_set, languages=languages, variants=variants,
                ebay_stats=ebay_stats, picker_sets=sets,
            )
        )

    nav = []
    if page_number > 1:
        nav.append(f"<a class='chip' href='/review?page={page_number - 1}'>&larr; Previous</a>")
    nav.append(f"<span class='muted'>Page {page_number} of {pages}</span>")
    if page_number < pages:
        nav.append(f"<a class='chip' href='/review?page={page_number + 1}'>Next &rarr;</a>")

    body = (
        brand_header("Review queue")
        + stale_notice
        + "<div class='stat-grid'>"
        + f"<div class='stat-card'><div class='label'>Awaiting review</div><div class='value'>{total}</div></div>"
        + f"<div class='stat-card'><div class='label'>Cached sets to match against</div><div class='value'>{len(catalogs)}</div></div>"
        + "</div>"
        + f"<div class='toolbar'>{''.join(nav)}</div>"
        + _BULK_TOOLBAR
        + f"<div class='review-grid'>{''.join(cards)}</div>"
        + _STYLE + _SCRIPT + _BULK_SCRIPT
    )
    return HTMLResponse(page("EzBay — Review queue", body))



_SHEET_MATCHED_RE = re.compile(r"^Sheet (sheet-[\d-]+)\.png: matched .+? \(([\w\d]+)\), rotation")


def _ebay_listing_stats(session: Session, card_ids: list[str]) -> dict[str, tuple[Decimal, Decimal | None]]:
    """Latest eBay active-listing (low, median) per card, from the price import."""
    if not card_ids:
        return {}
    rows = session.execute(
        select(CardPrice.catalog_item_id, CardPrice.source, CardPrice.price)
        .where(
            CardPrice.catalog_item_id.in_(card_ids),
            CardPrice.source.in_(("ebay_active_low", "ebay_active_median")),
            CardPrice.price > 0,
        )
        .order_by(CardPrice.fetched_at.desc())
    ).all()
    latest: dict[str, dict[str, Decimal]] = {}
    for card_id, source, price in rows:
        latest.setdefault(card_id, {}).setdefault(source, Decimal(str(price)))
    return {
        card_id: (found["ebay_active_low"], found.get("ebay_active_median"))
        for card_id, found in latest.items() if "ebay_active_low" in found
    }


def _sheet_set_id(stem: str) -> str | None:
    """Which cached set this exact sheet matched against, straight from the
    log -- the log line only exists when a sheet resolved a set at all, so
    this only helps a crop whose sheet-mates *did* identify (or whose sheet
    resolved via the OCR-bootstrap path) while this one card didn't. Reads
    the whole file rather than the tail used elsewhere in this app: an old
    backlog sheet's "matched" line can be well outside a byte-bounded tail
    once today's session has logged thousands of lines since.
    """
    log_path = Path(get_settings().scan_log_file)
    if not log_path.exists():
        return None
    found = None
    with log_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            match = _SHEET_MATCHED_RE.match(line)
            if match and match.group(1) == stem:
                found = match.group(2)
    return found


def _likely_set_id(session: Session) -> str | None:
    """Best guess at which set is currently being scanned, for seeding the
    number field's autocomplete when a crop has no candidates of its own to
    go on. A `ScanSession` stays RUNNING indefinitely (see its docstring --
    a pile spread over days keeps one running count), so several can be
    RUNNING at once; the most recently *started* one is whichever set was
    picked up most recently, which is what a reviewer working the queue
    live is almost always still scanning.
    """
    return session.scalar(
        select(ScanSession.set_id).where(ScanSession.status == ScanSessionStatus.RUNNING)
        .order_by(ScanSession.started_at.desc()).limit(1)
    )


def _number_options_json(set_id: str, catalogs: dict, totals: dict) -> str:
    """One set's cards as a JSON array for the number field's custom
    autocomplete dropdown (see _SCRIPT) -- not a native <datalist>, because
    iOS Safari doesn't render one for text inputs at all (a long-standing
    WebKit gap; Chrome and Firefox are fine), which is the whole reason this
    is hand-rolled instead of a couple of <option> tags.
    """
    references = catalogs.get(set_id)
    total = totals.get(set_id)
    if not references or not total:
        return ""
    entries = [
        {
            "id": r.card_id,
            "v": f"{r.number}/{total}",
            "number": r.number,
            "n": r.name,
            "rarity": r.rarity or "",
        }
        for r in sorted(references, key=lambda r: (r.name, r.number, r.card_id))
    ]
    # </script> inside a name would otherwise close the tag early.
    payload = json.dumps(entries).replace("</", "<\\/")
    return f"<script type='application/json' class='number-options'>{payload}</script>"


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


_CONDITIONS = ("Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged")


def _condition_picker(selected: str = "Near Mint") -> str:
    options = "".join(
        f"<option value='{escape(value)}'{' selected' if value == selected else ''}>{escape(value)}</option>"
        for value in _CONDITIONS
    )
    return (
        "<label class='printing'>Condition"
        f"<select name='condition'>{options}</select></label>"
    )


def _set_picker(selected: str, set_names: dict[str, str], sets=None) -> str:
    """Set dropdown, grouped by game, each option led by its printed set
    code ("FB11 · Brightness of Hope") -- 800+ sets in one name-sorted list
    was unsearchable by the code that's actually printed on the card, and a
    native <select> type-ahead only matches the start of an option's text."""
    options = ["<option value=''>Choose a set&hellip;</option>"]
    if sets is None:
        for set_id, set_name in sorted(set_names.items(), key=lambda item: (item[1].lower(), item[0])):
            chosen = " selected" if set_id == selected else ""
            options.append(
                f"<option value='{escape(set_id)}'{chosen}>{escape(set_name)} ({escape(set_id)})</option>"
            )
        return "<select name='set_id' required>" + "".join(options) + "</select>"

    groups: dict[str, list] = {}
    for card_set in sets:
        groups.setdefault(GAME_LABELS.get(card_set.game or "pokemon", card_set.game or "Other"), []).append(card_set)
    for label in sorted(groups, key=str.lower):
        options.append(f"<optgroup label='{escape(label)}'>")
        for card_set in sorted(groups[label], key=lambda c: ((c.code or c.name).lower(), c.name.lower())):
            chosen = " selected" if card_set.id == selected else ""
            text = f"{card_set.code} · {card_set.name}" if card_set.code else card_set.name
            options.append(f"<option value='{escape(card_set.id)}'{chosen}>{escape(text)}</option>")
        options.append("</optgroup>")
    return "<select name='set_id' required>" + "".join(options) + "</select>"


def _card_html(
    crop: Path,
    candidates,
    set_names: dict[str, str],
    datalist_html: str = "",
    position: int = 0,
    prices: dict[str, Decimal] | None = None,
    *,
    seeded_set: str = "",
    languages: dict[str, str] | None = None,
    variants: dict[str, str] | None = None,
    ebay_stats: dict[str, tuple[Decimal, Decimal | None]] | None = None,
    picker_sets=None,
) -> str:
    ebay_stats = ebay_stats or {}
    prices = prices or {}
    languages = languages or {}
    variants = variants or {}
    name = crop.name
    position_html = f"<div class='review-position'>#{position}</div>" if position else ""
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

    def _pick_button(set_id: str, identification, *, primary: bool) -> str:
        confidence = identification.confidence
        kind = "ok" if confidence >= 0.75 else "warn"
        label = "Confirm" if primary else None
        card_id = identification.card_id or ""
        price = prices.get(card_id)
        attrs = (
            f"data-card-id='{escape(card_id)}' data-set-id='{escape(set_id)}' "
            f"data-number='{escape(identification.number or '')}' "
            f"data-name='{escape(identification.name or '')}' "
            f"data-rarity='{escape(identification.note or '')}' "
            f"data-language='{escape(languages.get(card_id, 'English'))}' "
            f"data-variant='{escape(variants.get(card_id, ''))}' "
            f"data-price='{escape(str(price) if price is not None else '')}'"
        )
        if primary:
            opening = (
                f"<button type='submit' class='pick pick-primary' name='pick' "
                f"value='{escape(set_id)}|{escape(card_id)}' {attrs}>"
            )
        else:
            opening = f"<button type='button' class='pick candidate-choice' {attrs}>"
        return (
            opening
            + (
                f"<span class='pick-name'>#{escape(identification.number or '')} {escape(identification.name or '')}</span>"
                f"<span class='pick-meta'>{escape(set_names.get(set_id, set_id))}"
                f"{' &middot; ' + escape(identification.note) if identification.note else ''} &middot; "
                f"d{identification.phash_distance} {pill(f'{confidence:.2f}', kind)}</span>"
                if not primary else ""
            )
            + (label or "")
            + "</button>"
        )

    if not candidates:
        candidate_html = (
            "<div class='no-match'>No confident match in any cached set.</div>"
            "<div class='hint'>Choose the set, then search by card name or number. "
            "You can also type the printed number with its total (for example 52/72).</div>"
        )
        default_set = seeded_set
        top_card_id = top_number = top_name = top_rarity = cost_value = ""
        top_language = "English"
    else:
        default_set, top = candidates[0]
        price = prices.get(top.card_id)
        top_card_id = top.card_id or ""
        top_number = top.number or ""
        top_name = top.name or ""
        top_rarity = top.note or ""
        top_language = languages.get(top_card_id, "English")
        cost_value = str(price) if price is not None else ""
        rest_html = ""
        if len(candidates) > 1:
            rows = [_pick_button(set_id, identification, primary=False) for set_id, identification in candidates[1:]]
            rest_html = (
                "<details class='review-alts'><summary>Not this one? "
                f"{len(rows)} other match{'es' if len(rows) != 1 else ''}</summary>"
                f"<div class='picks'>{''.join(rows)}</div></details>"
            )
        candidate_html = _pick_button(default_set, top, primary=True) + rest_html

    placeholder = "Search name or number"
    details = (
        "<div class='review-details'>"
        "<label class='wide'>Set"
        + _set_picker(default_set, set_names, picker_sets)
        + "</label>"
        f"<label class='wide'>Name<input name='name' value='{escape(top_name)}' "
          "placeholder='Card name' autocomplete='off'></label>"
        "<label>Number<div class='autocomplete'>"
        f"<input name='number' value='{escape(top_number)}' placeholder='{placeholder}' "
          "autocomplete='off' spellcheck='false'>"
        "<div class='autocomplete-list' hidden></div>"
        + datalist_html
        + "</div></label>"
        f"<label>Rarity<input name='rarity' value='{escape(top_rarity)}' "
          "placeholder='Unknown' autocomplete='off'></label>"
        f"<label>Language<input name='language' value='{escape(top_language)}' autocomplete='off'></label>"
        "<label>Price (AUD)"
        f"<input name='price' type='number' step='0.01' min='0.01' value='{escape(cost_value)}' "
          "placeholder='No price on file'></label>"
        "</div>"
    )
    screenshot_import = (
        "<div class='details-shot'>"
        "<div class='details-shot-title'>Add a details screenshot</div>"
        "<div class='details-shot-help'>Use a PriceCharting or similar product page. EzBay reads it locally "
        "first; only if that cannot identify the card is it sent to the configured recognition service. "
        "The card name, set, number, rarity and <b>ungraded</b> price are pre-filled, then the screenshot is discarded.</div>"
        "<button class='detect-crop-read' type='button'>Detect current card &mdash; local/free first</button>"
        "<button class='claude-suggest-read' type='button'>Ask Claude for a second opinion</button>"
        "<input class='details-shot-file' type='file' accept='image/jpeg,image/png,image/webp'>"
        "<button class='details-shot-read' type='button'>Read &amp; pre-fill</button>"
        "<div class='details-shot-status' role='status' aria-live='polite'></div>"
        "</div>"
    )
    research_query = " ".join(value for value in (
        top_name, top_number, set_names.get(default_set, ""), top_language,
    ) if value)
    ebay_low_median = ebay_stats.get(top_card_id) if candidates else None
    ebay_line = ""
    if ebay_low_median:
        low, median = ebay_low_median
        median_text = f" &middot; median <b>${median:,.2f}</b>" if median is not None else ""
        ebay_line = (
            f"<div class='ebay-active'>eBay AU active listings: low <b>${low:,.2f}</b>{median_text} "
            "<span class='muted'>(asking prices, not sold; the median can include parallels)</span></div>"
        )
    price_research = (
        "<div class='price-research'><div class='details-shot-title'>Check sold-market value</div>"
        + ebay_line
        + "<div class='details-shot-help'>PriceCharting is the primary estimate because it aggregates completed "
        "sales. Verify scarce or high-value cards against eBay sold results, then use the screenshot reader above "
        "to import the exact ungraded AUD value.</div>"
        f"<a class='pricecharting-link chip' target='_blank' rel='noopener' "
        f"href='https://www.pricecharting.com/search-products?q={quote_plus(research_query)}&amp;type=prices'>PriceCharting estimate</a> "
        f"<a class='ebay-sold-link chip' target='_blank' rel='noopener' "
        f"href='https://www.ebay.com.au/sch/i.html?_nkw={quote_plus(research_query)}&amp;LH_Sold=1&amp;LH_Complete=1'>eBay sold results</a>"
        "</div>"
    )
    title_import = (
        "<div class='title-import'>"
        "<div class='details-shot-title'>Paste a listing title</div>"
        "<div class='details-shot-help'>Example: Son Goku SH1-29 2016 Super Dragon Ball Heroes Card Holo Japanese Rare BANDAI. "
        "EzBay uses the printed card code to select the exact cached card.</div>"
        "<input class='title-import-input' type='text' placeholder='Paste the full title here' autocomplete='off'>"
        "<button class='title-import-go' type='button'>Auto-fill</button>"
        "<div class='title-import-status details-shot-status' role='status' aria-live='polite'></div>"
        "</div>"
    )
    return (
        "<div class='review-card'>"
        + position_html
        + "<label class='bulk-pick'><input type='checkbox' class='bulk-check'> Select</label>"
        + f"<a class='review-face' href='{image_url}' target='_blank' rel='noopener'>{thumb}</a>"
        + sheet_html
        + "<form method='post' action='/review/decide' class='review-form'>"
        + f"<input type='hidden' name='crop' value='{escape(name)}'>"
        + f"<input type='hidden' name='selected_card_id' value='{escape(top_card_id)}'>"
        + f"<input type='hidden' name='details_card_id' value='{escape(top_card_id)}'>"
        + "<input type='hidden' name='dirty_fields' value=''>"
        + "<input type='hidden' name='review_form_version' value='2'>"
        + title_import
        + screenshot_import
        + price_research
        + details
        + candidate_html
        + _printing_picker(CardVariant(variants[top_card_id]) if top_card_id in variants else CardVariant.NORMAL)
        + _condition_picker()
        + f"<div class='filename' title='{escape(name)}'>{escape(name)}</div>"
        + "<div class='manual'>"
        + "<button name='action' value='accept'>Save &amp; add</button>"
        + "<button name='action' value='discard' class='ghost' formnovalidate>Discard</button>"
        + "</div></form></div>"
    )


def _codes_from_listing_title(title: str) -> list[str]:
    """Card-like identifiers in a marketplace title, longest first."""
    found = re.findall(r"(?<![A-Z0-9])([A-Z]{1,10}\d{0,3}(?:-[A-Z0-9]+)+)(?![A-Z0-9])", title.upper())
    return sorted(dict.fromkeys(found), key=len, reverse=True)


@router.post("/parse-listing-title")
def review_parse_listing_title(
    title: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Paste a listing title first.")
    codes = _codes_from_listing_title(title)
    if not codes:
        raise HTTPException(status_code=422, detail="No card code was found. Include a code such as SH1-29.")

    matches = []
    for code in codes:
        rows = session.scalars(select(CatalogCard).where(func.upper(CatalogCard.number) == code)).all()
        if rows:
            matches = rows
            break
    if not matches:
        raise HTTPException(status_code=404, detail=f"{codes[0]} is not in a cached set yet.")

    normalized_title = _match_text(title)
    ranked = sorted(
        matches,
        key=lambda card: (
            _ocr_phrase_score(card.name, title, normalized_ocr=normalized_title)[0],
            bool(card.rarity and _match_text(card.rarity) in normalized_title),
        ),
        reverse=True,
    )
    card = ranked[0]
    tcg = session.get(TcgCard, card.id)
    card_set = session.get(CardSet, card.set_id)
    # Marketplace titles conventionally put the human name immediately
    # before the card code. This is particularly useful for the Japanese
    # caches whose upstream rows may still have a generic code-only title.
    code_at = title.upper().find(card.number.upper())
    pasted_name = title[:code_at].strip(" -–—:|") if code_at > 0 else ""
    boilerplate = {"bandai", "dragonball", "dragon", "heroes", "card", "japan", "japanese"}
    pasted_tokens = set(_match_text(pasted_name).split())
    usable_pasted_name = pasted_name and len(pasted_name) <= 256 and not (pasted_tokens & boilerplate)
    display_name = pasted_name if usable_pasted_name else card.name
    apply_fields = ["name"] if display_name != card.name else []
    return {
        "matched": True, "catalog_item_id": card.id, "set_id": card.set_id,
        "set_name": card_set.name if card_set else card.set_id,
        "number": card.number, "name": display_name, "rarity": card.rarity or "",
        "language": tcg.language if tcg else ("Japanese" if "japanese" in title.casefold() else "English"),
        "variant": (known.value if (known := catalogue_variant(card)[0]) else ""),
        "price": "", "apply_fields": apply_fields, "warnings": [], "source_site": "pasted listing title",
    }


@router.post("/decide")
def review_decide(
    crop: str = Form(...),
    action: str = Form(""),
    pick: str = Form(""),
    number: str = Form(""),
    set_id: str = Form(""),
    selected_card_id: str = Form(""),
    details_card_id: str = Form(""),
    dirty_fields: str = Form(""),
    review_form_version: str = Form(""),
    name: str = Form(""),
    variant: str = Form(""),
    condition: str = Form("Near Mint"),
    rarity: str = Form(""),
    language: str = Form(""),
    price: str = Form(""),
    cost: str = Form(""),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    # A reviewer can have the same queue open in two tabs.  Treat a decision
    # submitted after the other tab removed the crop as an idempotent success,
    # while still rejecting path traversal through `_crop_candidate`.
    path = _crop_candidate(crop)
    if not path.is_file():
        return RedirectResponse("/review?notice=already-processed", status_code=303)

    if action == "discard":
        path.unlink(missing_ok=True)
        return RedirectResponse("/review", status_code=303)

    # New pick buttons carry the exact catalogue id. This matters for alt
    # arts/parallels that legitimately share a set and number. Keep the old
    # set:number token readable for open tabs and older clients.
    picked_card_id = ""
    if pick:
        if "|" in pick:
            set_id, _, picked_card_id = pick.partition("|")
        else:
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

    if not set_id:
        raise HTTPException(
            status_code=400,
            detail="choose a set, or type the number as it's printed (e.g. 166/236) so the set can be identified",
        )

    references = catalogs.get(set_id)
    if not references:
        raise HTTPException(status_code=400, detail=f"set {set_id!r} is not cached")

    match = None
    if picked_card_id:
        match = next((r for r in references if r.card_id == picked_card_id), None)
        if match is None:
            raise HTTPException(status_code=400, detail=f"card {picked_card_id!r} is not in set {set_id!r}")
        number = match.number
    elif number:
        # The hidden exact id is honoured only while it still agrees with
        # the visible set and number. Typing a correction therefore cannot
        # accidentally commit the old suggestion.
        if selected_card_id:
            match = next(
                (
                    r for r in references
                    if r.card_id == selected_card_id and r.number == number
                ),
                None,
            )
        if match is None:
            matches = [r for r in references if r.number == number]
            if len(matches) == 1:
                match = matches[0]
            elif len(matches) > 1:
                exact_name = name.strip().casefold()
                named = [r for r in matches if r.name.strip().casefold() == exact_name] if exact_name else []
                if len(named) == 1:
                    match = named[0]
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"more than one card uses #{number} in {set_id}; choose the exact name "
                            "from the suggestions so an alternate art is not mistaken for the base card"
                        ),
                    )
    if match is None:
        if not number:
            raise HTTPException(
                status_code=400,
                detail="a card number is required -- search by name/number or type it as printed (e.g. 166/236)",
            )
        raise HTTPException(
            status_code=400,
            detail=f"no card #{number} in {set_id} -- if that's the wrong set, type the number as printed (e.g. {number}/236)",
        )

    settings = get_settings()
    try:
        printing = CardVariant(variant) if variant else CardVariant.NORMAL
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown printing: {variant!r}")
    matched_card = session.get(CatalogCard, match.card_id)
    known_printing, _ = catalogue_variant(matched_card) if matched_card is not None else (None, None)
    if known_printing is not None:
        printing = known_printing

    condition = condition.strip() or "Near Mint"
    if condition not in _CONDITIONS:
        raise HTTPException(status_code=400, detail=f"unknown condition: {condition!r}")

    requested_fields = {field.strip() for field in dirty_fields.split(",") if field.strip()}
    allowed_fields = {"name", "rarity", "language", "price"}
    requested_fields &= allowed_fields
    effective_price = price.strip() or cost.strip()
    # Legacy/open-tab requests have no form identity or dirty-field list.
    # Preserve their rarity/cost editing behaviour. New forms only apply
    # untouched prefilled metadata when it still belongs to the exact card.
    if review_form_version != "2" and not details_card_id and not requested_fields:
        if name.strip():
            requested_fields.add("name")
        if rarity.strip():
            requested_fields.add("rarity")
        if language.strip():
            requested_fields.add("language")
        if effective_price:
            requested_fields.add("price")

    _apply_details_overrides(
        session,
        match.card_id,
        name=name,
        rarity=rarity,
        language=language,
        price=effective_price,
        fields=requested_fields,
    )

    scan_session = start_or_resume(session, set_id, printing, _WebPrompter(), user=user, unattended=True)
    identification = Identification(
        card_id=match.card_id, number=match.number, name=match.name,
        source=Source.PHASH, confidence=1.0, note="confirmed in web review",
    )
    try:
        commit_card(
            session, identification, path, Path(settings.scan_media_dir),
            variant=printing, condition=condition,
            scan_session=scan_session, user=user, allow_unconfirmed=True,
        )
    except CommitRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    path.unlink(missing_ok=True)
    return RedirectResponse("/review", status_code=303)


def _apply_details_overrides(
    session: Session,
    card_id: str,
    *,
    name: str = "",
    rarity: str = "",
    language: str = "",
    price: str = "",
    fields: set[str] | None = None,
) -> None:
    """Apply explicit review edits to the exact catalogue record.

    CatalogCard powers scan matching while CatalogItem/TcgCard power the
    CRM, listings, and valuation screens. Keeping all three in sync avoids
    the old failure where a correction looked right on review but the old
    name or rarity reappeared later in the pipeline.
    """
    fields = fields or set()
    card = session.get(CatalogCard, card_id)
    catalog_item = session.get(CatalogItem, card_id)
    tcg_card = session.get(TcgCard, card_id)

    if "name" in fields:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="card name cannot be blank")
        if len(cleaned_name) > 256:
            raise HTTPException(status_code=400, detail="card name is too long")
        if card is not None:
            card.name = cleaned_name
        if catalog_item is not None:
            catalog_item.name = cleaned_name

    if "rarity" in fields:
        cleaned_rarity = normalize_star(rarity) or None
        if card is not None:
            card.rarity = cleaned_rarity
        if tcg_card is not None:
            tcg_card.rarity = cleaned_rarity

    if "language" in fields:
        cleaned_language = language.strip()
        if not cleaned_language:
            raise HTTPException(status_code=400, detail="language cannot be blank")
        if len(cleaned_language) > 32:
            raise HTTPException(status_code=400, detail="language is too long")
        if tcg_card is not None:
            tcg_card.language = cleaned_language

    if "price" in fields and price.strip():
        try:
            new_price = Decimal(price.strip()).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise HTTPException(status_code=400, detail="price must be a valid AUD amount")
        if not new_price.is_finite() or new_price <= 0:
            raise HTTPException(status_code=400, detail="price must be greater than zero")
        current = session.scalars(
            select(CardPrice).where(CardPrice.catalog_item_id == card_id).order_by(CardPrice.fetched_at.desc())
        ).first()
        already_this_value = (
            current is not None and current.source == "manual"
            and Decimal(str(current.price)).quantize(Decimal("0.01")) == new_price
        )
        if not already_this_value:
            session.add(CardPrice(catalog_item_id=card_id, source="manual", price=new_price, currency="AUD"))


def _match_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _text_similarity(left: str, right: str) -> float:
    a, b = _match_text(left), _match_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Price guides often prefix the real set with the franchise/game name
    # ("Dragon Ball Super Vicious Rejuvenation"). A full local set name
    # contained within that is stronger evidence than the raw edit ratio.
    if min(len(a), len(b)) >= 5 and (a in b or b in a):
        return 0.96
    return SequenceMatcher(None, a, b).ratio()


def _screenshot_number(value: str) -> str:
    # Printed totals are useful on physical cards but a guide may render
    # either "58/102" or just "58". Match the catalogue's number part.
    raw = (value or "").split("/", 1)[0]
    return re.sub(r"\s+", "", raw).upper().lstrip("0") or re.sub(r"\s+", "", raw).upper()


def _best_screenshot_set(session: Session, set_name: str) -> CardSet | None:
    if not set_name.strip():
        return None
    sets = list(session.scalars(select(CardSet).where(CardSet.cached_at.is_not(None))))
    ranked = sorted(((_text_similarity(set_name, card_set.name), card_set) for card_set in sets),
                    key=lambda pair: pair[0], reverse=True)
    if not ranked or ranked[0][0] < 0.78:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
        return None
    return ranked[0][1]


def _match_screenshot_card(
    session: Session, details: ExtractedCardDetailsScreenshot,
) -> tuple[CatalogCard | None, CardSet | None]:
    rows = session.execute(
        select(CatalogCard, CardSet).join(CardSet, CardSet.id == CatalogCard.set_id)
        .where(CardSet.cached_at.is_not(None))
    ).all()
    wanted_number = _screenshot_number(details.card_number)
    if wanted_number:
        rows = [row for row in rows if _screenshot_number(row[0].number) == wanted_number]

    ranked = []
    for card, card_set in rows:
        name_score = _text_similarity(details.card_name, card.name)
        set_score = _text_similarity(details.set_name, card_set.name)
        exact_name = _match_text(details.card_name) == _match_text(card.name)
        # Number is already an exact filter when present. The explicit
        # exact-name bonus separates a base card from "(SPR)" / reprint
        # records that legitimately share the same set and printed number.
        score = name_score * 0.50 + set_score * 0.35 + (0.15 if wanted_number else 0)
        if exact_name:
            score += 0.15
        if name_score >= 0.55 and set_score >= 0.55:
            ranked.append((score, exact_name, card, card_set))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)

    if ranked and ranked[0][0] >= 0.78:
        # Close non-exact candidates are ambiguity, not permission to pick a
        # random alt art. An exact title is allowed to beat a close suffix
        # variant such as "(SPR)" because that suffix is the distinction.
        if len(ranked) == 1 or ranked[0][1] or ranked[0][0] - ranked[1][0] >= 0.08:
            return ranked[0][2], ranked[0][3]
    return None, _best_screenshot_set(session, details.set_name)


def _ocr_phrase_score(
    phrase: str, ocr_text: str, *, normalized_ocr: str | None = None,
    ocr_tokens: set[str] | None = None,
) -> tuple[float, bool]:
    needle = _match_text(phrase)
    haystack = normalized_ocr if normalized_ocr is not None else _match_text(ocr_text)
    if not needle or not haystack:
        return 0.0, False
    if needle in haystack:
        return 1.0, True
    wanted = needle.split()
    seen = ocr_tokens if ocr_tokens is not None else set(haystack.split())

    def token_seen(token: str) -> bool:
        if token in seen:
            return True
        # Product-listing titles and OCR both introduce small spelling
        # errors (the real eBay example that prompted this was "Kririn"
        # for catalogue name "Krillin").  Permit a close fuzzy match only
        # for reasonably long words; doing this for short tokens such as
        # "ex", "V" or set digits would create far too many false hits.
        return len(token) >= 5 and any(
            len(candidate) >= 5 and SequenceMatcher(None, token, candidate).ratio() >= 0.75
            for candidate in seen
        )

    coverage = sum(token_seen(token) for token in wanted) / len(wanted)
    return coverage * 0.85, False


def _ocr_contains_number(
    ocr_text: str, number: str, *, upper_ocr: str | None = None,
    no_space_ocr: str | None = None, compact_ocr: str | None = None,
) -> bool:
    wanted = _screenshot_number(number)
    if not wanted:
        return False
    upper = upper_ocr if upper_ocr is not None else ocr_text.upper()
    if wanted.isdigit():
        return bool(re.search(rf"(?<!\d)0*{re.escape(wanted)}(?!\d)", upper))
    no_space = no_space_ocr if no_space_ocr is not None else re.sub(r"\s+", "", upper)
    if wanted in no_space:
        return True
    compact_text = compact_ocr if compact_ocr is not None else re.sub(r"[^A-Z0-9]", "", upper)
    compact_wanted = re.sub(r"[^A-Z0-9]", "", wanted)
    return len(compact_wanted) >= 4 and compact_wanted in compact_text


def _ocr_ungraded_price(ocr_text: str) -> tuple[str, str]:
    """Return (numeric price, currency) nearest the Ungraded/Raw heading.

    Only a small window immediately after that heading is considered. This
    is the guard that keeps the $3/$9.99 eBay advertisement row and PSA 10
    price in the example screenshot out of the editable ungraded field.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if not re.search(r"\b(?:ungraded|raw)\b", line, re.I):
            continue
        window = " ".join(lines[index:index + 3])
        # Begin at the heading and take the first monetary decimal following
        # it. Tesseract commonly renders PriceCharting's "A$1.06" as
        # "A1.06", so the Australian marker deliberately accepts both.
        after_heading = re.split(r"\b(?:ungraded|raw)\b", window, maxsplit=1, flags=re.I)[-1]
        match = re.search(
            r"(?<![A-Z0-9])(?P<prefix>AU\$|A\$|AUD|A)?\s*(?P<price>\d{1,6}[.,]\d{2})(?!\d)",
            after_heading,
            re.I,
        )
        if match:
            price = match.group("price").replace(",", ".")
            prefix = (match.group("prefix") or "").upper()
            return price, "AUD" if prefix in {"A", "A$", "AU$", "AUD"} else ""
    return "", ""


def _ocr_number_candidates(ocr_text: str) -> set[str]:
    """High-signal printed-number shapes visible on product pages."""
    found = set()
    patterns = (
        r"\b[A-Z]{1,8}\d{0,4}-[A-Z0-9]{1,12}\b",  # BT12-136, OP01-001, SVP-196
        r"\b[A-Z]{2,8}\s+\d{1,4}\b",              # SVP 049
        r"#\s*([A-Z0-9-]{1,16})\b",                 # #58, #SDV1-039
        r"\bNO\.?\s*([A-Z0-9-]{1,16})\b",           # No.480, No 338
        r"\b(\d{1,4})/\d{1,4}\b",                  # 58/102
    )
    upper = ocr_text.upper()
    for pattern in patterns:
        for match in re.finditer(pattern, upper):
            value = match.group(1) if match.lastindex else match.group(0)
            normalized = _screenshot_number(value)
            if normalized:
                found.add(normalized)
    return found


def _extract_details_with_local_ocr(path: Path, session: Session) -> ExtractedCardDetailsScreenshot | None:
    """Credit-free first pass for catalogue/price-guide screenshots.

    OCR text is matched against the *local* catalogue rather than parsed as
    free-form metadata. That gives the same exact-ID/alt-art safety as the
    rest of review and is particularly reliable for product pages, which
    print card name, number and set together near the top.
    """
    try:
        with Image.open(path) as image:
            ocr_text = pytesseract.image_to_string(image.convert("RGB"), config="--psm 6")
    except (OSError, RuntimeError, UnidentifiedImageError):
        return None
    if not ocr_text.strip():
        return None

    query = (
        select(CatalogCard, CardSet).join(CardSet, CardSet.id == CatalogCard.set_id)
        .where(CardSet.cached_at.is_not(None))
    )
    number_candidates = _ocr_number_candidates(ocr_text)
    rows = []
    if number_candidates:
        rows = session.execute(
            query.where(
                func.upper(func.replace(CatalogCard.number, " ", "")).in_(number_candidates)
            )
        ).all()
    # Some catalogues use punctuation/leading-zero conventions the OCR
    # token cannot mirror exactly. Fall back to the complete cache only if
    # the indexed-looking tokens found no possible local rows at all.
    if not rows:
        rows = session.execute(query).all()
    normalized_ocr = _match_text(ocr_text)
    ocr_tokens = set(normalized_ocr.split())
    upper_ocr = ocr_text.upper()
    no_space_ocr = re.sub(r"\s+", "", upper_ocr)
    compact_ocr = re.sub(r"[^A-Z0-9]", "", upper_ocr)
    set_scores: dict[str, tuple[float, bool]] = {}
    ranked = []
    for card, card_set in rows:
        number_seen = _ocr_contains_number(
            ocr_text, card.number, upper_ocr=upper_ocr,
            no_space_ocr=no_space_ocr, compact_ocr=compact_ocr,
        )
        name_score, exact_name = _ocr_phrase_score(
            card.name, ocr_text, normalized_ocr=normalized_ocr, ocr_tokens=ocr_tokens,
        )
        if card_set.id not in set_scores:
            set_scores[card_set.id] = _ocr_phrase_score(
                card_set.name, ocr_text, normalized_ocr=normalized_ocr, ocr_tokens=ocr_tokens,
            )
        set_score, exact_set = set_scores[card_set.id]
        if not number_seen and not (exact_name and exact_set):
            continue
        # Some eBay titles identify older cards by the exact continuous
        # Carddass number and character but say only "Dragon Ball Z
        # Carddass", omitting the formal "Hondan Part 12" set name.  An
        # exact number plus a strong name is safe here: if more than one
        # catalogue row shares them, the ambiguity check below still
        # refuses to choose between those rows.
        identity_without_set = number_seen and name_score >= 0.80
        if name_score < 0.60 or (set_score < 0.55 and not identity_without_set):
            continue
        score = (0.45 if number_seen else 0) + name_score * 0.35 + set_score * 0.20
        ranked.append((score, exact_name, exact_set, card, card_set))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    if not ranked or ranked[0][0] < 0.72:
        return None
    if len(ranked) > 1:
        lead = ranked[0]
        runner_up = ranked[1]
        if lead[0] - runner_up[0] < 0.08 and not (lead[1] and not runner_up[1]):
            return None

    _, _, _, card, card_set = ranked[0]
    price, currency = _ocr_ungraded_price(ocr_text)
    domain = re.search(r"\b([a-z0-9-]+\.(?:com\.au|com|net|org|co\.uk))\b", ocr_text, re.I)
    unreadable = [field for field, value in (
        ("ungraded_price", price), ("currency", currency),
    ) if not value]
    # Rarity/language come from the matched local record in
    # _screenshot_details_payload, not from guesses about page text.
    unreadable.extend(["rarity", "language"])
    return ExtractedCardDetailsScreenshot(
        card_name=card.name,
        set_name=card_set.name,
        card_number=card.number,
        rarity="",
        language="",
        ungraded_price=price,
        currency=currency,
        source_site=domain.group(1).lower() if domain else "local screenshot OCR",
        unreadable_fields=unreadable,
    )


def _extract_scan_crop_with_local_ocr(
    path: Path, session: Session,
) -> ExtractedCardDetailsScreenshot | None:
    """Identify the queued card itself without any paid recognition call.

    A physical card normally prints ``number/set total`` at its lower edge.
    That pair narrows the catalogue dramatically; the OCR'd card name then
    separates the few sets that share a printed total. Autocontrast is
    important on scans with coloured name bars/text, where raw Tesseract
    can miss both the title and bottom number despite a sharp image.
    """
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            prepared = ImageOps.autocontrast(ImageOps.grayscale(rgb))
            ocr_text = pytesseract.image_to_string(prepared, config="--psm 11")
            scan_phash = imagehash.phash(rgb)
            scan_art_phash = art_phash(rgb)
    except (OSError, RuntimeError, UnidentifiedImageError):
        return None
    if not ocr_text.strip():
        return _extract_scan_crop_with_phash(path, session)

    printed_pairs = []
    for match in re.finditer(r"(?<!\d)(\d{1,4})\s*/\s*(\d{2,4})(?!\d)", ocr_text):
        number = match.group(1).lstrip("0") or "0"
        printed_pairs.append((number, int(match.group(2))))
    if not printed_pairs:
        return _extract_scan_crop_with_phash(path, session)

    normalized_ocr = _match_text(ocr_text)
    ocr_tokens = set(normalized_ocr.split())
    ranked = []
    for number, total in printed_pairs:
        rows = session.execute(
            select(CatalogCard, CardSet).join(CardSet, CardSet.id == CatalogCard.set_id)
            .where(CardSet.cached_at.is_not(None))
        ).all()
        for card, card_set in rows:
            if _screenshot_number(card.number) != number:
                continue
            card_pair = re.search(r"(?<!\d)\d{1,4}\s*/\s*(\d{2,4})(?!\d)", card.number or "")
            card_total = int(card_pair.group(1)) if card_pair else None
            # Ordinary Pokemon records store N and put TOTAL on CardSet.
            # Special reprint records such as Trick or Trade retain their
            # original N/TOTAL on each individual card because one special
            # set contains cards from many source sets.
            if total not in {card_total, card_set.printed_total}:
                continue
            name_score, exact_name = _ocr_phrase_score(
                card.name, ocr_text, normalized_ocr=normalized_ocr, ocr_tokens=ocr_tokens,
            )
            if name_score < 0.60:
                continue
            visual_distance = None
            if card.phash:
                whole_distance = scan_phash - imagehash.hex_to_hash(card.phash)
                art_distance = (
                    scan_art_phash - imagehash.hex_to_hash(card.art_phash)
                    if card.art_phash else whole_distance
                )
                # Whole-card structure is the steadier signal; the art
                # window breaks ties between visually near-identical stamped
                # reprints and their base cards.
                visual_distance = whole_distance * 2 + art_distance
            ranked.append((
                name_score + (0.20 if exact_name else 0), exact_name,
                visual_distance, card, card_set,
            ))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    if not ranked:
        return _extract_scan_crop_with_phash(path, session)
    if len(ranked) > 1:
        best_text_score = ranked[0][0]
        contenders = [row for row in ranked if best_text_score - row[0] < 0.08]
        if len(contenders) > 1:
            visually_ranked = sorted(
                (row for row in contenders if row[2] is not None), key=lambda row: row[2],
            )
            if len(visually_ranked) < 2 or visually_ranked[0][2] >= visually_ranked[1][2]:
                # Same name/number can represent multiple arts or special
                # printings. Leave a true visual tie to the reviewer rather
                # than turning a good OCR read into the wrong physical card.
                return None
            ranked = visually_ranked

    _, _, _, card, card_set = ranked[0]
    return ExtractedCardDetailsScreenshot(
        card_name=card.name,
        set_name=card_set.name,
        card_number=card.number,
        rarity="",
        language="",
        ungraded_price="",
        currency="",
        source_site="local scan OCR + reference image",
        unreadable_fields=["rarity", "language", "ungraded_price", "currency"],
    )


def _extract_scan_crop_with_phash(
    path: Path, session: Session,
) -> ExtractedCardDetailsScreenshot | None:
    """Credit-free fallback for cards without a Pokemon-style N/TOTAL.

    Vintage Carddass uses one continuous number (for example ``480``), so
    `_extract_scan_crop_with_local_ocr` cannot use its printed-total route.
    A very strong, unambiguous match against an already-cached reference is
    still safe.  Keep the bar deliberately higher than the review page's
    suggestion bar: this endpoint fills the form directly, so it accepts
    only distance <= 4 and confidence >= .90.
    """
    catalogs = _load_all_catalogs(session)
    if not catalogs:
        return None
    totals = {card_set.id: card_set.printed_total for card_set in catalog.cached_sets(session)}
    candidates = _suggestions([path], catalogs, totals, get_settings()).get(path)
    if not candidates:
        return None
    set_id, identification = candidates[0]
    if (
        identification.card_id is None
        or identification.phash_distance is None
        or identification.phash_distance > 4
        or identification.confidence < 0.90
    ):
        return None
    card = session.get(CatalogCard, identification.card_id)
    card_set = session.get(CardSet, set_id)
    if card is None or card_set is None:
        return None
    return ExtractedCardDetailsScreenshot(
        card_name=card.name,
        set_name=card_set.name,
        card_number=card.number,
        rarity="",
        language="",
        ungraded_price="",
        currency="",
        source_site="local scan reference image",
        unreadable_fields=["rarity", "language", "ungraded_price", "currency"],
    )


def _screenshot_price(raw: str, currency: str) -> tuple[str, str | None]:
    raw = raw.strip()
    currency = (currency or "").strip().upper()
    if currency in {"A", "AU", "AUS", "A$", "AU$", "AUD"} or re.match(r"^(?:A\$|AU\$|AUD)\s*", raw, re.I):
        currency = "AUD"
    if not raw.strip():
        return "", None
    numeric = re.sub(r"^(?:A\$|AU\$|AUD|A(?=\s*\d)|\$)\s*", "", raw, flags=re.I).replace(",", "").strip()
    try:
        value = Decimal(numeric).quantize(Decimal("0.01"))
    except InvalidOperation:
        return "", "The ungraded price could not be read as a number."
    if not value.is_finite() or value <= 0:
        return "", "The ungraded price was not a positive amount."
    if currency != "AUD":
        label = currency or "an unknown currency"
        return "", f"The screenshot price is {label}, so it was not put into the AUD price field."
    return str(value), None


def _screenshot_details_payload(session: Session, details: ExtractedCardDetailsScreenshot) -> dict:
    card, card_set = _match_screenshot_card(session, details)
    if card is not None:
        tcg_card = session.get(TcgCard, card.id)
        name = card.name
        number = card.number
        rarity = details.rarity.strip() or card.rarity or ""
        language = details.language.strip() or (tcg_card.language if tcg_card else "") or "English"
        card_id = card.id
    else:
        name = details.card_name.strip()
        number = details.card_number.strip()
        rarity = details.rarity.strip()
        language = details.language.strip() or "English"
        card_id = ""

    price, price_warning = _screenshot_price(details.ungraded_price, details.currency)
    warnings = [price_warning] if price_warning else []
    if card is None:
        warnings.append(
            "The details were read, but no unique catalogue card matched. Check the set and choose the exact card before saving."
        )
    source = details.source_site.strip() or "the screenshot"
    return {
        "matched": card is not None,
        "catalog_item_id": card_id,
        "set_id": card_set.id if card_set else "",
        "set_name": card_set.name if card_set else details.set_name.strip(),
        "name": name,
        "number": number,
        "rarity": rarity,
        "language": language,
        "variant": (known.value if card is not None and (known := catalogue_variant(card)[0]) else ""),
        "price": price,
        "currency": "AUD" if price else (details.currency or "").strip().upper(),
        "source_site": source,
        "warnings": warnings,
        "apply_fields": [
            field for field, present in (
                ("rarity", bool(details.rarity.strip())),
                ("language", bool(details.language.strip())),
                ("price", bool(price)),
            ) if present
        ],
    }


def _details_from_lens_results(session: Session, matches: list[dict]) -> ExtractedCardDetailsScreenshot | None:
    """Turn Lens titles into a safe exact catalogue result when possible.

    A printed code alone may represent base and alternate art records. Such
    ties stay unmatched; the code/title are still returned to prefill review.
    """
    titles = [str(row.get("title") or "").strip() for row in matches if row.get("title")]
    if not titles:
        return None
    combined = "\n".join(titles[:12])
    codes = _ocr_number_candidates(combined)
    candidates: list[tuple[CatalogCard, CardSet]] = []
    if codes:
        for card, card_set in session.execute(
            select(CatalogCard, CardSet).join(CardSet, CardSet.id == CatalogCard.set_id)
        ).all():
            if _screenshot_number(card.number) in codes:
                candidates.append((card, card_set))

    chosen = None
    if len(candidates) == 1:
        chosen = candidates[0]
    elif candidates:
        normalized = _match_text(combined)
        tokens = set(normalized.split())
        ranked = sorted(
            ((_ocr_phrase_score(card.name, combined, normalized_ocr=normalized, ocr_tokens=tokens)[0], card, card_set)
             for card, card_set in candidates),
            reverse=True, key=lambda row: row[0],
        )
        if ranked[0][0] >= .75 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= .10):
            chosen = (ranked[0][1], ranked[0][2])

    if chosen:
        card, card_set = chosen
        tcg = session.get(TcgCard, card.id)
        return ExtractedCardDetailsScreenshot(
            card_name=card.name, set_name=card_set.name, card_number=card.number,
            rarity=card.rarity or "", language=(tcg.language if tcg else "") or "",
            ungraded_price="", currency="", source_site="Google Lens reverse search",
            unreadable_fields=["ungraded_price", "currency"],
        )

    # Still useful: show the reviewer Lens's best title and a detected code,
    # but do not invent a catalogue id or enable Save.
    return ExtractedCardDetailsScreenshot(
        card_name=titles[0], set_name="", card_number=sorted(codes)[0] if codes else "",
        rarity="", language="", ungraded_price="", currency="",
        source_site="Google Lens reverse search",
        unreadable_fields=["set_name", "rarity", "language", "ungraded_price", "currency"],
    )


def _provider_failure_message(exc: Exception) -> str:
    messages = []
    current: BaseException | None = exc
    while current is not None:
        messages.append(str(current))
        current = current.__cause__
    detail = " ".join(messages).casefold()
    if "credit balance is too low" in detail or "purchase credits" in detail:
        return (
            "Local OCR could not identify this screenshot, and the external recognition credits are exhausted. "
            "Add Anthropic API credits or enter the details manually."
        )
    if "authentication" in detail or "invalid x-api-key" in detail:
        return "Local OCR could not identify this screenshot, and the external recognition API key is invalid."
    return "Local OCR and the external recognition service could not read card details from that screenshot."


def _claude_evidence_summary(read: ExtractedCard, *, matched: bool) -> str:
    """A short evidence summary, not hidden model chain-of-thought."""
    visible = [
        f"name {read.character.strip()}" if read.character.strip() else "",
        f"set {read.set_name.strip()}" if read.set_name.strip() else "",
        f"number {read.card_number.strip()}" if read.card_number.strip() else "",
        f"rarity {read.rarity.strip()}" if read.rarity.strip() else "",
        f"language {read.language.strip()}" if read.language.strip() else "",
    ]
    summary = "Claude read " + ", ".join(value for value in visible if value) + "."
    if read.unreadable_fields:
        summary += " Unclear fields: " + ", ".join(read.unreadable_fields) + "."
    summary += (
        " Those fields resolve to one cached catalogue card."
        if matched else
        " They do not yet resolve to one unique cached catalogue card."
    )
    return summary


@router.post("/claude-suggest")
def review_claude_suggest(
    crop: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    """Return a Claude vision candidate; never commit or mutate the crop."""
    path = _safe_crop(crop)
    settings = get_settings()
    correlation_id = str(uuid.uuid4())
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=503, detail="Claude card recognition is not configured.")
    try:
        extracted = extract_card_details(
            path, None, settings.anthropic_api_key,
            game="trading card (identify the exact game/product line from the card itself)",
        )
    except RecognitionError as exc:
        record_event(
            session, actor_type="user", actor_id=user, action="review.claude_suggest",
            resource_type="review_crop", resource_id=crop, outcome="failed",
            correlation_id=correlation_id, details={"error": str(exc)},
        )
        message = _provider_failure_message(exc).replace("Local OCR could not identify this screenshot, and ", "")
        raise HTTPException(status_code=502, detail=message) from exc
    if len(extracted) != 1:
        raise HTTPException(
            status_code=422,
            detail="Claude could not isolate exactly one identifiable card; recrop around one complete card and try again.",
        )

    read = extracted[0]
    details = ExtractedCardDetailsScreenshot(
        card_name=read.character.strip(), set_name=read.set_name.strip(),
        card_number=read.card_number.strip(), rarity=read.rarity.strip(),
        language=read.language.strip(), ungraded_price="", currency="",
        source_site="Claude vision suggestion",
        unreadable_fields=read.unreadable_fields + ["ungraded_price", "currency"],
    )
    payload = _screenshot_details_payload(session, details)
    payload["reasoning"] = _claude_evidence_summary(read, matched=payload["matched"])
    payload["warnings"].append(
        "Suggestion only: nothing has been saved. Verify the fields, then click Save & add if correct."
    )
    record_event(
        session, actor_type="user", actor_id=user, action="review.claude_suggest",
        resource_type="review_crop", resource_id=crop, outcome="success",
        correlation_id=correlation_id,
        details={"catalog_item_id": payload["catalog_item_id"], "matched": payload["matched"]},
    )
    return payload


@router.post("/detect-crop")
async def review_detect_crop(
    crop: str = Form(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    path = _safe_crop(crop)
    correlation_id = str(uuid.uuid4())
    details = _extract_scan_crop_with_local_ocr(path, session)
    if details is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            record_event(
                session, actor_type="user", actor_id=user, action="review.detect_crop",
                resource_type="review_crop", resource_id=crop, outcome="failed",
                correlation_id=correlation_id, details={"reason": "local_ocr_no_unique_match_ai_not_configured"},
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    "Local OCR could not get a unique card and AI card reading is not configured. "
                    "Try Recrop, or choose the set and search by the printed number."
                ),
            )
        try:
            extracted = extract_card_details(
                path, None, settings.anthropic_api_key,
                game="trading card (identify the exact game/product line from the card itself)",
            )
        except RecognitionError as exc:
            # The configured model may be temporarily unavailable or out of
            # credits. Lens is already configured for collectible search and
            # often exposes the printed code in its result titles.
            if not settings.apify_token:
                raise HTTPException(status_code=502, detail=_provider_failure_message(exc)) from exc
            try:
                lens = await search_image(
                    path, settings.apify_token,
                    getattr(settings, "apify_google_lens_actor_id", "borderline~google-lens"),
                )
            except ReverseImageSearchError as lens_exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"{_provider_failure_message(exc)} Google Lens also failed: {lens_exc}",
                ) from lens_exc
            details = _details_from_lens_results(session, lens)
            if details is None:
                raise HTTPException(status_code=422, detail="Neither card-text reading nor Google Lens found a usable identity.")
            extracted = []
        if details is None and len(extracted) != 1:
            raise HTTPException(
                status_code=422,
                detail="AI could not isolate exactly one identifiable card in this crop; try Recrop first.",
            )
        if details is None:
            read = extracted[0]
            details = ExtractedCardDetailsScreenshot(
                card_name=read.character.strip(), set_name=read.set_name.strip(),
                card_number=read.card_number.strip(), rarity=read.rarity.strip(),
                language=read.language.strip(), ungraded_price="", currency="",
                source_site="AI reading of current scan",
                unreadable_fields=read.unreadable_fields + ["ungraded_price", "currency"],
            )

    payload = _screenshot_details_payload(session, details)
    method = (
        "ai_card_read" if details.source_site == "AI reading of current scan" else
        "google_lens" if details.source_site == "Google Lens reverse search" else "local_ocr"
    )
    payload["warnings"].append(
        "Detected from the scan locally; the existing price was left unchanged."
        if method == "local_ocr" else
        "Read from the physical card with AI; verify the printed code and catalogue match before saving."
        if method == "ai_card_read" else
        "AI text reading was unavailable, so Google Lens was used; verify the printed code before saving."
    )
    record_event(
        session, actor_type="user", actor_id=user, action="review.detect_crop",
        resource_type="review_crop", resource_id=crop, outcome="success",
        correlation_id=correlation_id,
        details={"catalog_item_id": payload["catalog_item_id"], "method": method},
    )
    return payload


@router.post("/details-screenshot")
async def review_details_screenshot(
    crop: str = Form(...),
    screenshot: UploadFile = File(...),
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
) -> dict:
    # Tie the request to a crop that is still awaiting review. Besides
    # preventing path tricks, this keeps a stale tab from spending an API
    # call on a card already handled elsewhere.
    crop_path = _safe_crop(crop)
    settings = get_settings()

    data = await screenshot.read(MAX_DETAILS_SCREENSHOT_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="choose a screenshot first")
    if len(data) > MAX_DETAILS_SCREENSHOT_BYTES:
        raise HTTPException(status_code=413, detail="screenshot is larger than the 8 MB safety limit")

    try:
        with Image.open(BytesIO(data)) as uploaded:
            image_format = (uploaded.format or "").upper()
            uploaded.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="the uploaded file is not a readable image") from exc
    suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image_format)
    if suffix is None:
        raise HTTPException(status_code=400, detail="use a JPG, PNG, or WebP screenshot")

    correlation_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"details{suffix}"
        path.write_bytes(data)
        details = _extract_details_with_local_ocr(path, session)
        recognition_method = "local_ocr"
        # Listings for older or unofficial cards often omit the collector
        # number entirely (the real Turles example said only "Anniversary
        # Edition HR Turles").  The screenshot alone is then ambiguous,
        # but the queued crop can still be an exact match to a local
        # reference.  Use that strong credit-free signal before considering
        # the external provider.
        if details is None:
            details = _extract_scan_crop_with_phash(crop_path, session)
            if details is not None:
                recognition_method = "local_crop_reference"
        if details is None:
            if not getattr(settings, "anthropic_api_key", None):
                raise HTTPException(
                    status_code=503,
                    detail="Local OCR could not identify this screenshot and external recognition is not configured.",
                )
            try:
                details = extract_card_details_screenshot(path, api_key=settings.anthropic_api_key)
                recognition_method = "external_vision"
            except Exception as exc:
                provider_error = str(exc.__cause__ or exc)
                record_event(
                    session, actor_type="user", actor_id=user, action="review.details_screenshot",
                    resource_type="review_crop", resource_id=crop, outcome="failed",
                    correlation_id=correlation_id,
                    details={"error": str(exc), "provider_error": provider_error[:1000]},
                )
                raise HTTPException(status_code=502, detail=_provider_failure_message(exc)) from exc

    if not any((details.card_name.strip(), details.set_name.strip(), details.card_number.strip())):
        raise HTTPException(status_code=422, detail="no card details were visible in that screenshot")
    payload = _screenshot_details_payload(session, details)
    if recognition_method == "local_crop_reference":
        payload["warnings"].append(
            "The listing omitted a unique card number, so EzBay identified it from the queued scan; "
            "the price was left unchanged."
        )
    record_event(
        session, actor_type="user", actor_id=user, action="review.details_screenshot",
        resource_type="review_crop", resource_id=crop, outcome="success",
        correlation_id=correlation_id,
        details={"matched": payload["matched"], "catalog_item_id": payload["catalog_item_id"],
                 "source_site": payload["source_site"], "method": recognition_method},
    )
    return payload


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


@router.get("/catalog/{set_id}")
def review_catalog(
    set_id: str,
    user: str = Depends(require_dashboard_user),
    session: Session = Depends(get_session),
):
    """Small authenticated lookup used when the reviewer changes Set.

    Loading every cached card into every review form made a twelve-card page
    unnecessarily huge. This returns one set on demand so the phone UI can
    still search by card name or number and retain the exact catalogue id.
    """
    card_set = session.get(CardSet, set_id)
    if card_set is None or card_set.cached_at is None:
        raise HTTPException(status_code=404, detail="set is not cached")
    cards = list(
        session.scalars(
            select(CatalogCard).where(CatalogCard.set_id == set_id)
            .order_by(CatalogCard.name, CatalogCard.number, CatalogCard.id)
        )
    )
    card_ids = [card.id for card in cards]
    prices = _best_prices(session, card_ids)
    languages = dict(
        session.execute(
            select(TcgCard.catalog_item_id, TcgCard.language)
            .where(TcgCard.catalog_item_id.in_(card_ids))
        ).all()
    ) if card_ids else {}
    variants = {
        card.id: known.value
        for card in cards
        if (known := catalogue_variant(card)[0]) is not None
    }
    total = card_set.printed_total
    return {
        "set_id": set_id,
        "cards": [
            {
                "id": card.id,
                "v": f"{card.number}/{total}" if total else card.number,
                "number": card.number,
                "n": card.name,
                "rarity": card.rarity or "",
                "language": languages.get(card.id, "English"),
                "variant": variants.get(card.id, ""),
                "price": str(prices[card.id]) if card.id in prices else "",
            }
            for card in cards
        ],
    }


@router.get("/image/{name}")
def review_image(name: str, user: str = Depends(require_dashboard_user)):
    return FileResponse(_safe_crop(name), headers={"Cache-Control": "private, max-age=300"})


_BULK_TOOLBAR = (
    "<div class='toolbar bulk-toolbar'>"
    "<label class='chip'><input type='checkbox' id='bulk-all'> Select all on page</label>"
    "<button type='button' id='bulk-save' disabled>Save &amp; add selected (0)</button>"
    "<span id='bulk-status' class='muted' role='status' aria-live='polite'></span>"
    "</div>"
)

_STYLE = """<style>
.bulk-pick{display:inline-flex;gap:6px;align-items:center;font-size:13px;font-weight:600;cursor:pointer}
.bulk-pick input,.bulk-toolbar input{width:18px;height:18px}
.bulk-toolbar button{padding:10px 16px;border-radius:10px;cursor:pointer;font-size:15px}
.bulk-toolbar button:disabled{opacity:.5;cursor:not-allowed}
.review-card.bulk-failed{outline:2px solid var(--danger)}
.bulk-error{color:var(--danger);font-size:13px;margin-top:6px}
.ebay-active{font-size:14px;margin:4px 0 8px}
.toolbar{display:flex;gap:10px;align-items:center;margin:0 0 18px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;padding:10px 16px;border-radius:999px;font-size:14px;
  border:1px solid var(--panel-border);color:var(--text-dim);text-decoration:none;background:var(--surface-sunken)}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.muted{color:var(--text-dim);font-size:13px}

/* One column full-width under ~300px (a phone), however many fit at
   >=260px each beyond that -- auto-fill/minmax does the collapse on its
   own, no media query needed, so the single-card-per-screen phone layout
   this had before and the side-by-side desktop grid asked for now are the
   same rule at two different viewport widths. */
.review-grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(260px, 1fr));gap:16px}
.review-card{background:var(--panel);border:1px solid var(--panel-border);border-radius:16px;
  padding:16px;display:flex;flex-direction:column;gap:8px;min-width:0}
.review-card>*{min-width:0;max-width:100%}

.review-face{display:block;border-radius:12px;overflow:hidden;background:var(--surface-sunken);
  aspect-ratio:5/7;max-height:340px;margin:0 auto}
.review-face img{width:100%;height:100%;object-fit:contain;display:block}

.review-position{font-size:12px;font-weight:700;color:var(--text-dim);letter-spacing:.04em;
  text-transform:uppercase}

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

/* The review card is an editor, not just a match preview. Two compact
   columns on desktop collapse naturally to one usable phone column. */
.review-details{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:4px 0}
.review-details label{display:flex;flex-direction:column;gap:5px;color:var(--text-dim);
  font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.055em;min-width:0}
.review-details label.wide{grid-column:1/-1}
.review-details input,.review-details select{width:100%;box-sizing:border-box;min-width:0;
  background:var(--surface-sunken);border:1px solid var(--panel-border);border-radius:9px;
  padding:10px;color:var(--text);font-size:14px;text-transform:none;letter-spacing:normal}
.review-details input:focus,.review-details select:focus{outline:2px solid color-mix(in srgb,var(--accent) 35%,transparent);
  border-color:var(--accent)}
.details-shot{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 9px;margin:4px 0;
  padding:11px;border:1px dashed var(--panel-border);border-radius:11px;background:var(--surface-sunken)}
.details-shot-title{grid-column:1/-1;font-size:13px;font-weight:700}
.details-shot-help{grid-column:1/-1;color:var(--text-dim);font-size:11.5px;line-height:1.4}
.details-shot-file{min-width:0;width:100%;font-size:12px;color:var(--text-dim);align-self:center}
.details-shot-read,.detect-crop-read,.claude-suggest-read{border:1px solid var(--panel-border);border-radius:9px;padding:8px 10px;background:var(--panel);
  color:var(--text);font-size:12px;font-weight:650;cursor:pointer;white-space:nowrap}
.detect-crop-read,.claude-suggest-read{grid-column:1/-1;width:100%;border-style:solid;color:var(--accent)}
.claude-suggest-read{color:var(--text);border-style:dashed}
.details-shot-read:hover,.detect-crop-read:hover,.claude-suggest-read:hover{border-color:var(--accent);color:var(--accent)}
.details-shot-read:disabled,.detect-crop-read:disabled,.claude-suggest-read:disabled{opacity:.5;cursor:wait}
.details-shot-status{grid-column:1/-1;min-height:16px;font-size:11.5px;line-height:1.4;color:var(--text-dim)}
.details-shot-status.ok{color:var(--status-ok,#278a55)}
.details-shot-status.bad{color:var(--danger,#c23b3b)}
.title-import{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px 9px;margin:4px 0;padding:10px;
  border:1px solid var(--panel-border);border-radius:10px;background:var(--panel-soft)}
.title-import .details-shot-title,.title-import .details-shot-help,.title-import-status{grid-column:1/-1}
.title-import-input{min-width:0;width:100%}
.title-import-go{border:1px solid var(--panel-border);border-radius:9px;padding:8px 12px;background:var(--panel);font-weight:700}
.title-import-go:hover{border-color:var(--accent);color:var(--accent)}
.title-import-go:disabled{opacity:.5;cursor:wait}
.pick-primary{align-items:center;justify-content:center;text-align:center;font-weight:700;
  font-size:15px;background:linear-gradient(120deg,var(--accent),var(--accent-2));color:#fff;
  border:none;padding:12px;margin-top:8px}
.pick-primary:hover{border-color:transparent;opacity:.92}
.pick-primary:disabled{opacity:.4;cursor:not-allowed}
.review-alts{margin-top:8px}
.review-alts summary{cursor:pointer;font-size:12.5px;color:var(--text-dim)}
.review-alts .picks{margin-top:8px}

.review-form{display:flex;gap:8px;margin-top:6px;flex-direction:column;max-width:none}
/* wraps rather than overflowing: on a narrow phone the input and both
   buttons don't fit on one line, and overflow hid the Discard button. */
.printing{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12px;
  color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em}
.printing select{flex:1 1 auto;min-width:0;background:var(--surface-sunken);border:1px solid var(--panel-border);
  border-radius:10px;padding:10px;color:var(--text);font-size:14px;text-transform:none;letter-spacing:normal}
.manual{display:flex;gap:8px;margin-top:2px;flex-wrap:wrap;min-width:0}
.autocomplete{min-width:0;position:relative}
.manual button{flex:0 0 auto;padding:12px 18px;border-radius:10px;font-size:15px;cursor:pointer;
  border:none;background:linear-gradient(120deg,var(--accent),var(--accent-2));
  color:#fff;font-weight:700}
.manual button.ghost{background:none;border:1px solid var(--panel-border);
  color:var(--text-dim);font-weight:600}
.manual button.ghost:hover{border-color:var(--danger);color:var(--danger)}

.autocomplete-list{position:absolute;left:0;right:0;bottom:calc(100% + 4px);z-index:30;
  background:var(--panel);border:1px solid var(--panel-border);border-radius:10px;
  max-height:200px;overflow-y:auto;box-shadow:0 8px 22px rgba(0,0,0,.22)}
.autocomplete-item{display:block;width:100%;text-align:left;padding:10px 12px;background:none;
  border:none;border-bottom:1px solid var(--panel-border);color:var(--text);font-size:14px;cursor:pointer}
.autocomplete-item:last-child{border-bottom:none}
.autocomplete-item:hover,.autocomplete-item:active{background:var(--surface-sunken)}
.autocomplete-item b{color:var(--accent);font-weight:700}
.candidate-choice.selected{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
@media(max-width:360px){
  .review-details{grid-template-columns:1fr}.review-details label.wide{grid-column:auto}
  .details-shot,.title-import{grid-template-columns:1fr}.details-shot-read,.title-import-go{width:100%}
}
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

  // Each card stays a self-contained editor. The exact catalogue id is
  // retained behind the visible fields so duplicate card numbers cannot
  // silently fall back to the first/base printing.
  var catalogCache = {};
  var catalogRequests = {};
  document.querySelectorAll('.review-form').forEach(function (form) {
    var wrap = form.querySelector('.autocomplete');
    var script = wrap && wrap.querySelector('script.number-options');
    var numberInput = form.querySelector('input[name="number"]');
    var nameInput = form.querySelector('input[name="name"]');
    var rarityInput = form.querySelector('input[name="rarity"]');
    var languageInput = form.querySelector('input[name="language"]');
    var priceInput = form.querySelector('input[name="price"]');
    var variantInput = form.querySelector('select[name="variant"]');
    var setSelect = form.querySelector('select[name="set_id"]');
    var selectedId = form.querySelector('input[name="selected_card_id"]');
    var detailsId = form.querySelector('input[name="details_card_id"]');
    var dirtyInput = form.querySelector('input[name="dirty_fields"]');
    var primaryPick = form.querySelector('.pick-primary');
    var list = wrap && wrap.querySelector('.autocomplete-list');
    var detailsFile = form.querySelector('.details-shot-file');
    var detailsRead = form.querySelector('.details-shot-read');
    var detectCropRead = form.querySelector('.detect-crop-read');
    var claudeSuggestRead = form.querySelector('.claude-suggest-read');
    var detailsStatus = form.querySelector('.details-shot .details-shot-status');
    var titleImportInput = form.querySelector('.title-import-input');
    var titleImportGo = form.querySelector('.title-import-go');
    var titleImportStatus = form.querySelector('.title-import-status');
    var pricechartingLink = form.querySelector('.pricecharting-link');
    var ebaySoldLink = form.querySelector('.ebay-sold-link');
    if (!wrap || !numberInput || !list || !setSelect) return;

    var options = [];
    if (script) {
      try { options = JSON.parse(script.textContent); } catch (e) { options = []; }
    }

    function setDirty(field) {
      var dirty = dirtyInput.value ? dirtyInput.value.split(',') : [];
      if (dirty.indexOf(field) === -1) dirty.push(field);
      dirtyInput.value = dirty.filter(Boolean).join(',');
    }
    [['name', nameInput], ['rarity', rarityInput], ['language', languageInput], ['price', priceInput]]
      .forEach(function (pair) {
        pair[1].addEventListener('input', function () { setDirty(pair[0]); });
      });

    function applyCard(opt) {
      setSelect.value = opt.setId || setSelect.value;
      numberInput.value = opt.number || '';
      nameInput.value = opt.n || '';
      rarityInput.value = opt.rarity || '';
      languageInput.value = opt.language || 'English';
      priceInput.value = opt.price || '';
      if (variantInput && opt.variant) variantInput.value = opt.variant;
      var researchQuery = [opt.n, opt.number, opt.language].filter(Boolean).join(' ');
      if (pricechartingLink) pricechartingLink.href = 'https://www.pricecharting.com/search-products?q=' + encodeURIComponent(researchQuery) + '&type=prices';
      if (ebaySoldLink) ebaySoldLink.href = 'https://www.ebay.com.au/sch/i.html?_nkw=' + encodeURIComponent(researchQuery) + '&LH_Sold=1&LH_Complete=1';
      selectedId.value = opt.id || '';
      detailsId.value = opt.id || '';
      dirtyInput.value = '';
      list.hidden = true;
      form.querySelectorAll('.candidate-choice').forEach(function (button) {
        button.classList.toggle('selected', button.dataset.cardId === opt.id);
      });
      if (primaryPick && primaryPick.dataset.cardId !== opt.id) primaryPick.disabled = true;
    }

    form.querySelectorAll('.candidate-choice').forEach(function (button) {
      button.addEventListener('click', function () {
        applyCard({
          id: button.dataset.cardId,
          setId: button.dataset.setId,
          number: button.dataset.number,
          n: button.dataset.name,
          rarity: button.dataset.rarity,
          language: button.dataset.language,
          variant: button.dataset.variant,
          price: button.dataset.price
        });
        // Keep search suggestions aligned if this alternate came from a
        // different set, without delaying the visible selection.
        loadSet(button.dataset.setId, false);
      });
    });

    function render(matches) {
      list.innerHTML = '';
      matches.forEach(function (opt) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'autocomplete-item';
        item.textContent = opt.v + '  ' + opt.n;
        // pointerdown fires before blur, including in iOS Safari.
        item.addEventListener('pointerdown', function (evt) {
          evt.preventDefault();
          opt.setId = setSelect.value;
          applyCard(opt);
        });
        list.appendChild(item);
      });
      list.hidden = matches.length === 0;
    }

    function search() {
      var q = numberInput.value.trim().toLowerCase();
      var matches = options.filter(function (opt) {
        return !q || opt.n.toLowerCase().indexOf(q) !== -1 || opt.v.toLowerCase().indexOf(q) !== -1;
      }).slice(0, 8);
      render(matches);
    }

    function loadSet(setId, clearFields) {
      if (!setId) { options = []; return; }
      if (clearFields) {
        numberInput.value = '';
        nameInput.value = '';
        rarityInput.value = '';
        languageInput.value = 'English';
        priceInput.value = '';
        selectedId.value = '';
        detailsId.value = '';
        dirtyInput.value = '';
      }
      if (catalogCache[setId]) {
        options = catalogCache[setId];
        return;
      }
      if (!catalogRequests[setId]) {
        catalogRequests[setId] = fetch(
          '/review/catalog/' + encodeURIComponent(setId),
          {headers: {'Accept': 'application/json'}}
        ).then(function (response) {
          return response.ok ? response.json() : Promise.reject(response);
        }).then(function (payload) {
          catalogCache[setId] = payload.cards || [];
          return catalogCache[setId];
        });
      }
      catalogRequests[setId].then(function (cards) {
        // Ignore a slow response for a set the reviewer has since left.
        if (setSelect.value === setId) options = cards;
      }).catch(function () { options = []; });
    }

    numberInput.addEventListener('input', function () {
      selectedId.value = '';
      detailsId.value = '';
      if (primaryPick) primaryPick.disabled = true;
      search();
    });
    numberInput.addEventListener('focus', search);
    numberInput.addEventListener('blur', function () {
      setTimeout(function () { list.hidden = true; }, 200);
    });
    setSelect.addEventListener('change', function () {
      if (primaryPick) primaryPick.disabled = true;
      loadSet(setSelect.value, true);
    });
    loadSet(setSelect.value, false);

    function applyScreenshot(data) {
      if (data.set_id) {
        setSelect.value = data.set_id;
        loadSet(data.set_id, false);
      }
      if (data.number) numberInput.value = data.number;
      if (data.name) nameInput.value = data.name;
      if (data.rarity) rarityInput.value = data.rarity;
      if (data.language) languageInput.value = data.language;
      if (data.price) priceInput.value = data.price;
      if (variantInput && data.variant) variantInput.value = data.variant;
      selectedId.value = data.catalog_item_id || '';
      detailsId.value = data.catalog_item_id || '';
      dirtyInput.value = '';
      (data.apply_fields || []).forEach(setDirty);
      list.hidden = true;
      form.querySelectorAll('.candidate-choice').forEach(function (button) {
        button.classList.toggle('selected', button.dataset.cardId === data.catalog_item_id);
      });
      if (primaryPick) primaryPick.disabled = primaryPick.dataset.cardId !== data.catalog_item_id;

      var summary = data.matched
        ? 'Matched ' + data.name + ' #' + data.number + ' from ' + data.source_site + '.'
        : 'Details read from ' + data.source_site + '; check the catalogue match before saving.';
      var warnings = data.warnings || [];
      var evidence = data.reasoning ? ' ' + data.reasoning : '';
      detailsStatus.textContent = summary + evidence + (warnings.length ? ' ' + warnings.join(' ') : '');
      detailsStatus.className = 'details-shot-status ' + (data.matched ? 'ok' : '');
    }

    function importListingTitle() {
      var title = titleImportInput && titleImportInput.value.trim();
      if (!title) {
        titleImportStatus.textContent = 'Paste a listing title first.';
        titleImportStatus.className = 'title-import-status details-shot-status bad';
        return;
      }
      var data = new FormData();
      data.append('title', title);
      titleImportGo.disabled = true;
      titleImportStatus.textContent = 'Looking up the printed card code…';
      titleImportStatus.className = 'title-import-status details-shot-status';
      fetch('/review/parse-listing-title', {method: 'POST', body: data})
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            if (!response.ok) throw new Error(payload.detail || 'Could not match that title.');
            return payload;
          });
        })
        .then(function (payload) {
          applyScreenshot(payload);
          titleImportStatus.textContent = 'Matched ' + payload.name + ' #' + payload.number + ' in ' + payload.set_name + '.';
          titleImportStatus.className = 'title-import-status details-shot-status ok';
        })
        .catch(function (error) {
          titleImportStatus.textContent = error.message || 'Could not match that title.';
          titleImportStatus.className = 'title-import-status details-shot-status bad';
        })
        .finally(function () { titleImportGo.disabled = false; });
    }

    if (titleImportGo) titleImportGo.addEventListener('click', importListingTitle);
    if (titleImportInput) titleImportInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); importListingTitle(); }
    });

    function readScreenshot() {
      if (!detailsFile || !detailsFile.files || !detailsFile.files[0]) {
        detailsStatus.textContent = 'Choose a screenshot first.';
        detailsStatus.className = 'details-shot-status bad';
        return;
      }
      var cropInput = form.querySelector('input[name="crop"]');
      var data = new FormData();
      data.append('crop', cropInput.value);
      data.append('screenshot', detailsFile.files[0]);
      detailsRead.disabled = true;
      if (detectCropRead) detectCropRead.disabled = true;
      if (claudeSuggestRead) claudeSuggestRead.disabled = true;
      detailsStatus.textContent = 'Reading card details and ungraded price…';
      detailsStatus.className = 'details-shot-status';
      if (window.EzBayProgress) window.EzBayProgress.begin('POST /review/details-screenshot');
      fetch('/review/details-screenshot', {method: 'POST', body: data})
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            if (!response.ok) throw new Error(payload.detail || 'Could not read that screenshot.');
            return payload;
          });
        })
        .then(applyScreenshot)
        .catch(function (error) {
          detailsStatus.textContent = error.message || 'Could not read that screenshot.';
          detailsStatus.className = 'details-shot-status bad';
        })
        .finally(function () {
          detailsRead.disabled = false;
          if (detectCropRead) detectCropRead.disabled = false;
          if (claudeSuggestRead) claudeSuggestRead.disabled = false;
          if (window.EzBayProgress) window.EzBayProgress.done('POST /review/details-screenshot');
        });
    }

    function detectCurrentCrop() {
      var cropInput = form.querySelector('input[name="crop"]');
      var data = new FormData();
      data.append('crop', cropInput.value);
      detectCropRead.disabled = true;
      if (detailsRead) detailsRead.disabled = true;
      if (claudeSuggestRead) claudeSuggestRead.disabled = true;
      detailsStatus.textContent = 'Reading the current card locally, then with AI if needed…';
      detailsStatus.className = 'details-shot-status';
      if (window.EzBayProgress) window.EzBayProgress.begin('POST /review/detect-crop');
      fetch('/review/detect-crop', {method: 'POST', body: data})
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            if (!response.ok) throw new Error(payload.detail || 'Could not identify the current scan.');
            return payload;
          });
        })
        .then(applyScreenshot)
        .catch(function (error) {
          detailsStatus.textContent = error.message || 'Could not identify the current scan.';
          detailsStatus.className = 'details-shot-status bad';
        })
        .finally(function () {
          detectCropRead.disabled = false;
          if (detailsRead) detailsRead.disabled = false;
          if (claudeSuggestRead) claudeSuggestRead.disabled = false;
          if (window.EzBayProgress) window.EzBayProgress.done('POST /review/detect-crop');
        });
    }
    if (detailsRead && detailsFile && detailsStatus) {
      detailsRead.addEventListener('click', readScreenshot);
      detailsFile.addEventListener('change', function () {
        if (detailsFile.files && detailsFile.files[0]) readScreenshot();
      });
    }
    if (detectCropRead && detailsStatus) detectCropRead.addEventListener('click', detectCurrentCrop);

    function askClaude() {
      var cropInput = form.querySelector('input[name="crop"]');
      var data = new FormData();
      data.append('crop', cropInput.value);
      claudeSuggestRead.disabled = true;
      if (detailsRead) detailsRead.disabled = true;
      if (detectCropRead) detectCropRead.disabled = true;
      detailsStatus.textContent = 'Asking Claude to read this card…';
      detailsStatus.className = 'details-shot-status';
      if (window.EzBayProgress) window.EzBayProgress.begin('POST /review/claude-suggest');
      fetch('/review/claude-suggest', {method: 'POST', body: data})
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            if (!response.ok) throw new Error(payload.detail || 'Claude could not identify this card.');
            return payload;
          });
        })
        .then(applyScreenshot)
        .catch(function (error) {
          detailsStatus.textContent = error.message || 'Claude could not identify this card.';
          detailsStatus.className = 'details-shot-status bad';
        })
        .finally(function () {
          claudeSuggestRead.disabled = false;
          if (detailsRead) detailsRead.disabled = false;
          if (detectCropRead) detectCropRead.disabled = false;
          if (window.EzBayProgress) window.EzBayProgress.done('POST /review/claude-suggest');
        });
    }
    if (claudeSuggestRead && detailsStatus) claudeSuggestRead.addEventListener('click', askClaude);
  });
})();
</script>"""


_BULK_SCRIPT = """<script>
(function () {
  var saveBtn = document.getElementById('bulk-save');
  var allBox = document.getElementById('bulk-all');
  var status = document.getElementById('bulk-status');
  if (!saveBtn) return;
  function checked() { return Array.prototype.slice.call(document.querySelectorAll('.bulk-check:checked')); }
  function refresh() {
    var n = checked().length;
    saveBtn.textContent = 'Save & add selected (' + n + ')';
    saveBtn.disabled = n === 0;
  }
  document.addEventListener('change', function (evt) {
    if (evt.target.classList && evt.target.classList.contains('bulk-check')) refresh();
  });
  allBox.addEventListener('change', function () {
    document.querySelectorAll('.bulk-check').forEach(function (box) { box.checked = allBox.checked; });
    refresh();
  });
  function bodyFor(form) {
    var data = new FormData(form);
    data.set('action', 'accept');
    var primary = form.querySelector('button.pick-primary');
    var numberInput = form.querySelector("input[name='number']");
    var setSelect = form.querySelector("[name='set_id']");
    if (primary && numberInput && numberInput.value === primary.dataset.number &&
        (!setSelect || setSelect.value === primary.dataset.setId)) {
      data.set('pick', primary.value);
    }
    return data;
  }
  saveBtn.addEventListener('click', function () {
    var boxes = checked();
    if (!boxes.length) return;
    if (!window.confirm('Save ' + boxes.length + ' card(s) to inventory using the values shown on each card?')) return;
    saveBtn.disabled = true;
    var done = 0, failed = 0, index = 0;
    function next() {
      if (index >= boxes.length) {
        status.textContent = 'Saved ' + done + (failed ? ', ' + failed + ' need attention' : '') + '.';
        if (!failed) { window.location.reload(); } else { refresh(); }
        return;
      }
      var box = boxes[index++];
      var card = box.closest('.review-card');
      var form = card.querySelector('form.review-form');
      status.textContent = 'Saving ' + index + ' of ' + boxes.length + '…';
      fetch('/review/decide', {method: 'POST', body: bodyFor(form), redirect: 'follow'})
        .then(function (response) {
          if (response.ok) { done++; card.remove(); return; }
          return response.json().catch(function () { return {}; }).then(function (payload) {
            failed++;
            card.classList.add('bulk-failed');
            var old = card.querySelector('.bulk-error'); if (old) old.remove();
            var note = document.createElement('div');
            note.className = 'bulk-error';
            note.textContent = payload.detail || ('Could not save (HTTP ' + response.status + ')');
            form.appendChild(note);
            box.checked = false;
          });
        })
        .catch(function () { failed++; box.checked = false; })
        .then(next);
    }
    next();
  });
  refresh();
})();
</script>"""
