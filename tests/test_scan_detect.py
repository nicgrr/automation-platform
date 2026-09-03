import cv2
import numpy as np
import pytest

from automation_control.scan_ingest.detect import OUTPUT_HEIGHT, OUTPUT_WIDTH, detect_cards

# Cards on a real flatbed sit close together with thin, near-uniform gaps
# between them; the detector separates them by pixel variance, so a
# synthetic card has to be *textured* (not a flat fill) to stand in for real
# artwork. Sizes mirror a real 300 DPI scan closely enough to exercise the
# same band/aspect-ratio logic.
CARD_W, CARD_H = 660, 460  # landscape, ratio ~0.697 -- as cards lie on the bed
GAP = 40
MARGIN = 15
BG_COLOR = (169, 224, 233)  # amber/cream, matching a real scanner bed


def _textured_card(seed: int) -> np.ndarray:
    """A card-sized patch of high-variance noise plus a solid ID stripe.
    The noise is what the variance-profile detector keys on; the stripe's
    color identifies which grid cell a crop came from, so reading order can
    be asserted.
    """
    rng = np.random.default_rng(seed)
    card = rng.integers(0, 255, size=(CARD_H, CARD_W, 3), dtype=np.uint8)
    stripe_color = [(seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256]
    card[CARD_H // 2 - 30 : CARD_H // 2 + 30, CARD_W // 2 - 30 : CARD_W // 2 + 30] = stripe_color
    return card


def _stripe_color(crop_path, rotated: bool) -> tuple[int, int, int]:
    crop = cv2.imread(str(crop_path))
    if rotated:
        crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)
    h, w = crop.shape[:2]
    patch = crop[h // 2 - 8 : h // 2 + 8, w // 2 - 8 : w // 2 + 8]
    return tuple(int(v) for v in patch.reshape(-1, 3).mean(axis=0))


def _make_sheet(rows: int, cols: int) -> np.ndarray:
    height = MARGIN * 2 + rows * CARD_H + (rows - 1) * GAP
    width = MARGIN * 2 + cols * CARD_W + (cols - 1) * GAP
    sheet = np.full((height, width, 3), BG_COLOR, dtype=np.uint8)
    for row in range(rows):
        for col in range(cols):
            y = MARGIN + row * (CARD_H + GAP)
            x = MARGIN + col * (CARD_W + GAP)
            sheet[y : y + CARD_H, x : x + CARD_W] = _textured_card(row * cols + col + 1)
    return sheet


def test_detect_cards_infers_grid_without_being_told(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out")

    assert (result.rows, result.cols) == (4, 2)
    assert result.count == 8
    assert result.ok
    assert result.warnings == []
    assert len(result.crop_paths) == 8
    for path in result.crop_paths:
        assert path.exists()


def test_detect_cards_handles_a_partly_filled_sheet(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=2, cols=1))

    result = detect_cards(sheet_path, tmp_path / "out")

    assert (result.rows, result.cols) == (2, 1)
    assert result.count == 2
    assert result.ok


def test_detect_cards_orders_left_to_right_top_to_bottom(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out")

    # cells were seeded row-major (1..8); reading order must reproduce that
    for index, crop_path in enumerate(result.crop_paths):
        seed = index + 1
        expected = ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)
        actual = _stripe_color(crop_path, rotated=False)
        assert all(abs(a - e) < 30 for a, e in zip(actual, expected)), f"crop {seed}: {actual} != {expected}"


def test_detect_cards_flags_review_on_expected_count_mismatch(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=2, cols=1))

    result = detect_cards(sheet_path, tmp_path / "out", expected_count=8)

    assert result.count == 2
    assert result.needs_review
    assert any("expected 8" in w for w in result.warnings)
    assert result.overlay_path is not None and result.overlay_path.exists()


def test_detect_cards_accepts_matching_expected_count(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out", expected_count=8)

    assert result.ok
    assert result.warnings == []


def test_detect_cards_flags_review_on_blank_sheet(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), np.full((1200, 900, 3), BG_COLOR, dtype=np.uint8))

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 0
    assert result.needs_review
    assert any("no cards detected" in w for w in result.warnings)


def test_detect_cards_always_save_overlay_even_when_ok(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out", always_save_overlay=True)

    assert result.ok
    assert result.overlay_path is not None and result.overlay_path.exists()


def test_detect_cards_rotation_yields_upright_portrait_crops(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out", post_rotation_degrees=90)

    for path in result.crop_paths:
        # cards lie landscape on the bed but must come out portrait
        assert cv2.imread(str(path)).shape[:2] == (OUTPUT_HEIGHT, OUTPUT_WIDTH)


def test_detect_cards_rotation_preserves_reading_order(tmp_path):
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=4, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out", post_rotation_degrees=90)

    for index, crop_path in enumerate(result.crop_paths):
        seed = index + 1
        expected = ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)
        actual = _stripe_color(crop_path, rotated=True)
        assert all(abs(a - e) < 30 for a, e in zip(actual, expected)), f"crop {seed}: {actual} != {expected}"


def test_detect_cards_raises_on_unreadable_file(tmp_path):
    with pytest.raises(ValueError, match="could not read image"):
        detect_cards(tmp_path / "nope.png", tmp_path / "out")


# --- freeform placement (hand-placed on a flatbed, not a mechanical feeder) ---
# Confirmed against a real LiDE 300 scan: two columns whose rows didn't line
# up, and two cards in a column touching with literally no gap between them.

def _place_card(sheet: np.ndarray, seed: int, x: int, y: int) -> None:
    sheet[y : y + CARD_H, x : x + CARD_W] = _textured_card(seed)


def test_detect_cards_splits_two_cards_touching_with_no_gap(tmp_path):
    """The exact real-world failure: two cards stacked with zero gap read
    as one band. Its merged aspect ratio can coincidentally still look like
    a plausible single card (confirmed on the real scan), so this must be
    caught by size, not by aspect ratio alone.

    The size check is self-calibrating from *other* correctly-sized cards
    on the same sheet (see `_split_merged_cards`): the "expected" size is a
    median across every detected band, which only reliably excludes an
    outlier once normal-sized bands are the majority -- confirmed on the
    real scan, which had 6 normal cards alongside 2 merged pairs. A sheet
    with too few reference cards (or nothing but merged blobs) isn't
    reliably handled; that's a real, currently-unaddressed limitation, not
    this test's concern, so this uses a realistic reference count.
    """
    width = CARD_W * 2 + MARGIN * 3
    sheet = np.full((CARD_H * 2 + MARGIN * 3, width, 3), BG_COLOR, dtype=np.uint8)
    _place_card(sheet, 1, MARGIN, MARGIN)
    _place_card(sheet, 2, MARGIN, MARGIN + CARD_H)  # touching, no gap
    _place_card(sheet, 3, MARGIN * 2 + CARD_W, MARGIN)  # normal reference cards
    _place_card(sheet, 4, MARGIN * 2 + CARD_W, MARGIN * 2 + CARD_H)
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 4
    assert result.needs_review
    assert any("split" in w for w in result.warnings)
    # the split halves must still each contain the right card, not a
    # 50/50 mix of both
    split_stripes = [_stripe_color(p, rotated=False) for p in result.crop_paths]
    for expected_seed in (1, 2):
        expected = _dominant_stripe(expected_seed)
        assert any(all(abs(a - e) < 30 for a, e in zip(found, expected)) for found in split_stripes), f"seed {expected_seed} not found among {split_stripes}"


def _dominant_stripe(seed: int) -> tuple[int, int, int]:
    return ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)


def _assert_close_color(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> None:
    assert all(abs(a - e) < 30 for a, e in zip(actual, expected)), f"{actual} != {expected}"


def test_detect_cards_handles_columns_with_misaligned_rows(tmp_path):
    """Confirmed against a real scan: the right-hand column sat lower than
    the left. A shared row-band grid (row-bands x column-bands) would
    merge or misalign these; per-column row detection must not."""
    width = CARD_W * 2 + MARGIN * 3
    height = CARD_H * 2 + MARGIN * 3 + 200  # extra room for the offset column
    sheet = np.full((height, width, 3), BG_COLOR, dtype=np.uint8)

    left_x = MARGIN
    right_x = MARGIN * 2 + CARD_W
    _place_card(sheet, 1, left_x, MARGIN)
    _place_card(sheet, 2, left_x, MARGIN * 2 + CARD_H)
    # right column shifted down by 150px relative to the left column
    _place_card(sheet, 3, right_x, MARGIN + 150)
    _place_card(sheet, 4, right_x, MARGIN * 2 + CARD_H + 150)

    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 4
    assert result.ok  # correctly detected despite misalignment -- no review needed
    found_stripes = [_stripe_color(p, rotated=False) for p in result.crop_paths]
    for expected_seed in (1, 2, 3, 4):
        expected = _dominant_stripe(expected_seed)
        assert any(all(abs(a - e) < 30 for a, e in zip(found, expected)) for found in found_stripes), f"seed {expected_seed} not found among {found_stripes}"


def test_detect_cards_does_not_split_a_normally_sized_card(tmp_path):
    """Sanity check on the flip side: a lone, correctly-sized card must
    never get sliced up just because it's the only card on the sheet."""
    sheet = np.full((CARD_H + MARGIN * 2, CARD_W + MARGIN * 2, 3), BG_COLOR, dtype=np.uint8)
    _place_card(sheet, 1, MARGIN, MARGIN)
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 1
    assert result.ok


def test_detect_cards_tolerates_real_scan_variance_near_the_aspect_floor(tmp_path):
    """Confirmed against a real 24-card scan: two genuinely single,
    unmerged cards measured ar=0.599 and ar=0.600 purely from normal
    per-card row-band detection variance (their column neighbors ranged
    0.62-0.69). A too-tight MIN_ASPECT_RATIO flagged them as "not
    card-shaped" and skipped the whole sheet. This reproduces that ratio
    with a card genuinely shorter than its neighbor (not touching, not
    oversized -- the merge detector is the size check, not this one)."""
    shrunk_h = int(CARD_W * 0.599)  # mirrors the real measured ratio
    width = CARD_W + MARGIN * 2
    height = CARD_H * 2 + MARGIN * 3
    sheet = np.full((height, width, 3), BG_COLOR, dtype=np.uint8)

    sheet[MARGIN : MARGIN + CARD_H, MARGIN : MARGIN + CARD_W] = _textured_card(1)[:CARD_H, :CARD_W]
    y2 = MARGIN * 2 + CARD_H
    sheet[y2 : y2 + shrunk_h, MARGIN : MARGIN + CARD_W] = _textured_card(2)[:shrunk_h, :CARD_W]

    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 2
    assert result.ok
    assert result.warnings == []


def _card_with_uniform_strip(seed: int, strip_at: float = 0.45, strip_w: int = 44) -> np.ndarray:
    """A textured card carrying a plain vertical panel, like the text column
    on a real card. That panel is what a variance detector mistakes for the
    empty bed between cards."""
    card = _textured_card(seed)
    x = int(CARD_W * strip_at)
    card[:, x : x + strip_w] = (246, 246, 246)
    return card


def test_detect_cards_rejoins_a_card_split_down_its_own_text_panel(tmp_path):
    """The real failure this guards against: a Paldean Fates sheet on white
    paper split one column of cards into 812px and 1286px pieces with a 94px
    'gap', while the genuine gap between columns was 184px -- so gap size
    can't separate them. Aspect ratio can't either: the pieces scored 0.75
    and 0.80, both inside the card-shaped band.

    Geometry mirrors that scan: one intact column supplying the true card
    width, one column split by its own panel.
    """
    gap = 150
    width = MARGIN * 2 + CARD_W * 2 + gap
    height = MARGIN * 2 + CARD_H
    sheet = np.full((height, width, 3), BG_COLOR, dtype=np.uint8)

    left_x = MARGIN
    right_x = MARGIN + CARD_W + gap
    sheet[MARGIN : MARGIN + CARD_H, left_x : left_x + CARD_W] = _textured_card(1)
    sheet[MARGIN : MARGIN + CARD_H, right_x : right_x + CARD_W] = _card_with_uniform_strip(2)

    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 2, f"expected 2 cards, got {result.count}"
    for card in result.cards:
        w, _ = card.size
        assert abs(w - CARD_W) < CARD_W * 0.15, f"card width {w} is not a whole card ({CARD_W})"


def test_detect_cards_rejoins_rows_split_inside_all_nine_cards(tmp_path):
    """A 3x3 landscape layout can put a low-variance horizontal boundary
    through every card.  With no intact row for calibration, the card width
    must provide the expected full height so 18 halves become nine cards.
    """
    rows, cols = 3, 3
    sheet = _make_sheet(rows, cols)
    strip_y = CARD_H // 2 - 25
    for row in range(rows):
        for col in range(cols):
            y = MARGIN + row * (CARD_H + GAP) + strip_y
            x = MARGIN + col * (CARD_W + GAP)
            sheet[y : y + 50, x : x + CARD_W] = (246, 246, 246)

    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)
    result = detect_cards(sheet_path, tmp_path / "out")

    assert (result.rows, result.cols) == (3, 3)
    assert result.count == 9
    assert result.ok
    for card in result.cards:
        _, height = card.size
        assert abs(height - CARD_H) < CARD_H * 0.15


def test_detect_cards_rejoins_a_lone_split_card_using_evidence_from_another_column(tmp_path):
    """The real failure this guards against: an 11-card irregular sheet
    where one column held a single card (its own text panel splitting it
    into two row fragments) while a *different* column had three intact,
    genuinely gapped cards to calibrate height from. The row-rejoin logic
    used to gate on "this column has 3+ bands of its own", which a lone
    split card can never satisfy -- the fix trusts sheet-wide evidence
    instead, since the height it establishes doesn't depend on which
    column supplied it.
    """
    rows, cols = 3, 2
    sheet = _make_sheet(rows, cols)
    # Column 0 keeps its three genuine, gapped cards untouched -- that's
    # the trustworthy evidence. Column 1's single top card (the other two
    # cells are cleared to background) gets a low-variance strip through
    # its own middle, mirroring a card's own text panel.
    top_x = MARGIN + CARD_W + GAP
    sheet[MARGIN : MARGIN + CARD_H, top_x : top_x + CARD_W] = _textured_card(99)
    strip_y = MARGIN + CARD_H // 2 - 25
    sheet[strip_y : strip_y + 50, top_x : top_x + CARD_W] = (246, 246, 246)
    for row in (1, 2):
        y = MARGIN + row * (CARD_H + GAP)
        sheet[y : y + CARD_H, top_x : top_x + CARD_W] = BG_COLOR

    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), sheet)
    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 4, f"expected 4 cards (3 + the split one rejoined), got {result.count}"
    for card in result.cards:
        _, height = card.size
        assert abs(height - CARD_H) < CARD_H * 0.15, f"card height {height} is not a whole card ({CARD_H})"


def test_detect_cards_still_separates_two_genuinely_adjacent_cards(tmp_path):
    """The flip side: rejoining must never fuse two real cards. Two whole
    cards with a normal gap stay two cards."""
    sheet_path = tmp_path / "sheet.png"
    cv2.imwrite(str(sheet_path), _make_sheet(rows=1, cols=2))

    result = detect_cards(sheet_path, tmp_path / "out")

    assert result.count == 2
    for card in result.cards:
        w, _ = card.size
        assert abs(w - CARD_W) < CARD_W * 0.15


def test_predict_card_width_refuses_to_guess_without_support(tmp_path):
    """A wrong width prediction would fuse real cards, so with no band
    matching either candidate the detector must decline to merge."""
    from automation_control.scan_ingest.detect import _predict_card_width

    # widths nowhere near height/0.716 (=1397) or height*0.716 (=716)
    assert _predict_card_width([200, 210], median_height=1000) is None
    # ...and a supported prediction is returned
    assert _predict_card_width([1400, 1390], median_height=1000) is not None
