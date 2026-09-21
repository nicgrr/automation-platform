"""One-off: commit the 7 cards from sheet-20260919-135809-847008 that
couldn't auto-identify because they belong to different sets than the one
the sheet matched against (Temporal Forces/sv5 -- only 2 of the 9 cards
are actually from that set; the rest are 151/sv3pt5 and Destined
Rivals/sv10, a genuinely mixed-set sheet). Identified directly by the
printed numbers off the overlay image. All 9 cards on this sheet show the
same visual foil pattern (textured border/background, flat character art)
as the 2 that already auto-matched as Reverse Holo, so all 7 are filed the
same way.

    .venv/bin/python -m scripts.commit_sheet_847008_remainder
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

STEM = "sheet-20260919-135809-847008"
CONDITION = "Near Mint"
VARIANT = CardVariant.REVERSE_HOLO

# (set_id, number, name, crop file)
CARDS = [
    ("sv3pt5", "119", "Seaking", f"{STEM}-card1-unidentified.jpg"),
    ("sv10", "87", "Team Rocket's Mimikyu", f"{STEM}-card4-unidentified.jpg"),
    ("sv3pt5", "22", "Fearow", f"{STEM}-card5-unidentified.jpg"),
    ("sv3pt5", "2", "Ivysaur", f"{STEM}-card6-unidentified.jpg"),
    ("sv10", "91", "Primeape", f"{STEM}-card7-unidentified.jpg"),
    ("sv10", "60", "Abomasnow", f"{STEM}-card8-unidentified.jpg"),
    ("sv10", "2", "Yanma", f"{STEM}-card9-unidentified.jpg"),
]


def main() -> int:
    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"

    with SessionLocal() as session:
        for set_id, number, name, crop_name in CARDS:
            crop_path = review_dir / crop_name
            if not crop_path.exists():
                print(f"{name} #{number}: crop {crop_name} not found, skipping")
                continue
            scan_session = start_or_resume(session, set_id, VARIANT, None, user=getpass.getuser(), unattended=True)
            identification = Identification(
                card_id=f"{set_id}-{number}", number=number, name=name,
                source=Source.PHASH, confidence=1.0,
                note="mixed-set sheet, identified by printed number against the correct set",
            )
            try:
                result = commit_card(
                    session, identification, crop_path, Path(settings.scan_media_dir),
                    variant=VARIANT, condition=CONDITION,
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
