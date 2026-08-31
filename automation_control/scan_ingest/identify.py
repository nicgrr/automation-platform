"""Identify a scanned card crop against one already-known set.

Two independent signals, deliberately combined rather than tried in strict
sequence:

* **OCR** of the printed collector number ("71/114"). Exact when it works,
  and the only thing that reliably separates same-evolution-line lookalikes
  (Klink #71 vs Klang #72 share almost identical artwork). But it depends
  heavily on scan resolution -- measured on real ~150 DPI scans it read
  only ~40% of cards, because the number is barely 15px tall there. At 300
  DPI it becomes much more dependable.
* **Perceptual hash** against the cached reference art for this set. Robust
  to blur and low resolution -- measured 17/18 on those same 150 DPI scans
  -- but it can't tell near-identical artwork apart.

They fail in different ways, so agreement between them is strong evidence
and disagreement is a reason to ask a human. The original plan called for
"OCR first, pHash fallback"; the real-scan measurements above inverted
which one carries the weight, so both always run and the result records
which signals actually agreed.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

import cv2
import imagehash
import numpy as np
import pytesseract
from PIL import Image

# Where the collector number sits on the card, as fractions of the crop.
# The corner depends on the era, so both are tried rather than guessed at:
# XY/SM print it bottom-RIGHT beside the illustrator credit (verified on
# real Steam Siege scans), while Sword & Shield moved it bottom-LEFT
# (verified on real Vivid Voltage scans, where the bottom-right region read
# nothing at all and bottom-left read "151/185" cleanly). Each is generous
# enough to absorb the few-percent framing variation the grid split leaves.
NUMBER_REGIONS = (
    (0.74, 0.945, 0.99, 0.995),  # XY/SM era: bottom-right   (x0, y0, x1, y1)
    (0.01, 0.930, 0.28, 0.995),  # SWSH era:  bottom-left
)

# Tesseract wants roughly 25px of character height; a 150 DPI scan gives
# ~15px, so upscale before thresholding. Cubic interpolation preserves the
# stroke shapes better than nearest/linear at these sizes.
OCR_UPSCALE = 8

_NUMBER_PATTERN = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})")

# Below this Hamming margin the top two candidates are effectively tied, so
# the art signal cannot distinguish them however close the winner is -- the
# Klink #71 / Klang #72 situation. Such a match is capped below the
# auto-accept bar and sent to a human even at distance 0, because "very
# similar to two different cards" is not evidence for either one.
MIN_UNAMBIGUOUS_MARGIN = 3
AMBIGUOUS_CONFIDENCE_CAP = 0.70

# Rotation candidates for `detect_sheet_set_and_rotation`, as clockwise
# degrees -- matches `detect.py`'s `post_rotation_degrees` convention so the
# result can be passed straight through.
_ROTATION_CANDIDATES = (0, 90, 180, 270)
_PIL_TRANSPOSE_FOR_CLOCKWISE = {
    0: None,
    90: Image.Transpose.ROTATE_270,   # 270 deg counterclockwise == 90 clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,   # 90 deg counterclockwise == 270 clockwise
}

# How many crops on a sheet must confidently match the same (set, rotation)
# before it's trusted -- one lucky match isn't enough evidence to route an
# entire sheet's commits to a set, but requiring more than this on a
# 1-2-card partial sheet would make auto-detection never fire at all.
MIN_CONFIDENT_SHEET_MATCHES = 2

# Only matches this much *inside* the distance limit count toward the tally
# above. Confirmed against a real misroute: with the sheet's actual set not
# yet cached, a wrong set scraped together 3 matches sitting exactly at the
# limit (12, 12, 12) and won, while the correct set -- once cached --
# matched at 6, 6, 10, 10. Matches pinned to the boundary are what
# "everything is equally bad" looks like, so they're evidence of nothing
# and must not be able to carry a routing decision on their own.
STRONG_MATCH_DISTANCE_MARGIN = 2


class Source(str, Enum):
    """Which signal(s) produced the match."""
    BOTH = "both"           # OCR and pHash agree -- strongest evidence
    OCR = "ocr"             # number read cleanly, pHash unavailable/silent
    PHASH = "phash"         # art matched, number unreadable
    CONFLICT = "conflict"   # signals disagree -- always needs a human
    NONE = "none"           # nothing matched


@dataclass
class Identification:
    card_id: str | None
    number: str | None
    name: str | None
    source: Source
    confidence: float
    phash_distance: int | None = None
    phash_margin: int | None = None
    ocr_text: str | None = None
    note: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        """Whether a human must confirm before this is committed. Never
        auto-commit a low-confidence match, per the module's contract."""
        return self.card_id is None or self.source is Source.CONFLICT or self.confidence < 0.75


@dataclass
class ReferenceCard:
    """The subset of a cached CatalogCard that identification needs -- kept
    as a plain dataclass so this module stays free of DB session handling
    and is trivially testable."""
    card_id: str
    number: str
    name: str
    phash: str


def read_card_number(image_path: Path, set_total: int | None = None) -> tuple[str | None, str | None]:
    """OCR the printed collector number. Returns (number, raw_ocr_text).

    `number` is the left-hand part ("71" from "71/114"), or None if nothing
    parseable was read or the value falls outside the set's real range --
    an out-of-range read is a misread, not a discovery, so it's rejected
    rather than passed on as if it were a real card number.
    """
    image = cv2.imread(str(image_path))
    if image is None:
        return None, None

    height, width = image.shape[:2]
    first_raw: str | None = None
    for x0, y0, x1, y1 in NUMBER_REGIONS:
        region = image[int(height * y0) : int(height * y1), int(width * x0) : int(width * x1)]
        if region.size == 0:
            continue

        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, None, fx=OCR_UPSCALE, fy=OCR_UPSCALE, interpolation=cv2.INTER_CUBIC)
        _, binarized = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        try:
            raw = pytesseract.image_to_string(binarized, config="--psm 7 -c tessedit_char_whitelist=0123456789/").strip()
        except Exception:
            # A missing/broken tesseract binary must not take the whole scan
            # down -- pHash alone still identifies most cards.
            return None, None

        if not raw:
            continue
        first_raw = first_raw or raw

        match = _NUMBER_PATTERN.search(raw)
        if not match:
            continue
        number = str(int(match.group(1)))
        # An out-of-range read is a misread, not a discovery -- but it may
        # just be the wrong corner for this card's era, so keep looking
        # rather than giving up on the card.
        if set_total is not None and not (1 <= int(number) <= set_total):
            continue
        return number, raw

    return None, first_raw


def read_set_totals(crop_paths: list[Path]) -> list[int]:
    """The printed set totals ("264" from "199/264") read off a sheet's
    crops, most-agreed-on first.

    This is how an *uncached* set gets identified: pHash can't help when
    there's nothing cached to compare against, but the denominator is
    printed on every card and names the set's size directly. Reading it
    across the whole sheet and ranking by agreement tolerates the OCR being
    unreliable per card (measured ~40% legible at low DPI) -- a couple of
    correct reads out of eight is enough, and misreads rarely agree with
    each other.

    Rotation is handled by the caller cropping upright first; totals are
    returned unvalidated, since which of them is a real set is a question
    for the catalog, not for OCR.
    """
    counts: dict[int, int] = {}
    for crop_path in crop_paths:
        # set_total=None: the range check exists to validate a card's own
        # number against a known set, and here the set is exactly what
        # isn't known yet.
        _, raw = read_card_number(crop_path, set_total=None)
        if not raw:
            continue
        match = _NUMBER_PATTERN.search(raw)
        if not match:
            continue
        total = int(match.group(2))
        if total > 0:
            counts[total] = counts.get(total, 0) + 1
    return [total for total, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def best_set_by_art(
    image_path: Path, catalogs: dict[str, list[ReferenceCard]], max_phash_distance: int = 12,
) -> str | None:
    """Which cached set's art a crop most resembles, or None if none match.

    Art only, and the crop is hashed exactly once. This exists so callers
    that need to search *every* cached set can narrow to one first: running
    the full `identify_card` per set instead re-runs OCR per set, which
    dominated the cost (measured: minutes for a few dozen crops against a
    dozen sets, versus seconds this way) for a signal that can't pick a set
    anyway -- a collector number means nothing until you know the set.
    """
    hashed = {sid: [r for r in refs if r.phash] for sid, refs in catalogs.items()}
    hashed = {sid: refs for sid, refs in hashed.items() if refs}
    if not hashed:
        return None
    try:
        with Image.open(image_path) as image:
            scan_hash = imagehash.phash(image.convert("RGB"))
    except Exception:
        return None

    best_set, best_distance = None, None
    for set_id, references in hashed.items():
        distance = min(scan_hash - imagehash.hex_to_hash(r.phash) for r in references)
        if distance <= max_phash_distance and (best_distance is None or distance < best_distance):
            best_set, best_distance = set_id, distance
    return best_set


def _rank_by_phash(image_path: Path, references: list[ReferenceCard]) -> list[tuple[int, ReferenceCard]]:
    hashed = [r for r in references if r.phash]
    if not hashed:
        return []
    try:
        with Image.open(image_path) as image:
            scan_hash = imagehash.phash(image.convert("RGB"))
    except Exception:
        return []
    scored = [(scan_hash - imagehash.hex_to_hash(r.phash), r) for r in hashed]
    scored.sort(key=lambda item: item[0])
    return scored


def _phash_confidence(distance: int, margin: int, max_distance: int) -> float:
    """Blend absolute similarity with how far ahead the winner is.

    Both matter and neither is sufficient alone: a low distance with a tiny
    margin means two cards look nearly identical (the Klink #71 / Klang #72
    case), while a large margin at a poor distance means everything matched
    badly.

    Calibrated against 18 real 150 DPI scans, where every match inside the
    distance threshold turned out correct (distances 4-12) and both errors
    sat outside it (16 and 18). So an in-threshold match starts from a
    deliberately generous base and margin decides whether it clears
    auto-accept -- the residual risk in-threshold is lookalikes, which is
    exactly what a small margin signals. Only 18 samples back this, so
    treat the constants as a starting point to re-tune, not settled truth.
    """
    closeness = max(0.0, 1.0 - distance / (max_distance + 1))
    separation = min(1.0, margin / 6.0)
    score = round(0.55 + 0.25 * closeness + 0.20 * separation, 3)
    if margin < MIN_UNAMBIGUOUS_MARGIN:
        return min(score, AMBIGUOUS_CONFIDENCE_CAP)
    return score


def identify_card(
    image_path: Path, references: list[ReferenceCard], set_total: int | None = None,
    max_phash_distance: int = 12,
) -> Identification:
    """Identify one scanned card crop against the active set's reference
    cards. Runs both signals and reconciles them; never guesses past the
    configured distance threshold.
    """
    by_number = {r.number: r for r in references}
    ocr_number, ocr_raw = read_card_number(image_path, set_total)
    ocr_match = by_number.get(ocr_number) if ocr_number else None

    ranked = _rank_by_phash(image_path, references)
    phash_match = ranked[0][1] if ranked else None
    distance = ranked[0][0] if ranked else None
    margin = (ranked[1][0] - ranked[0][0]) if len(ranked) > 1 else None

    phash_ok = phash_match is not None and distance is not None and distance <= max_phash_distance
    phash_confidence = _phash_confidence(distance, margin or 0, max_phash_distance) if phash_ok else 0.0

    # Both signals agree -- the strongest evidence available, and enough to
    # accept even when the pHash distance alone would have been marginal.
    if ocr_match and phash_match and ocr_match.card_id == phash_match.card_id:
        return Identification(
            card_id=ocr_match.card_id, number=ocr_match.number, name=ocr_match.name,
            source=Source.BOTH, confidence=1.0, phash_distance=distance, phash_margin=margin, ocr_text=ocr_raw,
        )

    # They disagree, and both are inside their thresholds. Surface pHash's
    # answer as the suggestion rather than OCR's: on real low-DPI scans OCR
    # produced *well-formed, in-range, but wrong* numbers (a Mankey #52 read
    # as "57/114"), which would silently overwrite a correct art match. A
    # human confirms either way, so the only question is which suggestion
    # saves them more keystrokes -- and that's the more accurate signal.
    if ocr_match and phash_ok:
        return Identification(
            card_id=phash_match.card_id, number=phash_match.number, name=phash_match.name,
            source=Source.CONFLICT, confidence=0.5, phash_distance=distance, phash_margin=margin, ocr_text=ocr_raw,
            note=f"art matches #{phash_match.number} ({phash_match.name}), but OCR read #{ocr_match.number} ({ocr_match.name})",
        )

    if ocr_match:
        return Identification(
            card_id=ocr_match.card_id, number=ocr_match.number, name=ocr_match.name,
            source=Source.OCR, confidence=0.9, phash_distance=distance, phash_margin=margin, ocr_text=ocr_raw,
        )

    if phash_ok:
        return Identification(
            card_id=phash_match.card_id, number=phash_match.number, name=phash_match.name,
            source=Source.PHASH, confidence=phash_confidence,
            phash_distance=distance, phash_margin=margin, ocr_text=ocr_raw,
        )

    note = "no reference hashes for this set -- is it cached?" if not ranked else f"closest art match was #{phash_match.number} at distance {distance} (limit {max_phash_distance})"
    return Identification(
        card_id=None, number=None, name=None, source=Source.NONE, confidence=0.0,
        phash_distance=distance, phash_margin=margin, ocr_text=ocr_raw, note=note,
    )


def detect_sheet_set_and_rotation(
    crop_paths: list[Path], catalogs: dict[str, list[ReferenceCard]],
    max_phash_distance: int = 12, min_confident_matches: int = MIN_CONFIDENT_SHEET_MATCHES,
) -> tuple[str, int] | None:
    """Work out which *cached* set a freshly scanned sheet belongs to, and
    which way its cards are rotated -- so a long-running session never has
    to be told either up front, and switching piles is just "cache the set,
    then scan."

    pHash only, deliberately: OCR needs to already know roughly where the
    number sits and at what orientation, so it can't bootstrap this the way
    it can confirm an already-oriented, already-set-scoped match. Tries
    every (rotation, cached set) combination against every crop on the
    sheet and picks whichever combination the most crops agree on -- a
    physical sheet is one pile, so it's virtually always entirely one set,
    making agreement across crops strong evidence even when any single
    crop's match would be too weak to trust alone.

    Returns None when nothing clears `min_confident_matches`: either the
    set genuinely isn't cached yet, or the sheet is too ambiguous to route
    automatically. The caller should leave it alone rather than guess --
    same "never auto-commit a low-confidence match" contract as
    `identify_card`.
    """
    hashed_catalogs = {set_id: [r for r in refs if r.phash] for set_id, refs in catalogs.items()}
    hashed_catalogs = {set_id: refs for set_id, refs in hashed_catalogs.items() if refs}
    if not hashed_catalogs or not crop_paths:
        return None

    strong_limit = max_phash_distance - STRONG_MATCH_DISTANCE_MARGIN
    distances: dict[tuple[str, int], list[int]] = {}
    for crop_path in crop_paths:
        try:
            with Image.open(crop_path) as original:
                original = original.convert("RGB")
                for degrees in _ROTATION_CANDIDATES:
                    transpose = _PIL_TRANSPOSE_FOR_CLOCKWISE[degrees]
                    rotated = original if transpose is None else original.transpose(transpose)
                    scan_hash = imagehash.phash(rotated)
                    for set_id, refs in hashed_catalogs.items():
                        best_distance = min(scan_hash - imagehash.hex_to_hash(r.phash) for r in refs)
                        if best_distance <= strong_limit:
                            distances.setdefault((set_id, degrees), []).append(int(best_distance))
        except Exception:
            continue

    if not distances:
        return None
    # Most strong matches wins; ties break toward the tighter distances,
    # since "same number of matches but consistently closer" is the better
    # explanation of the same sheet.
    best_key, best_distances = max(distances.items(), key=lambda item: (len(item[1]), -sum(item[1])))
    if len(best_distances) < min(min_confident_matches, len(crop_paths)):
        return None
    return best_key
