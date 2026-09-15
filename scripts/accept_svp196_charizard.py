"""One-off: accept the jumbo Charizard ex (svp #196) recropped out of the
review queue (sheet-20260911-161856-884634, card 1) -- mirrors exactly what
/review/decide does for an Accept click with pick=svp:196, variant=normal,
condition=Near Mint, since the user asked to finish this one from the CLI
instead of tapping through the web form.

    .venv/bin/python -m scripts.accept_svp196_charizard
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

CROP = Path("scan_ingest_data/media/needs_review/sheet-20260911-161856-884634-card1-not-card-shaped.jpg")
SET_ID = "svp"
NUMBER = "196"
NAME = "Charizard ex"
VARIANT = CardVariant.NORMAL
CONDITION = "Near Mint"


def main() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        scan_session = start_or_resume(session, SET_ID, VARIANT, None, user=getpass.getuser(), unattended=True)
        identification = Identification(
            card_id=f"{SET_ID}-{NUMBER}", number=NUMBER, name=NAME,
            source=Source.PHASH, confidence=1.0, note="confirmed via recrop, accepted from CLI",
        )
        try:
            result = commit_card(
                session, identification, CROP, Path(settings.scan_media_dir),
                variant=VARIANT, condition=CONDITION,
                scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
            )
        except CommitRefused as exc:
            print(f"error: {exc}")
            return 1
        session.commit()

    CROP.unlink(missing_ok=True)
    print(f"committed: {result.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
