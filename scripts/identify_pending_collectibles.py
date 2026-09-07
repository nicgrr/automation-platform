"""Re-identify pending collectible captures from their own cropped photo.

The first pass read forty-odd figures out of one wide cabinet shot, which
is a lot to ask of a single look: attention is spread across the whole
shelf and the per-figure detail suffers. Twenty-six of those rows came
back with no series and nineteen with no character.

Each row now has its own crop, so this asks again -- one figure per call,
with the figure filling the frame. Same model, much narrower question.

It also passes the known series list as context. Sonny Angel motifs
repeat across series (there is a bear in several), so "which series is
this" is closer to matching against a known set than open recall. The
prompt is explicit that a figure outside the list should be named
anyway, so this narrows the search without forcing a wrong fit.

Only ever writes to captured_collectibles, and only to rows still
PENDING_REVIEW -- confirming into the catalogue stays a human step.

    .venv/bin/python -m scripts.identify_pending_collectibles --dry-run
    .venv/bin/python -m scripts.identify_pending_collectibles --limit 5
    .venv/bin/python -m scripts.identify_pending_collectibles --only-blank
"""

import argparse
import time
from pathlib import Path

from sqlalchemy import select

from automation_control.card_recognition import RecognitionError, extract_collectible_details
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CapturedCollectible, CardCaptureStatus

CALL_DELAY_SECONDS = 0.3

SERIES_CONTEXT = (
    "For reference, Sonny Angel's regular series are: Animal Series 1, Animal "
    "Series 2, Animal Series 3, Animal Series 4, Marine Series, Fruit Series, "
    "Vegetable Series, Sweets Series, Flower Series, Hippers Animal Series and "
    "Hippers Harvest Series. There are also many limited and seasonal lines "
    "(for example Summer Vacation, Christmas, Halloween, Easter, Sleeping "
    "Angel, Fast Food, and various collaborations). Prefer a series from the "
    "regular list when the figure clearly belongs to one, but if it is plainly "
    "from a limited or seasonal line, name that line instead -- do not force a "
    "figure into the regular list. If you genuinely cannot tell which series "
    "it is, put series in unreadable_fields rather than guessing."
)

FIELDS = ("brand", "series", "character", "variant", "blind_box_series")

# Series names have to match each other exactly to group or price
# together, and the model returns them however the packaging words it --
# "Summer Vacation" and "Summer Vacation Series" are the same line, and
# "CRYBABY" and "Crybaby" are the same thing shouted.
_SERIES_ALIASES = {
    "summer vacation": "Summer Vacation Series",
    "crybaby": "Crybaby",
    "fast food": "Fast Food Series",
    "christmas": "Christmas Series",
    "marine": "Marine Series",
    "fruit": "Fruit Series",
    "vegetable": "Vegetable Series",
    "sweets": "Sweets Series",
    "flower": "Flower Series",
    "animal": "Animal Series",
}


def normalise_series(value: str | None) -> str | None:
    """Canonical form of a series name, or None if it says nothing."""
    text = (value or "").strip()
    if not text or text.lower() in {"unknown", "none", "n/a", "-"}:
        return None
    key = text.lower().removesuffix(" series").strip()
    if key in _SERIES_ALIASES:
        return _SERIES_ALIASES[key]
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many (0 = all)")
    parser.add_argument("--only-blank", action="store_true",
                        help="only rows missing a series or character")
    parser.add_argument("--no-context", action="store_true",
                        help="don't pass the known series list to the model")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Source the env first:\n"
            "  set -a; . ./.env.local; set +a"
        )

    improved = unchanged = skipped = failed = 0
    with SessionLocal() as session:
        rows = session.scalars(
            select(CapturedCollectible)
            .where(CapturedCollectible.status == CardCaptureStatus.PENDING_REVIEW)
            .order_by(CapturedCollectible.captured_at, CapturedCollectible.id)
        ).all()
        if args.only_blank:
            rows = [r for r in rows if not (r.series or "").strip() or not (r.character or "").strip()]
        if args.limit:
            rows = rows[: args.limit]

        print(f"re-identifying {len(rows)} pending capture(s) from their own crops\n")

        for index, capture in enumerate(rows):
            photo = Path(capture.image_path)
            if not photo.exists():
                print(f"  skip  {capture.id[:8]}: image missing")
                skipped += 1
                continue

            try:
                found = extract_collectible_details(
                    photo, api_key=settings.anthropic_api_key, max_tokens=2048,
                    context="" if args.no_context else SERIES_CONTEXT,
                )
            except (RecognitionError, Exception) as exc:  # noqa: B014 - report, never abort the run
                print(f"  fail  {capture.id[:8]}: {exc.__class__.__name__}")
                failed += 1
                continue

            if not found:
                print(f"  none  {capture.id[:8]}: nothing recognised in the crop")
                skipped += 1
                continue

            extracted = found[0]
            unread = set(extracted.unreadable_fields or [])
            before = {field: getattr(capture, field) for field in FIELDS}
            applied = []

            for field in FIELDS:
                if field in unread:
                    continue  # a second guess that isn't sure doesn't overwrite a first
                value = (getattr(extracted, field, "") or "").strip()
                if field == "series":
                    value = normalise_series(value) or ""
                if value and value != (before[field] or ""):
                    if not args.dry_run:
                        setattr(capture, field, value)
                    applied.append(field)
            if "is_secret" not in unread and bool(extracted.is_secret) != bool(capture.is_secret):
                if not args.dry_run:
                    capture.is_secret = bool(extracted.is_secret)
                applied.append("is_secret")

            label = extracted.character or extracted.series or "(unnamed)"
            if applied:
                improved += 1
                print(f"  ok    {label[:34]:<34} {extracted.series or '(no series)':<28} +{','.join(applied)}")
            else:
                unchanged += 1
                print(f"  same  {label[:34]:<34} {extracted.series or '(no series)':<28}")

            if not args.dry_run and improved and improved % 10 == 0:
                session.commit()
            if index < len(rows) - 1:
                time.sleep(CALL_DELAY_SECONDS)

        if not args.dry_run:
            session.commit()

    verb = "would update" if args.dry_run else "updated"
    print(f"\n{verb} {improved}, {unchanged} unchanged, {skipped} skipped, {failed} failed.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    else:
        print("Everything stays PENDING_REVIEW -- confirm at /collectibles/review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
