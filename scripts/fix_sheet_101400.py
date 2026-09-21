"""One-off: sheet-20260916-151730-101400 (Prismatic Evolutions, sv8pt5) --
user confirmed the real per-card variant for all 9 positions after a
reprocess run had already committed 4 of them under the wrong base variant
(normal) and set the other 5 aside for review. This corrects the 4 wrong
commits and commits the other 5 directly (their numbers/names were already
read cleanly off the sheet image, only the auto-identification was
failing -- see the sheet's foil sheen, established this session as the
cause of the phash mismatch).

Grid (row-major, matches detect.py's ordering):
  Earthen Vessel #106 (Holo)   | Carmine #103 (Reverse)     | Roto-Stick #127 (Holo)
  Roaring Moon #65 (Holo)      | Vaporeon #22 (Holo)        | Great Tusk #55 (Holo)
  Explorer's Guidance #107 (Holo) | Riolu #50 (Reverse)     | Dreepy #71 (Reverse)

    .venv/bin/python -m scripts.fix_sheet_101400
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard, InventoryItem
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume
from sqlalchemy import select

SET_ID = "sv8pt5"
CONDITION = "Near Mint"
SHEET_STEM = "sheet-20260916-151730-101400"

# Cards already (wrongly) committed as Normal -- move to the right variant.
WRONG_NORMAL_FIXES = [
    ("103", "Carmine", CardVariant.REVERSE_HOLO),
    ("22", "Vaporeon", CardVariant.HOLO),
    ("55", "Great Tusk", CardVariant.HOLO),
    ("107", "Explorer's Guidance", CardVariant.HOLO),
]

# Cards still sitting in needs_review -- commit directly by number, crop
# file name from the reprocess run's "(unidentified)" set-asides.
REVIEW_COMMITS = [
    ("106", "Earthen Vessel", CardVariant.HOLO, f"{SHEET_STEM}-card1-unidentified.jpg"),
    ("127", "Roto-Stick", CardVariant.HOLO, f"{SHEET_STEM}-card3-unidentified.jpg"),
    ("65", "Roaring Moon", CardVariant.HOLO, f"{SHEET_STEM}-card4-unidentified.jpg"),
    ("71", "Dreepy", CardVariant.REVERSE_HOLO, f"{SHEET_STEM}-card9-unidentified.jpg"),
    # The classifier already correctly overrode this to reverse_holo during
    # reprocess (logged at 1.00 confidence) before it hit the confirmation
    # gate on identity -- but _set_aside_for_review has no field to persist
    # that override, so leaving this for manual /review would silently
    # lose it (that page's printing dropdown defaults to Normal with no
    # memory of the override). Committing directly instead.
    ("50", "Riolu", CardVariant.REVERSE_HOLO, f"{SHEET_STEM}-card8-50-Riolu.jpg"),
]


def main() -> int:
    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"

    with SessionLocal() as session:
        for number, name, correct_variant in WRONG_NORMAL_FIXES:
            card = session.scalar(select(CatalogCard).where(CatalogCard.set_id == SET_ID, CatalogCard.number == number))
            wrong_row = session.scalar(
                select(InventoryItem).where(InventoryItem.card_id == card.id, InventoryItem.variant == CardVariant.NORMAL)
            )
            if wrong_row is None or wrong_row.quantity < 1:
                print(f"{name} #{number}: no Normal unit found, skipping")
                continue
            if wrong_row.quantity == 1:
                session.delete(wrong_row)
            else:
                wrong_row.quantity -= 1
            correct_row = session.scalar(
                select(InventoryItem).where(InventoryItem.card_id == card.id, InventoryItem.variant == correct_variant)
            )
            if correct_row is None:
                correct_row = InventoryItem(card_id=card.id, variant=correct_variant, condition=CONDITION, quantity=0)
                session.add(correct_row)
            correct_row.quantity += 1
            print(f"{name} #{number}: Normal -> {correct_variant.value} (now {correct_row.quantity})")
        session.commit()

        for number, name, variant, crop_name in REVIEW_COMMITS:
            crop_path = review_dir / crop_name
            if not crop_path.exists():
                print(f"{name} #{number}: crop {crop_name} not found, skipping")
                continue
            scan_session = start_or_resume(session, SET_ID, variant, None, user=getpass.getuser(), unattended=True)
            identification = Identification(
                card_id=f"{SET_ID}-{number}", number=number, name=name,
                source=Source.PHASH, confidence=1.0, note="identified by printed number, foil sheen broke phash",
            )
            try:
                result = commit_card(
                    session, identification, crop_path, Path(settings.scan_media_dir),
                    variant=variant, condition=CONDITION,
                    scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
                )
            except CommitRefused as exc:
                print(f"{name}: error: {exc}")
                continue
            print(f"committed: {result.summary}")
            crop_path.unlink(missing_ok=True)
        session.commit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
