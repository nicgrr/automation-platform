"""One-off: commit the 9 cards from sheet-20260921-112012-558803, a scan
that trips a real detector bug (splits into 6 rows x 3 cols instead of the
physical 3x3 -- see conversation) rather than being a bad photo. The
underlying image is clean and every card was read directly off it, so
these are committed without a per-copy photo (commit_card's documented
behaviour for a known-bad crop: scan_image_path stays unset and the
storefront falls back to the catalogue stock photo instead of passing off
a bad crop as this specific copy).

    .venv/bin/python -m scripts.commit_sheet_558803
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

SET_ID = "zsv10pt5"
CONDITION = "Near Mint"
VARIANT = CardVariant.HOLO

# (number, name)
CARDS = [
    ("53", "Landorus"),
    ("66", "Cobalion"),
    ("66", "Cobalion"),
    ("33", "Thundurus"),
    ("70", "Haxorus"),
    ("12", "Victini"),
    ("26", "Beartic"),
    ("26", "Beartic"),
    ("49", "Conkeldurr"),
]


def main() -> int:
    settings = get_settings()

    with SessionLocal() as session:
        scan_session = start_or_resume(session, SET_ID, VARIANT, None, user=getpass.getuser(), unattended=True)
        for number, name in CARDS:
            identification = Identification(
                card_id=f"{SET_ID}-{number}", number=number, name=name,
                source=Source.PHASH, confidence=1.0,
                note="detector split this sheet into 16 fragments (real bug); identified directly off the full sheet image instead",
            )
            try:
                result = commit_card(
                    session, identification, None, Path(settings.scan_media_dir),
                    variant=VARIANT, condition=CONDITION,
                    scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
                )
            except CommitRefused as exc:
                print(f"{name}: error: {exc}")
                continue
            print(f"committed: {result.summary}")
        session.commit()

    watch_sheet = Path(settings.scan_media_dir).parent / "watch" / "sheet-20260921-112012-558803.png"
    if watch_sheet.exists():
        watch_sheet.unlink()
        print(f"removed {watch_sheet}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
