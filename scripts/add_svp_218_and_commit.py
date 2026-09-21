"""One-off: svp-218 "Team Rocket's Persian ex" is a promo newer than what
pokemontcg.io's SVP catalogue has indexed (re-caching with --force still
only reaches #200 -- see conversation), so it has no CatalogCard row at
all. Insert a minimal one by hand (matching the fields every other SVP
card has) so the held copy can be committed to inventory normally, then
commit it. No phash/art_phash/reference image -- there's no upstream image
to hash against yet; if pokemontcg.io indexes this card later, re-running
`cache-set --force` will fill those in without disturbing this row's id.

    .venv/bin/python -m scripts.add_svp_218_and_commit
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

SET_ID = "svp"
NUMBER = "218"
NAME = "Team Rocket's Persian ex"
CROP_NAME = "sheet-20260921-125338-258374-card7-unidentified.jpg"
CONDITION = "Near Mint"
VARIANT = CardVariant.HOLO


def main() -> int:
    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"
    crop_path = review_dir / CROP_NAME

    with SessionLocal() as session:
        card_id = f"{SET_ID}-{NUMBER}"
        if session.get(CatalogCard, card_id) is None:
            session.add(CatalogCard(
                id=card_id, set_id=SET_ID, number=NUMBER, name=NAME,
                rarity="Promo", supertype="Pokémon",
            ))
            session.flush()
            print(f"inserted catalog row {card_id}")

        scan_session = start_or_resume(session, SET_ID, VARIANT, None, user=getpass.getuser(), unattended=True)
        identification = Identification(
            card_id=card_id, number=NUMBER, name=NAME,
            source=Source.PHASH, confidence=1.0,
            note="promo newer than pokemontcg.io's cached SVP catalogue; identified directly and added by hand",
        )
        try:
            result = commit_card(
                session, identification, crop_path if crop_path.exists() else None,
                Path(settings.scan_media_dir),
                variant=VARIANT, condition=CONDITION,
                scan_session=scan_session, user=getpass.getuser(), allow_unconfirmed=True,
            )
        except CommitRefused as exc:
            print(f"error: {exc}")
            return 1
        print(f"committed: {result.summary}")
        if crop_path.exists():
            crop_path.unlink()
        session.commit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
