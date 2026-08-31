"""Compute art_phash for cards cached before that column existed.

The artwork-window hash is what finds reverse holos, whose foil pattern
covers everything except that window -- measured on real scans, a Cosmic
Eclipse Pancham went from Hamming distance 14 (rank 3, beaten by an
unrelated card) to 6 and rank 1. Plain cards are unaffected: four verified
non-holos ranked first on both signals.

Reads only local reference images, so no network. Sets going forward get it
at cache time (see catalog._compute_phashes); this is for the ~18k already
cached. Re-running only fills what's still missing.

    .venv/bin/python -m scripts.backfill_art_phash [--limit N]
"""

import argparse
import time
from pathlib import Path

import imagehash
from PIL import Image
from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import CatalogCard
from automation_control.scan_ingest.identify import art_phash

# Committing per card would be thousands of transactions competing with the
# scan service and the web app for the write lock; per batch keeps it brief.
BATCH = 250


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill artwork-window hashes")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many cards")
    args = parser.parse_args(argv)

    done = skipped = 0
    started = time.time()

    with SessionLocal() as session:
        pending = list(session.scalars(
            select(CatalogCard).where(
                CatalogCard.art_phash.is_(None),
                CatalogCard.local_image_path.is_not(None),
            )
        ))
        if args.limit:
            pending = pending[: args.limit]
        print(f"{len(pending):,} card(s) to hash", flush=True)

        for index, card in enumerate(pending, start=1):
            path = Path(card.local_image_path)
            if not path.exists():
                skipped += 1
                continue
            try:
                with Image.open(path) as image:
                    rgb = image.convert("RGB")
                    card.art_phash = str(art_phash(rgb))
                    if not card.phash:
                        card.phash = str(imagehash.phash(rgb))
                done += 1
            except Exception:
                # A corrupt reference image shouldn't stop the run; the card
                # simply keeps art_phash=None and is matched on the
                # whole-card hash alone, exactly as before.
                skipped += 1

            if index % BATCH == 0:
                session.commit()
                rate = index / (time.time() - started)
                print(f"  {index:,}/{len(pending):,}  (~{(len(pending)-index)/rate/60:.1f} min left)", flush=True)
        session.commit()

    print(f"\nHashed {done:,}; skipped {skipped:,} (missing or unreadable image).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
