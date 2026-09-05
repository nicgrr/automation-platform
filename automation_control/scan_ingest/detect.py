from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# Real TCG card is 63x88mm -> 0.7159. A detected cell whose short/long ratio
# falls outside this band isn't a card (two cards merged into one band, a
# stray object, a mis-split). Wider than the theoretical value because the
# scanner's own edge cropping shaves a few px off inconsistently -- confirmed
# against a real 24-card scan where two genuinely single, unmerged cards
# measured ar=0.599 and ar=0.600 purely from per-card row-band variance
# (their column neighbors ranged 0.62-0.69), which a 0.60 floor flagged as
# false "not card-shaped" and skipped the whole sheet for. The actual merge
# detector is size-based (`_split_merged_cards`); this check is a backstop,
# so it can afford to run looser.
MIN_ASPECT_RATIO = 0.55
MAX_ASPECT_RATIO = 0.85

# A row/column band narrower than this (as a fraction of the sheet's
# corresponding dimension) is noise -- a scanner edge artifact or a shadow
# line -- not a row/column of cards.
MIN_BAND_FRACTION = 0.05

# A row/column is "content" (part of a card) rather than "gap" (empty bed)
# when its pixel standard deviation exceeds this fraction of the sheet's
# peak row/column deviation. Card artwork is busy and varied; the empty bed
# is near-uniform, so the two separate cleanly. Relative rather than
# absolute so it survives different scanners, bed colors, and exposures.
CONTENT_STD_RATIO = 0.25

# How many rows/columns _content_bands averages over before thresholding --
# see the smoothing comment inside it. Deliberately tiny: a real sheet with
# cards sitting slightly rotated (see the FALLBACK_CONTENT_STD_RATIO
# comment above) can have a genuine inter-column gap as narrow as ~6px at
# full scan resolution, and confirmed against that real sheet that a
# window of 7 already bridges it away entirely, silently halving the
# detected column count. 3 is small enough to leave that gap intact while
# still bridging the 1-2px dips between text-line glyphs that fragmented a
# real Metapod's attack-text section into pieces too short to count as
# their own band -- the two failures this constant balances came from two
# different real sheets, not from reasoning about either alone.
SMOOTHING_WINDOW = 3

# See the retry inside _content_bands: a single detected band covering at
# least this fraction of the profile is treated as "the default threshold
# found no gaps," not "there's genuinely one giant card."
STUCK_BAND_FRACTION = 0.6

# The steeper threshold tried only in that one situation. Confirmed against a
# real scan that this recovers three real, evenly-sized columns (~1550-1650px
# each) where the default threshold saw one 4906px band -- and confirmed
# against every other sheet processed so far that this ratio is never even
# reached for them, since they already produce more than one band at the
# default.
FALLBACK_CONTENT_STD_RATIO = 0.4

# A detected band whose long dimension is at least this many times the
# sheet's median card size is treated as multiple cards touching with no
# gap between them (confirmed against a real flatbed scan: two landscape
# cards stacked with zero gap produced one band whose *aspect ratio* still
# looked like a plausible single card, so the ratio check alone missed it).
# Below 2x is deliberately not auto-split -- a merge that isn't at least
# ~2 cards' worth is more likely a genuine oversized card/measurement noise
# than something safe to guess a split point for.
MERGE_SIZE_RATIO = 1.6

# --- rejoining a card split down its middle ---------------------------------
# The mirror of MERGE_SIZE_RATIO above. A card's own plain text panel can be
# as uniform as the empty bed beside it -- measured on a real Paldean Fates
# sheet photographed on white paper, where one column of cards split into
# 812px and 1286px pieces separated by a 94px "gap", while the genuine gap
# between columns was 184px. Gap size alone therefore cannot separate them,
# and neither can aspect ratio: those pieces scored 0.75 and 0.80, both
# inside the card-shaped band.
#
# What does separate them is the card's real proportions. Rows are detected
# *within* a column, so a vertical slice of a card still reports the card's
# full height -- which makes height the trustworthy axis and lets the
# expected width be predicted from it.
CARD_ASPECT = 63 / 88  # 0.7159, the real card's short/long ratio

# A band narrower than this fraction of the predicted card width is a piece
# of a card rather than a card.
FRAGMENT_WIDTH_RATIO = 0.8
# ...and pieces are only joined while the result stays within this multiple
# of one card, so two genuinely separate cards are never fused.
MERGED_WIDTH_TOLERANCE = 1.25
# How close a band must be to the predicted width to count as evidence that
# the prediction is the right one.
WIDTH_MATCH_TOLERANCE = 0.2

# --- corner-accurate refinement of each detected band ---
# The variance bands above locate cards, but only roughly: a card whose edge
# region is low-contrast gets its band cut short (measured on a real 600 DPI
# scan: bands 1321-1581px tall for cards that are physically ~1488px), and a
# card lying a few degrees crooked gets an axis-aligned box with background
# wedges in the corners. Both distort the crop once it's resized to a fixed
# output shape, and a distorted crop is exactly what perceptual hashing
# cannot match. So each band is re-measured: expand it, find the card's true
# four corners, and warp that quadrilateral flat.

# How far to grow a band before looking for the card's real edges -- enough
# to recover a truncated band, small enough to usually stay off the
# neighbouring card.
QUAD_MARGIN = 0.10

# A pixel this far (Euclidean, BGR) from the sampled background colour is
# card rather than bed. Sampling the background from the expanded band's own
# corners rather than assuming a colour is what makes this survive both the
# amber bed of the earlier scans and the white bed of the later ones.
QUAD_BACKGROUND_DISTANCE = 40.0

# The found quad must fill at least this much of the expanded band, or it's
# noise//a shadow rather than a card.
QUAD_MIN_AREA_RATIO = 0.35

# ...and must stay within these multiples of the band it came from. Guards
# the case where expansion bleeds into an adjacent card (especially a half
# from `_split_merged_cards`, whose neighbour is touching by definition):
# swallowing two cards yields a quad far larger than its band, which is
# rejected in favour of the plain axis-aligned crop.
QUAD_MIN_BAND_RATIO = 0.6
QUAD_MAX_BAND_RATIO = 1.4

# Output crop size for each card -- matches the 63:88mm ratio
# (750/1050 = 0.7143) at a resolution that's already good enough to use
# directly as an eBay listing photo, per the spec.
OUTPUT_WIDTH = 750
OUTPUT_HEIGHT = 1050

# cv2's rotate constants, keyed by degrees clockwise -- covers the case
# (confirmed against a real scan) where every card on a sheet is placed in
# the same non-upright orientation, e.g. sideways to fit more per sheet.
# The grid split has no notion of which physical card edge is "up", so this
# is a whole-sheet setting the operator sets once per placement method,
# not something detected per card.
_ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass
class DetectedCard:
    """One card's location on the sheet, in grid position and pixel terms.

    `quad` holds the card's four true corners once `_find_card_quad` has
    refined the band (see the QUAD_* constants) -- None when refinement
    didn't find a trustworthy card outline and the plain axis-aligned box
    is used instead.
    """
    row: int
    col: int
    x1: int
    y1: int
    x2: int
    y2: int
    quad: np.ndarray | None = field(default=None, compare=False, repr=False)

    @property
    def size(self) -> tuple[float, float]:
        """The card's (width, height) -- measured from the refined corners
        when available, since a truncated or crooked band misreports both."""
        if self.quad is not None:
            return _quad_dimensions(self.quad)
        return float(self.x2 - self.x1), float(self.y2 - self.y1)

    @property
    def aspect_ratio(self) -> float:
        w, h = self.size
        return min(w, h) / max(w, h) if max(w, h) else 0.0


@dataclass
class DetectionResult:
    crop_paths: list[Path] = field(default_factory=list)
    cards: list[DetectedCard] = field(default_factory=list)
    rows: int = 0
    cols: int = 0
    count: int = 0
    expected_count: int | None = None
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    overlay_path: Path | None = None
    # 1-indexed positions (matching crop_paths order) that failed the
    # aspect-ratio check -- e.g. a card genuinely cut off at the sheet's
    # edge, or two cards merged into one band. Exposed separately from
    # `warnings` so a caller can set aside just these specific crops for
    # review instead of the whole sheet, the same way an identification
    # failure already does per-card -- see `structural_issue` for when that
    # isn't safe.
    misshapen: list[int] = field(default_factory=list)
    # True when the grid itself looks wrong -- no cards found, the count
    # doesn't match what was expected or physically fits, or cards had to be
    # split out of an oversized band -- as opposed to `misshapen`, where the
    # count is trustworthy and only specific cards are flagged. There's no
    # single physical card position to isolate here, so the whole sheet needs
    # a human look (or a rescan) rather than a per-card set-aside.
    structural_issue: bool = False

    @property
    def ok(self) -> bool:
        return not self.needs_review


def _content_bands(
    std_profile: np.ndarray, min_length: int, ratio: float = CONTENT_STD_RATIO
) -> list[tuple[int, int]]:
    """Split a row- or column-wise standard-deviation profile into runs of
    "content" (card) separated by "gap" (empty scanner bed). Returns
    [(start, end), ...] for each content run at least `min_length` long.

    This replaced a contour-based detector that failed on real scans: a
    Pokemon card's border is yellow and the scanner bed photographs
    amber/cream, so brightness *and* color thresholding both bleed the two
    together. But the bed is near-uniform while card art is busy, so
    variance separates them cleanly even when the colors don't.
    """
    if std_profile.size == 0:
        return []

    # A card's own attack-text section is mostly blank background around
    # sparse glyphs, so its instantaneous per-row/column variance dips
    # between text lines -- confirmed against a real Metapod whose attack
    # text created dozens of individual 1-2px dips below threshold, each
    # separating a content run shorter than MIN_BAND_FRACTION, so the
    # *whole* text section (both the runs and the dips) was read as one
    # long gap. A genuine gap sits far below any of these dips even on
    # average (measured: ~2-3 mean std over 100+px, against ~14-25 for the
    # text section's dips) -- smoothing over a short window before
    # thresholding lets a brief dip get outvoted by its high-variance
    # neighbours without meaningfully raising a sustained low region, since
    # the window is far shorter than any real inter-card gap seen on a real
    # scan (the shortest documented is 40px; this is half that).
    smoothing_window = min(SMOOTHING_WINDOW, std_profile.size)
    smoothed = (
        np.convolve(std_profile, np.ones(smoothing_window) / smoothing_window, mode="same")
        if smoothing_window > 1 else std_profile
    )

    threshold = smoothed.max() * ratio
    is_content = smoothed > threshold

    bands: list[tuple[int, int]] = []
    start: int | None = None
    for index, content in enumerate(is_content):
        if content and start is None:
            start = index
        elif not content and start is not None:
            bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, len(is_content)))

    bands = [band for band in bands if band[1] - band[0] >= min_length]

    # A single band spanning most of the sheet means the default threshold
    # never found a gap at all -- confirmed against a real scan where every
    # card sat slightly rotated, so each one's silver border dipped into what
    # should have been the gap at a different height, keeping that column's
    # variance elevated everywhere except the outer margins. Retrying with a
    # steeper threshold only in that specific situation (never when the
    # default already found >1 band) recovers the real gaps without touching
    # sheets the default already splits correctly -- those bands can be
    # narrower than a genuine card (a card's own low-variance text panel), but
    # that's exactly what the existing fragment-rejoin pass downstream exists
    # to repair, using this axis's own trustworthy evidence from elsewhere.
    if ratio == CONTENT_STD_RATIO and len(bands) == 1:
        band_width = bands[0][1] - bands[0][0]
        if band_width >= std_profile.size * STUCK_BAND_FRACTION:
            retried = _content_bands(std_profile, min_length, ratio=FALLBACK_CONTENT_STD_RATIO)
            if len(retried) > 1:
                return retried

    return bands


def _predict_card_width(column_widths: list[int], median_height: float) -> float | None:
    """The width a card should have, given the height rows are reporting.

    A card lies on the bed either landscape or portrait, so height implies
    one of two widths. The *widest* band observed decides between them:
    splitting a card only ever produces pieces narrower than the card, so
    the widest band is the best available lower bound on the real width.

    Counting how many bands each candidate explains was tried first and is
    wrong -- when a card splits into two near-equal halves those halves
    outvote the one intact card, and the detector confidently predicts a
    half-card as the true width. The widest band can't be outvoted that way.

    Returns None when the widest band matches neither candidate, which
    happens if bands are nothing like cards. Declining is the safe failure:
    a wrong prediction here would fuse real cards together.
    """
    if not column_widths or median_height <= 0:
        return None
    widest = max(column_widths)
    candidates = (median_height / CARD_ASPECT, median_height * CARD_ASPECT)
    best = min(candidates, key=lambda c: abs(widest - c))
    if abs(widest - best) > best * WIDTH_MATCH_TOLERANCE:
        return None
    return best


def _merge_fragmented_columns(col_bands: list[tuple[int, int]], expected_width: float) -> list[tuple[int, int]]:
    """Join adjacent column bands that are pieces of one card.

    Absorbs neighbours only while the running band is still materially
    narrower than a card and the result wouldn't overshoot one -- so a
    correctly-sized band is never touched, and two adjacent whole cards are
    never merged into one.
    """
    merged: list[tuple[int, int]] = []
    index = 0
    while index < len(col_bands):
        x1, x2 = col_bands[index]
        while index + 1 < len(col_bands) and (x2 - x1) < expected_width * FRAGMENT_WIDTH_RATIO:
            next_x1, next_x2 = col_bands[index + 1]
            if (next_x2 - x1) > expected_width * MERGED_WIDTH_TOLERANCE:
                break
            x2 = next_x2
            index += 1
        merged.append((x1, x2))
        index += 1
    return merged


def _merge_fragmented_rows(row_bands: list[tuple[int, int]], expected_height: float) -> list[tuple[int, int]]:
    """Join adjacent row bands that are pieces of one physical card."""
    merged: list[tuple[int, int]] = []
    index = 0
    while index < len(row_bands):
        y1, y2 = row_bands[index]
        while index + 1 < len(row_bands) and (y2 - y1) < expected_height * FRAGMENT_WIDTH_RATIO:
            next_y1, next_y2 = row_bands[index + 1]
            if (next_y2 - y1) > expected_height * MERGED_WIDTH_TOLERANCE:
                break
            y2 = next_y2
            index += 1
        merged.append((y1, y2))
        index += 1
    return merged


def _presplit_oversized_columns(col_bands: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Split a column band that's really N cards touching with no gap,
    before row-detection ever runs on it -- a strip spanning two side-by-
    side cards produces a row profile that's the union of both cards'
    internal transitions, not either card's real rows, so waiting until
    afterward (as the final `_split_merged_cards` safety net does) is too
    late to recover cleanly.

    Uses the *narrowest* band as the one-card reference, on the same
    reasoning as the orientation decision above: a merge only ever makes a
    band wider, so the narrowest band is never itself a merge. It can still
    be a fragment (then this splits nothing, since nothing looks 1.6x
    *that* -- the existing fragment-rejoin pass elsewhere handles narrow
    pieces instead).
    """
    if len(col_bands) < 2:
        return col_bands
    narrowest = min(x2 - x1 for x1, x2 in col_bands)
    if not narrowest:
        return col_bands

    split: list[tuple[int, int]] = []
    for x1, x2 in col_bands:
        width = x2 - x1
        multiple = round(width / narrowest) if width / narrowest >= MERGE_SIZE_RATIO else 1
        if multiple > 1:
            step = width // multiple
            for i in range(multiple):
                piece_x1 = x1 + i * step
                piece_x2 = x2 if i == multiple - 1 else x1 + (i + 1) * step
                split.append((piece_x1, piece_x2))
        else:
            split.append((x1, x2))
    return split


def _detect_columns_and_rows(sheet: np.ndarray) -> list[tuple[tuple[int, int], list[tuple[int, int]]]]:
    """Column bands globally, then row bands *independently within each
    column's own vertical strip* -- not a shared grid.

    A mechanically-fed sheet has rows aligned across every column, but
    hand-placed cards on a flatbed often don't: confirmed against a real
    scan where the right-hand column sat lower than the left, which a
    global cross-product of row-bands x column-bands would have merged or
    misaligned. Detecting rows per-column handles both cases -- it
    degrades to the shared-grid result when rows genuinely do line up.
    """
    gray = cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    min_row = int(height * MIN_BAND_FRACTION)
    col_bands = _content_bands(gray.std(axis=0), int(width * MIN_BAND_FRACTION))

    def rows_for(x1: int, x2: int) -> list[tuple[int, int]]:
        return _content_bands(gray[:, x1:x2].std(axis=1), min_row)

    initial = [((x1, x2), rows_for(x1, x2)) for x1, x2 in col_bands]
    # Decide orientation across the sheet, not independently per column.
    # One column may have every card split into two near-equal fragments,
    # while another still contains an intact card.  The latter is the only
    # reliable evidence of the full height (the current real 3x3 layout is
    # exactly this case).
    observed_heights = [y2 - y1 for _, rows in initial for y1, y2 in rows]
    # The *narrowest* band, not the median: two real columns fused with no
    # gap between them only ever makes a band wider, never narrower, so with
    # as few as two raw column bands the median can land squarely on a fused
    # one (confirmed against a real sheet: two columns, widths 3278 and
    # 1597 -- the true single-column width -- where sorted-median picked
    # 3278). A genuinely fragmented column would pull the narrowest *below*
    # the true width instead, which is the safer direction to be wrong in:
    # it degrades to a looser orientation guess rather than actively
    # trusting a merged band's inflated size.
    narrowest_width = min((x2 - x1 for x1, x2 in col_bands), default=0)
    tallest = max(observed_heights, default=0)
    portrait = (
        abs(tallest - narrowest_width / CARD_ASPECT) < abs(tallest - narrowest_width * CARD_ASPECT)
        if narrowest_width and tallest else False
    )
    # With only one or two bands *anywhere on the sheet*, a portrait card and
    # two touching landscape cards are geometrically indistinguishable, so
    # there's nothing trustworthy to rejoin with -- keep the old size-based
    # split path. But once *any* column supplies three or more bands, that
    # evidence is sheet-wide (it fed `tallest` above), not column-local: a
    # short column whose one card split into just two fragments (confirmed
    # against a real 11-card irregular sheet, where the split card was alone
    # in its column) still deserves the rejoin, and gating on *that* column's
    # own count alone silently skipped it.
    trustworthy_evidence = any(len(rows) >= 3 for _, rows in initial)
    # One expected height for the *whole sheet*, from the narrowest column's
    # width -- not each column's own width re-derived per column. A column
    # that's itself a fusion of two real columns would otherwise predict its
    # own, inflated expected height from that same fused width (confirmed
    # against the same real sheet: column 1's genuinely intact rows, ~2100px
    # each, stayed correctly unmerged only because this is now a *shared*
    # target derived from column 2's narrow, correct width instead of
    # column 1's own 3278px band).
    shared_expected_height = (
        narrowest_width / CARD_ASPECT if portrait else narrowest_width * CARD_ASPECT
    ) if narrowest_width else 0.0

    result = []
    for (x1, x2), rows in initial:
        expected_height = shared_expected_height or ((x2 - x1) / CARD_ASPECT if portrait else (x2 - x1) * CARD_ASPECT)
        if trustworthy_evidence:
            rows = _merge_fragmented_rows(rows, expected_height)
        result.append(((x1, x2), rows))

    # A card split down its middle reports the *full* card height in both
    # halves (rows are found within a column), so this first pass gives a
    # trustworthy height even when the widths are wrong -- which is what
    # makes predicting the true width possible. See _predict_card_width.
    heights = [y2 - y1 for _, rows in result for y1, y2 in rows]
    final_cols = col_bands
    if heights:
        median_height = sorted(heights)[len(heights) // 2]
        expected_width = _predict_card_width([x2 - x1 for x1, x2 in col_bands], median_height)
        if expected_width:
            final_cols = _merge_fragmented_columns(col_bands, expected_width)

    # Only now -- with fragments already rejoined into whole cards above --
    # is the narrowest remaining band trustworthy as a one-card reference
    # for the opposite problem, two cards fused with no gap between them.
    # Checking earlier risked the narrowest band being a fragment instead
    # (confirmed: doing this before the rejoin above split a perfectly
    # intact card in an existing test, because the still-unrejoined
    # fragment next to it was narrower still).
    presplit_cols = _presplit_oversized_columns(final_cols)
    if presplit_cols != final_cols:
        final_cols = presplit_cols

    if final_cols != col_bands:
        result = []
        for x1, x2 in final_cols:
            rows = rows_for(x1, x2)
            expected_height = shared_expected_height or ((x2 - x1) / CARD_ASPECT if portrait else (x2 - x1) * CARD_ASPECT)
            if trustworthy_evidence:
                rows = _merge_fragmented_rows(rows, expected_height)
            result.append(((x1, x2), rows))
    return result


def _split_merged_cards(cards: list[DetectedCard]) -> list[DetectedCard]:
    """A band far taller (or wider) than its peers is almost certainly
    several cards touching with no gap between them -- confirmed against a
    real scan where this happened and the merged band's aspect ratio still
    looked like a plausible single card, so ratio-checking alone missed it.
    Splits it evenly along its long axis; the result still gets flagged for
    review by the caller so a human can verify the split landed cleanly.
    """
    if len(cards) < 2:
        return cards

    heights = [c.y2 - c.y1 for c in cards]
    widths = [c.x2 - c.x1 for c in cards]
    median_height = sorted(heights)[len(heights) // 2]
    median_width = sorted(widths)[len(widths) // 2]

    split: list[DetectedCard] = []
    for card in cards:
        height, width = card.y2 - card.y1, card.x2 - card.x1
        vertical_multiple = round(height / median_height) if median_height and height / median_height >= MERGE_SIZE_RATIO else 1
        horizontal_multiple = round(width / median_width) if median_width and width / median_width >= MERGE_SIZE_RATIO else 1

        if vertical_multiple > 1:
            step = height // vertical_multiple
            for i in range(vertical_multiple):
                y1 = card.y1 + i * step
                y2 = card.y2 if i == vertical_multiple - 1 else card.y1 + (i + 1) * step
                split.append(DetectedCard(row=card.row, col=card.col, x1=card.x1, y1=y1, x2=card.x2, y2=y2))
        elif horizontal_multiple > 1:
            step = width // horizontal_multiple
            for i in range(horizontal_multiple):
                x1 = card.x1 + i * step
                x2 = card.x2 if i == horizontal_multiple - 1 else card.x1 + (i + 1) * step
                split.append(DetectedCard(row=card.row, col=card.col, x1=x1, y1=card.y1, x2=x2, y2=card.y2))
        else:
            split.append(card)
    return split


def _order_quad_points(points: np.ndarray) -> np.ndarray:
    """Order four corners as top-left, top-right, bottom-right, bottom-left.

    Uses the standard sum/difference trick, which is valid for the small
    tilts a hand-placed card actually has (measured up to ~4 degrees); a
    card rotated near 45 degrees would confuse it, but that isn't a
    placement anyone makes, and `_find_card_quad`'s size guards reject the
    result anyway.
    """
    ordered = np.zeros((4, 2), dtype=np.float32)
    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]
    ordered[2] = points[np.argmax(total)]
    diff = np.diff(points, axis=1).ravel()
    ordered[1] = points[np.argmin(diff)]
    ordered[3] = points[np.argmax(diff)]
    return ordered


def _quad_dimensions(quad: np.ndarray) -> tuple[float, float]:
    """Width and height of an ordered quad, taking the longer of each
    opposing pair so a slight perspective difference doesn't shrink it."""
    width = max(float(np.linalg.norm(quad[2] - quad[3])), float(np.linalg.norm(quad[1] - quad[0])))
    height = max(float(np.linalg.norm(quad[1] - quad[2])), float(np.linalg.norm(quad[0] - quad[3])))
    return width, height


def _find_card_quad(sheet: np.ndarray, card: DetectedCard) -> np.ndarray | None:
    """The card's four true corners in sheet coordinates, or None to fall
    back to the band's axis-aligned box.

    Works by growing the band, estimating the scanner bed's colour from the
    grown band's own corners (which are bed, not card, whenever the card is
    even slightly inset or crooked), masking everything unlike that colour,
    and taking the minimum-area rectangle around the largest such region.
    """
    height, width = sheet.shape[:2]
    band_w, band_h = card.x2 - card.x1, card.y2 - card.y1
    if band_w <= 0 or band_h <= 0:
        return None

    margin_x, margin_y = int(band_w * QUAD_MARGIN), int(band_h * QUAD_MARGIN)
    x1, y1 = max(0, card.x1 - margin_x), max(0, card.y1 - margin_y)
    x2, y2 = min(width, card.x2 + margin_x), min(height, card.y2 + margin_y)
    band = sheet[y1:y2, x1:x2]
    if band.size == 0:
        return None

    patch = max(3, min(band.shape[0], band.shape[1]) // 40)
    background = np.median(
        np.concatenate([
            band[:patch, :patch].reshape(-1, 3), band[:patch, -patch:].reshape(-1, 3),
            band[-patch:, :patch].reshape(-1, 3), band[-patch:, -patch:].reshape(-1, 3),
        ]),
        axis=0,
    )
    distance = np.linalg.norm(band.astype(np.float32) - background, axis=2)
    mask = (distance > QUAD_BACKGROUND_DISTANCE).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) / float(band.shape[0] * band.shape[1]) < QUAD_MIN_AREA_RATIO:
        return None

    quad = _order_quad_points(cv2.boxPoints(cv2.minAreaRect(largest)).astype(np.float32))
    quad_w, quad_h = _quad_dimensions(quad)
    if not (QUAD_MIN_BAND_RATIO <= quad_w / band_w <= QUAD_MAX_BAND_RATIO):
        return None
    if not (QUAD_MIN_BAND_RATIO <= quad_h / band_h <= QUAD_MAX_BAND_RATIO):
        return None

    quad[:, 0] += x1
    quad[:, 1] += y1
    return quad


def _reading_order(cards: list[DetectedCard]) -> list[DetectedCard]:
    """Left-to-right, top-to-bottom by centroid, tolerant of rows that
    don't perfectly line up across columns (see
    `_detect_columns_and_rows`) -- cluster by y using half the median card
    height as the row-break threshold, then sort each cluster by x. Reduces
    to plain row-major order for a well-aligned grid.
    """
    if not cards:
        return []

    heights = [c.y2 - c.y1 for c in cards]
    row_threshold = (sorted(heights)[len(heights) // 2]) * 0.5

    by_y = sorted(cards, key=lambda c: (c.y1 + c.y2) / 2)
    rows: list[list[DetectedCard]] = []
    for card in by_y:
        center_y = (card.y1 + card.y2) / 2
        if rows and abs(center_y - (rows[-1][-1].y1 + rows[-1][-1].y2) / 2) <= row_threshold:
            rows[-1].append(card)
        else:
            rows.append([card])

    ordered: list[DetectedCard] = []
    for row in rows:
        row.sort(key=lambda c: (c.x1 + c.x2) / 2)
        ordered.extend(row)
    return ordered


def _crop_card(sheet: np.ndarray, card: DetectedCard, post_rotation_degrees: int = 0) -> np.ndarray:
    if card.quad is not None:
        # Warp the card's true corners flat, which simultaneously squares up
        # a crooked card and restores a band the variance pass cut short.
        quad_w, quad_h = _quad_dimensions(card.quad)
        target_w, target_h = max(1, int(round(quad_w))), max(1, int(round(quad_h)))
        destination = np.array(
            [[0, 0], [target_w - 1, 0], [target_w - 1, target_h - 1], [0, target_h - 1]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(card.quad, destination)
        cropped = cv2.warpPerspective(sheet, transform, (target_w, target_h))
    else:
        cropped = sheet[card.y1 : card.y2, card.x1 : card.x2]
    # Resize to a canvas shaped the way the card actually lies on the bed
    # (swap width/height for a quarter turn), *then* rotate upright -- going
    # straight to a fixed portrait canvas would scale the two axes unequally
    # and distort the card.
    if post_rotation_degrees % 180 == 90:
        target = (OUTPUT_HEIGHT, OUTPUT_WIDTH)
    else:
        target = (OUTPUT_WIDTH, OUTPUT_HEIGHT)
    resized = cv2.resize(cropped, target, interpolation=cv2.INTER_AREA)
    rotation = _ROTATIONS.get(post_rotation_degrees % 360)
    return cv2.rotate(resized, rotation) if rotation is not None else resized


def _draw_overlay(sheet: np.ndarray, cards: list[DetectedCard]) -> np.ndarray:
    overlay = sheet.copy()
    for index, card in enumerate(cards):
        in_range = MIN_ASPECT_RATIO <= card.aspect_ratio <= MAX_ASPECT_RATIO
        color = (0, 200, 0) if in_range else (0, 0, 220)
        if card.quad is not None:
            # draw what's actually cropped, not the looser band it came from
            cv2.polylines(overlay, [card.quad.astype(np.int32)], True, color, 4)
        else:
            cv2.rectangle(overlay, (card.x1, card.y1), (card.x2, card.y2), color, 4)
        label = f"{index + 1} ar={card.aspect_ratio:.2f}"
        cv2.putText(overlay, label, (card.x1 + 12, card.y1 + 44), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 128, 0), 3)
    return overlay


def detect_cards(
    sheet_path: Path, output_dir: Path, expected_count: int | None = None,
    max_count: int | None = None,
    always_save_overlay: bool = False, post_rotation_degrees: int = 0,
) -> DetectionResult:
    """Detect and crop every card on a scanned sheet by finding the grid of
    content bands (see `_content_bands`) rather than tracing each card's
    outline -- cards sit in a regular grid on a flatbed, and the gaps
    between them are far easier to find reliably than their yellow-on-amber
    borders.

    Cards come back in reading order (left-to-right, top-to-bottom) so
    downstream manual correction can refer to them by position. The grid
    shape is inferred from the scan, so a partly-filled sheet works without
    being told the layout; pass `expected_count` to additionally assert a
    specific number of cards.

    `max_count` is a looser, one-sided version of the same idea: the
    physical bed only ever holds so many cards at once, so a count above
    that ceiling can *only* mean over-fragmentation (bands wrongly split
    into more pieces than there are real cards), never extra cards that
    need explaining -- unlike `expected_count`, which every smaller,
    partly-filled sheet would otherwise fail to match. Flagging it here
    turns a vague "oversized band" warning into a message that says
    outright that the count is impossible, not just unexpected.

    `needs_review` is set (and an annotated overlay saved) when the count
    doesn't match `expected_count`, exceeds `max_count`, when nothing was
    detected, or when any detected cell isn't card-shaped -- the last of
    which catches two cards touching and being read as one band. The first
    three make the whole grid untrustworthy (`structural_issue`); the last
    can be isolated to specific card positions (`misshapen`), so a caller
    can set aside just those crops instead of the whole sheet.

    `post_rotation_degrees` corrects for every card on the sheet being
    placed the same non-upright way (e.g. sideways to fit more per sheet).
    """
    sheet = cv2.imread(str(sheet_path))
    if sheet is None:
        raise ValueError(f"could not read image: {sheet_path}")

    columns = _detect_columns_and_rows(sheet)
    raw_cards = [
        DetectedCard(row=r, col=c, x1=x1, y1=y1, x2=x2, y2=y2)
        for c, ((x1, x2), row_bands) in enumerate(columns)
        for r, (y1, y2) in enumerate(row_bands)
    ]
    cards = _reading_order(_split_merged_cards(raw_cards))
    for card in cards:
        card.quad = _find_card_quad(sheet, card)

    warnings: list[str] = []
    structural_issue = False
    if not cards:
        warnings.append("no cards detected -- is the sheet blank, or the scan very low contrast?")
        structural_issue = True
    if len(cards) != len(raw_cards):
        warnings.append(f"{len(cards) - len(raw_cards)} card(s) were split out of an oversized band -- verify the split landed cleanly")
        structural_issue = True
    misshapen = [index + 1 for index, card in enumerate(cards) if not (MIN_ASPECT_RATIO <= card.aspect_ratio <= MAX_ASPECT_RATIO)]
    if misshapen:
        warnings.append(f"card(s) {misshapen} are not card-shaped -- possibly two cards touching, or a mis-split")
    if expected_count is not None and len(cards) != expected_count:
        warnings.append(f"expected {expected_count} cards, found {len(cards)}")
        structural_issue = True
    if max_count is not None and len(cards) > max_count:
        warnings.append(f"found {len(cards)} cards, more than the {max_count} the bed can physically hold -- this is fragmentation, not extra cards")
        structural_issue = True

    output_dir.mkdir(parents=True, exist_ok=True)
    crop_paths = []
    for index, card in enumerate(cards):
        crop_path = output_dir / f"card{index + 1}.jpg"
        cv2.imwrite(str(crop_path), _crop_card(sheet, card, post_rotation_degrees))
        crop_paths.append(crop_path)

    needs_review = bool(warnings)
    overlay_path = None
    if needs_review or always_save_overlay:
        overlay_path = output_dir / "overlay.jpg"
        cv2.imwrite(str(overlay_path), _draw_overlay(sheet, cards))

    max_rows = max((len(row_bands) for _, row_bands in columns), default=0)
    return DetectionResult(
        crop_paths=crop_paths, cards=cards, rows=max_rows, cols=len(columns),
        count=len(cards), expected_count=expected_count, needs_review=needs_review,
        warnings=warnings, overlay_path=overlay_path,
        misshapen=misshapen, structural_issue=structural_issue,
    )
