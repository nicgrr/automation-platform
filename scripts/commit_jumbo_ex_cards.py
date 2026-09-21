"""One-off: commit the 2 jumbo ex cards (Leafeon ex #6, Wellspring Mask
Ogerpon ex #27) found merged into one crop on sheet
sheet-20260919-132910-791908 (card1-not-card-shaped.jpg) -- same jumbo
oversized-card detection issue as the original svp Charizard/Zapdos jumbos
from the start of this project: the fixed grid assumption doesn't fit an
oversized card, but the printed number was legible directly off the scan,
so this identifies by number instead of guessing from the mangled crop.

    .venv/bin/python -m scripts.commit_jumbo_ex_cards
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

CROP = Path("scan_ingest_data/media/needs_review/sheet-20260919-132910-791908-card1-not-card-shaped.jpg")
SET_ID = "sv8pt5"
CARDS = [("6", "Leafeon ex"), ("27", "Wellspring Mask Ogerpon ex")]
VARIANT = CardVariant.HOLO
CONDITION = "Near Mint"


def main() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        scan_session = start_or_resume(session, SET_ID, VARIANT, None, user=getpass.getuser(), unattended=True)
        for number, name in CARDS:
            identification = Identification(
                card_id=f"{SET_ID}-{number}", number=number, name=name,
                source=Source.PHASH, confidence=1.0, note="jumbo card, identified by printed number",
            )
            try:
                result = commit_card(
                    session, identification, CROP, Path(settings.scan_media_dir),
                    variant=VARIANT, condition=CONDITION,
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
