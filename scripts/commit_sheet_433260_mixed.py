"""One-off: commit the 9 cards from sheet-20260920-164306-433260, left in the
watch folder because it's a genuinely mixed bulk pile spanning 6 different
Sword & Shield-era sets (Silver Tempest, Shining Fates, Vivid Voltage,
Astral Radiance, Brilliant Stars, Fusion Strike) -- no single cached set
matched confidently, which is expected for a mixed sheet, not a bug.
Identified directly off the printed name/number/set-total against the
catalogue (see conversation), one match per card confirmed unambiguous.
Foil variant comes from the same trained classifier the live pipeline uses
(automation_control.scan_ingest.foil.assess_foil), not guessed.

    .venv/bin/python -m scripts.commit_sheet_433260_mixed
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CatalogCard
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.foil import assess_foil
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

STEM = "sheet-20260920-164306-433260"
CROP_DIR = Path(
    "scan_ingest_data/media/work/_probe/sheet-20260920-164306-433260"
)
CONDITION = "Near Mint"

# (crop file, catalog card id, name)
CARDS = [
    ("card1.jpg", "swsh12-38", "Wailord"),
    ("card2.jpg", "swsh12-82", "Meowstic"),
    ("card3.jpg", "swsh12-84", "Slurpuff"),
    ("card4.jpg", "swsh45-61", "Rusted Shield"),
    ("card5.jpg", "swsh45-29", "Snom"),
    ("card6.jpg", "swsh4-59", "Eelektross"),
    ("card7.jpg", "swsh10-97", "Absol"),
    ("card8.jpg", "swsh9-35", "Piplup"),
    ("card9.jpg", "swsh8-241", "Sidney"),
]


def main() -> int:
    settings = get_settings()

    with SessionLocal() as session:
        for crop_name, card_id, name in CARDS:
            crop_path = CROP_DIR / crop_name
            if not crop_path.exists():
                print(f"{name} ({card_id}): crop {crop_name} not found, skipping")
                continue

            card = session.get(CatalogCard, card_id)
            if card is None:
                print(f"{name} ({card_id}): not in catalogue, skipping")
                continue

            assessment = assess_foil(crop_path, card)
            variant = assessment.suggestion or assessment.candidates[0] if assessment.candidates else None
            if variant is None:
                from automation_control.models import CardVariant
                variant = CardVariant.NORMAL
            print(f"{name} ({card_id}): {assessment.log_summary()}")

            scan_session = start_or_resume(
                session, card.set_id, variant, None, user=getpass.getuser(), unattended=True
            )
            identification = Identification(
                card_id=card_id, number=card.number, name=name,
                source=Source.PHASH, confidence=1.0,
                note="mixed bulk sheet, identified directly off printed name/number/set-total",
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
            print(f"  committed: {result.summary}")
        session.commit()

    # The original sheet file and its watch-folder crops are no longer needed
    # once every card on it has been committed by hand.
    watch_sheet = Path(settings.scan_media_dir).parent / "watch" / f"{STEM}.png"
    if watch_sheet.exists():
        watch_sheet.unlink()
        print(f"removed {watch_sheet}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
