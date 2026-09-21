"""One-off: confirm the review-queue crops that already carry a phash/OCR
name suggestion (confidence 0.5-0.74, below the pipeline's auto-commit bar
but clearly correct on inspection -- verified by cross-checking one
"conflict"-sourced case (Garbodor) against its printed number directly).
Saves the user from re-confirming these one at a time in /review.

    .venv/bin/python -m scripts.confirm_named_review_batch
"""

import getpass
from pathlib import Path

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardVariant, CatalogCard
from automation_control.scan_ingest.commit import CommitRefused, commit_card
from automation_control.scan_ingest.identify import Identification, Source
from automation_control.scan_ingest.session import start_or_resume

CONDITION = "Near Mint"

# (crop filename, set_id, number, name, variant)
CARDS = [
    ("sheet-20260920-163442-888446-card7-48-Phanpy.jpg", "sv4pt5", "48", "Phanpy", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-163700-397639-card4-36-Dedenne.jpg", "sv4pt5", "36", "Dedenne", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-164437-460250-card4-72-Tranquill.jpg", "zsv10pt5", "72", "Tranquill", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-164437-460250-card5-50-Throh.jpg", "zsv10pt5", "50", "Throh", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-165044-359807-card6-113-Grapploct.jpg", "sv8", "113", "Grapploct", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-165207-425505-card8-8-Morelull.jpg", "sv8", "8", "Morelull", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-165656-188400-card2-3-Leavanny.jpg", "rsv10pt5", "3", "Leavanny", CardVariant.REVERSE_HOLO),
    ("sheet-20260920-165656-188400-card6-60-Garbodor.jpg", "rsv10pt5", "60", "Garbodor", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-090952-717638-card9-39-Yamask.jpg", "rsv10pt5", "39", "Yamask", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-091531-904876-card6-93-Florges.jpg", "sv1", "93", "Florges", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-091531-904876-card8-51-Bruxish.jpg", "sv1", "51", "Bruxish", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-091725-137110-card3-174-Exp. Share.jpg", "sv1", "174", "Exp. Share", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-091923-583467-card5-89-Drifloon.jpg", "sv1", "89", "Drifloon", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-092322-655021-card5-162-Squawkabilly.jpg", "sv1", "162", "Squawkabilly", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-092322-655021-card8-187-Pokémon Catcher.jpg", "sv1", "187", "Pokémon Catcher", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-093447-614420-card2-66-Goomy.jpg", "me4", "66", "Goomy", CardVariant.REVERSE_HOLO),
    ("sheet-20260921-110415-556368-card3-63-Ferrothorn.jpg", "me4", "63", "Ferrothorn", CardVariant.HOLO),
    ("sheet-20260921-110614-675708-card1-85-Magnetic Metal Energy.jpg", "me4", "85", "Magnetic Metal Energy", CardVariant.HOLO),
]


def main() -> int:
    settings = get_settings()
    review_dir = Path(settings.scan_media_dir) / "needs_review"

    with SessionLocal() as session:
        for crop_name, set_id, number, name, variant in CARDS:
            crop_path = review_dir / crop_name
            if not crop_path.exists():
                print(f"{name} ({set_id}-{number}): crop not found, skipping")
                continue

            card_id = f"{set_id}-{number}"
            card = session.get(CatalogCard, card_id)
            if card is None or card.name != name:
                print(f"{name}: no exact catalog match at {card_id} (got {card.name if card else None}), skipping")
                continue

            scan_session = start_or_resume(session, set_id, variant, None, user=getpass.getuser(), unattended=True)
            identification = Identification(
                card_id=card_id, number=number, name=name,
                source=Source.PHASH, confidence=1.0,
                note="confirmed from the pipeline's own low-confidence phash/OCR suggestion",
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
