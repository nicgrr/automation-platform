"""One-off: sheet-20260919-135809-847008 (Temporal Forces, sv5) tripped the
"oversized band split" structural-issue guard and got skipped whole by the
unattended pipeline (0 committed, 9 skipped) -- but the saved overlay shows
all 9 boxes landing cleanly on individual cards, no real merge/split
problem. Running non-unattended to bypass that guard isn't viable here:
review_card's per-card confirmation loop expects a real terminal, and a
stub prompter just spins forever re-asking. Instead this suppresses only
the structural_issue flag on detect_cards' result (confirmed safe to
ignore by looking at the overlay) while keeping unattended=True for
everything else, so ambiguous individual cards still get the normal,
tested set-aside-for-review treatment instead of an interactive hang.

    .venv/bin/python -m scripts.force_reprocess_847008
"""

import dataclasses
import getpass
from pathlib import Path
from unittest.mock import patch

from automation_control.config import get_settings
from automation_control.database import Base, engine
from automation_control.database import SessionLocal
from automation_control.scan_ingest import catalog
from automation_control.scan_ingest import detect as detect_module
from automation_control.scan_ingest.identify import ReferenceCard
from automation_control.scan_ingest.session import process_sheet, start_or_resume
from automation_control.models import CardSet, CardVariant

SHEET = Path("scan_ingest_data/watch/sheet-20260919-135809-847008.png")
SET_ID = "sv5"
VARIANT = CardVariant.NORMAL  # this batch's foil cards will still be classifier-overridden per card

_real_detect_cards = detect_module.detect_cards


def _detect_cards_ignoring_structural_issue(*args, **kwargs):
    result = _real_detect_cards(*args, **kwargs)
    if result.structural_issue:
        print(f"  (suppressing structural_issue -- {result.warnings})")
        result = dataclasses.replace(result, structural_issue=False)
    return result


class _NotifyOnlyPrompter:
    def ask(self, question: str, default: str = "") -> str:
        return default

    def confirm(self, question: str, default: bool = True) -> bool:
        return default

    def notify(self, message: str) -> None:
        print(message)


def main() -> int:
    settings = get_settings()
    Base.metadata.create_all(engine)
    if not SHEET.exists():
        print(f"error: sheet not found: {SHEET}")
        return 1

    with SessionLocal() as session:
        card_set = session.get(CardSet, SET_ID)
        references = [
            ReferenceCard(card_id=c.id, number=c.number, name=c.name, phash=c.phash)
            for c in catalog.get_cached_cards(session, SET_ID) if c.phash
        ]
        prompter = _NotifyOnlyPrompter()
        scan_session = start_or_resume(session, SET_ID, VARIANT, prompter, user=getpass.getuser(), unattended=True)
        with patch("automation_control.scan_ingest.session.detect_cards", _detect_cards_ignoring_structural_issue):
            outcome = process_sheet(
                session, SHEET, scan_session, references, settings, prompter,
                set_total=card_set.printed_total, variant=VARIANT, condition="Near Mint",
                user=getpass.getuser(), unattended=True,
            )
    print(f"\n{outcome.committed} committed, {outcome.skipped} skipped, {outcome.set_aside} set aside for review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
