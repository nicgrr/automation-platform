#!/usr/bin/env python
"""One-time cleanup for photos captured before auto-orientation existed.

Two passes over every distinct stored image (front + back, deduplicated
since a multi-card group photo is shared across several CapturedCard rows):

1. EXIF auto-orient (free, instant) -- fixes photos where the phone recorded
   an orientation flag but didn't rotate the pixel data.
2. AI rotation check (Claude vision, small per-image cost) on whatever's
   still not upright after that -- catches photos that are genuinely
   sideways with no EXIF flag to go on.

Run from the automation-platform directory:
    .venv/bin/python scripts/fix_photo_orientation.py
"""

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(PROJECT_DIR / ".env.local")

from automation_control.card_recognition import detect_rotation  # noqa: E402
from automation_control.config import get_settings  # noqa: E402
from automation_control.database import SessionLocal  # noqa: E402
from automation_control.image_processing import auto_orient, rotate_in_place  # noqa: E402
from automation_control.models import CapturedCard  # noqa: E402


def main() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not configured, aborting.", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) > 1:
        # retry mode: only check the specific paths given, so a partial
        # failure doesn't require re-billing every photo that already
        # succeeded on a prior run.
        paths: set[str] = set(sys.argv[1:])
    else:
        with SessionLocal() as session:
            rows = session.query(CapturedCard).all()
            paths = set()
            for row in rows:
                if row.front_image_path:
                    paths.add(row.front_image_path)
                if row.back_image_path:
                    paths.add(row.back_image_path)

    print(f"{len(paths)} distinct photo(s) to check\n")

    ai_checked = 0
    ai_rotated = 0
    failed: list[tuple[str, str]] = []

    for path_str in sorted(paths):
        path = Path(path_str)
        if not path.exists():
            print(f"  skip (file missing): {path}")
            continue

        try:
            auto_orient(path)
        except Exception as exc:
            failed.append((str(path), f"EXIF pass failed: {exc}"))
            continue

        try:
            rotation = detect_rotation(path, api_key=settings.anthropic_api_key)
            ai_checked += 1
            if rotation:
                rotate_in_place(path, rotation)
                ai_rotated += 1
                print(f"  rotated {rotation} deg: {path}")
        except Exception as exc:
            failed.append((str(path), f"AI rotation check failed: {exc}"))

    print(f"\nDone. AI-checked {ai_checked} photo(s), rotated {ai_rotated}.")
    if failed:
        print(f"\n{len(failed)} failure(s):")
        for path, err in failed:
            print(f"  {path}: {err}")


if __name__ == "__main__":
    main()
