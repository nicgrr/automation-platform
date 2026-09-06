"""Keep /foil-review's candidate list current without a manual click.

Rescanning every item's photo (~60s at current inventory size) is too slow
to redo on every page view, so foil_review.py caches the result to a file
instead of recomputing per-request. Left alone, that cache only ever
updates when someone happens to click "Rescan inventory" on the page. This
is the scheduled half of that: run it on inventory-audit.timer's own
schedule (see that unit) so newly-scanned cards show up as candidates by
the time anyone opens the page, the same way the identity audit already
surfaces same-day. Read-only against inventory; the only thing it writes
is the cache file foil_review.py itself reads.

    .venv/bin/python -m scripts.refresh_foil_candidates
"""

from automation_control.database import SessionLocal
from automation_control.foil_review import _compute_candidates, _save_candidates


def main(argv: list[str] | None = None) -> int:
    with SessionLocal() as session:
        candidates = _compute_candidates(session)
    _save_candidates(candidates)
    print(f"refreshed {len(candidates)} foil-review candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
