"""One-off: commit review-queue crops identified by eye against the catalog,
reading from a CSV of (crop filename, set_id, number, name, variant) built up
during a manual review-queue sweep. See conversation for how each row was
identified. Variant is read straight off the physical card via the trained
classifier (assess_foil), same as the live pipeline -- the CSV's own variant
column is only a fallback used when assess_foil can't reach a confident
verdict (single-catalogue-printing cards, energy/trainer cards with no foil
question, or a damaged/unreadable crop).

    .venv/bin/python -m scripts.commit_review_batch <csv_path>
"""

import csv
import getpass
import sys
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.foil import assess_foil
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

CONDITION = "Near Mint"


def main(csv_path: str) -> int:
    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    with SessionLocal() as session:
        for row in rows:
            crop_name = row["crop"]
            set_id = row["set_id"]
            number = row["number"]
            name = row["name"]
            fallback_variant = CardVariant(row["variant"])

            crop_path = review_dir / crop_name
            if not crop_path.exists():
                print(f"{name} ({set_id}-{number}): crop not found, skipping")
                continue

            card_id = f"{set_id}-{number}"
            card = session.get(CatalogCard, card_id)
            if card is None or card.name != name:
                print(f"{name}: no exact catalog match at {card_id} (got {card.name if card else None}), skipping")
                continue

            assessment = assess_foil(crop_path, card)
            variant = assessment.suggestion or (assessment.candidates[0] if assessment.candidates else fallback_variant)

            scan_session = start_or_resume(session, set_id, variant, None, user=getpass.getuser(), unattended=True)
            identification = Identification(
                card_id=card_id, number=number, name=name,
                source=Source.PHASH, confidence=1.0,
                note="identified by eye during a manual review-queue sweep",
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
    raise SystemExit(main(sys.argv[1]))
