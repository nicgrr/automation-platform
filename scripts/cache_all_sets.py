"""Cache every set in the pokemontcg.io catalogue, so a newly scanned pile
is identifiable without waiting on a download.

Long-running by nature (~18k card images at the time of writing, several
hours). Designed to be interrupted and re-run: `cache_set` skips sets that
are already cached, so progress is never lost and a second run costs only
the API call that lists sets.

pokemontcg.io fails a large fraction of requests with empty-body 5xx, so
each set gets several attempts before being recorded as a failure and
moved past -- one bad set must not end the run. Failures are listed at the
end and are simply re-attempted on the next run.

    .venv/bin/python -m scripts.cache_all_sets [--limit N] [--dry-run]
"""

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import select

from automation_control.adapters import pokemontcg_catalog
from automation_control.config import get_settings
from automation_control.database import Base, SessionLocal, engine
from automation_control.models import CardSet

ATTEMPTS_PER_SET = 3
# Between sets, not between images -- cache_set already paces its own image
# downloads. This is just about not hammering the list/card endpoints.
PAUSE_SECONDS = 2.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cache every pokemontcg.io set locally")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many sets (for a trial run)")
    parser.add_argument("--dry-run", action="store_true", help="list what would be cached, download nothing")
    args = parser.parse_args(argv)

    settings = get_settings()
    Base.metadata.create_all(engine)
    cache_dir = Path(settings.scan_cache_dir)

    try:
        upstream = pokemontcg_catalog.list_sets(api_key=settings.pokemontcg_api_key)
    except Exception as exc:
        print(f"error: could not list sets: {exc}", file=sys.stderr)
        return 1

    with SessionLocal() as session:
        cached = {s.id for s in session.scalars(select(CardSet).where(CardSet.cached_at.is_not(None)))}

    # Newest first: a freshly opened pile is far likelier to be a recent set
    # than a 2003 one, so the useful sets land early if the run is cut short.
    todo = sorted(
        (s for s in upstream if s.get("id") and s["id"] not in cached),
        key=lambda s: s.get("releaseDate") or "",
        reverse=True,
    )
    if args.limit:
        todo = todo[: args.limit]

    print(f"{len(cached)} set(s) already cached; {len(todo)} to go.\n", flush=True)
    if args.dry_run:
        for s in todo:
            print(f"  would cache {s['id']:<10} {s.get('name','')} ({s.get('releaseDate')})")
        return 0

    done = failed = 0
    failures: list[str] = []
    started = time.time()

    for index, entry in enumerate(todo, start=1):
        set_id = entry["id"]
        name = entry.get("name", "")
        for attempt in range(1, ATTEMPTS_PER_SET + 1):
            try:
                with SessionLocal() as session:
                    result = pokemontcg_catalog_cache(session, set_id, cache_dir, settings)
                elapsed = time.time() - started
                rate = index / elapsed * 3600 if elapsed else 0
                remaining = (len(todo) - index) / rate if rate else 0
                print(
                    f"[{index}/{len(todo)}] {set_id:<10} {name[:32]:<32} "
                    f"{result.cards_total:>4} cards, {result.hashed:>4} hashed"
                    + (f", {result.images_failed} image(s) failed" if result.images_failed else "")
                    + f"   (~{remaining:.1f}h left)",
                    flush=True,
                )
                done += 1
                break
            except Exception as exc:
                if attempt == ATTEMPTS_PER_SET:
                    print(f"[{index}/{len(todo)}] {set_id:<10} FAILED after {attempt} attempts: {exc}", flush=True)
                    failed += 1
                    failures.append(set_id)
                else:
                    time.sleep(PAUSE_SECONDS * attempt)
        time.sleep(PAUSE_SECONDS)

    print(f"\nCached {done} set(s); {failed} failed.")
    if failures:
        print("Failed (just re-run to retry these): " + ", ".join(failures))
    return 0


def pokemontcg_catalog_cache(session, set_id: str, cache_dir: Path, settings):
    """Thin wrapper so the retry loop above reads cleanly."""
    from automation_control.scan_ingest import catalog

    return catalog.cache_set(session, set_id, cache_dir, api_key=settings.pokemontcg_api_key)


if __name__ == "__main__":
    raise SystemExit(main())
