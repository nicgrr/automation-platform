"""One-off: commit the 6 cards merged into sheet-20260919-132910-791908's
card2-not-card-shaped.jpg (the bottom two rows of a sheet whose top row
had 2 jumbo cards, throwing off the grid split for everything below it --
see card1's handling in scripts/commit_jumbo_ex_cards.py for the same
sheet's other half). Identified directly by the printed numbers/total
(all /131 -> sv8pt5, Prismatic Evolutions) since the merged crop can't be
auto-identified. User confirmed by eye which one is Normal vs Holo.

    .venv/bin/python -m scripts.commit_merged_6card_sheet
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

CROP = Path("scan_ingest_data/media/needs_review/sheet-20260919-132910-791908-card2-not-card-shaped.jpg")
SET_ID = "sv8pt5"
CONDITION = "Near Mint"

# (number, name, variant) -- Miltank is the one flat/matte card among six,
# per the user's direct visual read of the physical cards.
CARDS = [
    ("80", "Dudunsparce", CardVariant.HOLO),
    ("78", "Noctowl", CardVariant.HOLO),
    ("5", "Leafeon", CardVariant.HOLO),
    ("81", "Miltank", CardVariant.NORMAL),
    ("50", "Riolu", CardVariant.HOLO),
    ("29", "Jolteon", CardVariant.HOLO),
]


def main() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        for number, name, variant in CARDS:
            scan_session = start_or_resume(session, SET_ID, variant, None, user=getpass.getuser(), unattended=True)
            identification = Identification(
                card_id=f"{SET_ID}-{number}", number=number, name=name,
                source=Source.PHASH, confidence=1.0, note="merged multi-card crop, identified by printed number",
            )
            try:
                result = commit_card(
                    session, identification, CROP, Path(settings.scan_media_dir),
                    variant=variant, condition=CONDITION,
                    scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
                )
            except CommitRefused as exc:
                print(f"{name}: error: {exc}")
                continue
            print(f"committed: {result.summary}")
        session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
