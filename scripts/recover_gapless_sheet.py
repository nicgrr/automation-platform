"""Recover a sheet whose cards were placed with zero gap between them --
`detect_cards`'s gap-based grid inference has no boundary to find in that
case (confirmed against sheet-20260906-124651-567809.png: variance between
expected column/row boundaries never dips at all, even at the fallback
threshold) and there's no other card on the sheet sized correctly enough to
serve as a reference for the merge-splitting safety net either. That's a
placement problem, not a detection bug -- rescanning with a visible gap is
the real fix -- but when the physical cards are no longer available to
rescan, this instead assumes a perfectly even `--rows` x `--cols` grid across
the sheet's outer content bounds and runs the normal identify/commit
pipeline against those cells.

    .venv/bin/python -m scripts.recover_gapless_sheet \
        --sheet scan_ingest_data/watch/sheet-20260906-124651-567809.png \
        --set-id sv10 --rotate 180 --unattended
"""
import argparse
import getpass
import sys
from pathlib import Path

import cv2

from automation_control.database import Base, SessionLocal, engine
from automation_control.models import CardSet, CardVariant
from automation_control.scan_ingest import catalog, detect
from automation_control.scan_ingest import session as session_module
from automation_control.scan_ingest.detect import DetectedCard, DetectionResult
from automation_control.scan_ingest.identify import ReferenceCard
from automation_control.scan_ingest.session import ConsolePrompter, process_sheet, start_or_resume
from automation_control.config import get_settings


def _grid_detect(
    sheet_path: Path, output_dir: Path, expected_count: int | None = None,
    max_count: int | None = None, always_save_overlay: bool = False,
    post_rotation_degrees: int = 0, rows: int = 3, cols: int = 3,
) -> DetectionResult:
    sheet = cv2.imread(str(sheet_path))
    gray = cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]

    col_bands = detect._content_bands(gray.std(axis=0), int(width * detect.MIN_BAND_FRACTION))
    row_bands = detect._content_bands(gray.std(axis=1), int(height * detect.MIN_BAND_FRACTION))
    x1, x2 = min(b[0] for b in col_bands), max(b[1] for b in col_bands)
    y1, y2 = min(b[0] for b in row_bands), max(b[1] for b in row_bands)
    cell_w, cell_h = (x2 - x1) / cols, (y2 - y1) / rows

    output_dir.mkdir(parents=True, exist_ok=True)
    cards: list[DetectedCard] = []
    crop_paths: list[Path] = []
    for r in range(rows):
        for c in range(cols):
            card = DetectedCard(
                row=r, col=c,
                x1=int(round(x1 + c * cell_w)), x2=int(round(x1 + (c + 1) * cell_w)),
                y1=int(round(y1 + r * cell_h)), y2=int(round(y1 + (r + 1) * cell_h)),
            )
            card.quad = detect._find_card_quad(sheet, card)
            cropped = detect._crop_card(sheet, card, post_rotation_degrees=post_rotation_degrees)
            index = r * cols + c + 1
            path = output_dir / f"card{index}.jpg"
            cv2.imwrite(str(path), cropped)
            cards.append(card)
            crop_paths.append(path)

    return DetectionResult(
        crop_paths=crop_paths, cards=cards, rows=rows, cols=cols, count=len(cards),
        expected_count=expected_count, needs_review=False, warnings=[], misshapen=[], structural_issue=False,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sheet", required=True, help="path to the sheet image (watch or archive folder)")
    parser.add_argument("--set-id", required=True, help="the cached set to identify against, e.g. sv10")
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--variant", default=CardVariant.NORMAL.value, choices=[v.value for v in CardVariant])
    parser.add_argument("--condition", default="Near Mint")
    parser.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="degrees clockwise to make crops upright")
    parser.add_argument("--unattended", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    sheet_path = Path(args.sheet)
    if not sheet_path.exists():
        print(f"error: sheet not found: {sheet_path}", file=sys.stderr)
        return 1

    Base.metadata.create_all(engine)
    prompter = ConsolePrompter()
    with SessionLocal() as db:
        if not catalog.is_cached(db, args.set_id):
            print(f"error: set {args.set_id!r} is not cached.", file=sys.stderr)
            return 1
        card_set = db.get(CardSet, args.set_id)
        references = [
            ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash)
            for c in catalog.get_cached_cards(db, args.set_id) if c.phash
        ]
        if not references:
            print(f"error: set {args.set_id!r} has no reference hashes.", file=sys.stderr)
            return 1

        variant = CardVariant(args.variant)
        scan_session = start_or_resume(db, args.set_id, variant, prompter, user=getpass.getuser(), unattended=True)

        original_detect_cards = session_module.detect_cards
        session_module.detect_cards = lambda sheet_path, output_dir, **kw: _grid_detect(
            sheet_path, output_dir, rows=args.rows, cols=args.cols,
            expected_count=kw.get("expected_count"), post_rotation_degrees=kw.get("post_rotation_degrees", 0),
        )
        try:
            outcome = process_sheet(
                db, sheet_path, scan_session, references, settings, prompter,
                set_total=card_set.printed_total, variant=variant, condition=args.condition,
                post_rotation_degrees=args.rotate, user=getpass.getuser(), unattended=args.unattended,
            )
        finally:
            session_module.detect_cards = original_detect_cards

    print(f"\n{outcome.committed} committed, {outcome.skipped} skipped, {outcome.set_aside} set aside for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
