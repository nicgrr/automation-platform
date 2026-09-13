"""Fall back to stock art on inventory items the photo audit flagged.

`scripts/audit_inventory_photos.py` finds items whose stored photo doesn't
hash like the card they're filed under. This clears `scan_image_path` on
those items so the shop shows the publisher's reference art instead of a
photo that may be of something else.

**It moves no files and deletes nothing.** Every cleared path is written to
a journal first, and `--revert` puts them all back from it. The photos stay
on disk throughout; only the database pointer changes.

Two things worth understanding before running it:

  * This hides a symptom rather than fixing a cause. A flagged item is
    either a good card whose photo hashes badly (a foil, a crop catching
    the neighbouring card) or a genuinely misfiled card. In the second
    case stock art makes the listing *more* wrong, not less -- the buyer
    now sees a confident render of a card you may not own, carrying that
    card's price. That is the exact failure audit_inventory_photos.py was
    written to catch. Correcting the card in /inventory-audit fixes it;
    this only stops the wrong photo being shown.
  * It clears the photo from the review page too, which is the evidence
    you would use to decide what the card actually is. Revert first if you
    later want to work through them properly.

Excluded automatically, because neither is a mismatch:

  * items you have already dismissed in /inventory-audit -- a dismissal is
    a human saying "I looked, this is fine", and re-clearing it would
    throw that verdict away;
  * items whose closest match *is* the card they're filed under, which is
    a badly-hashing photo of the right card.

    .venv/bin/python -m scripts.photo_audit_use_stock --dry-run
    .venv/bin/python -m scripts.photo_audit_use_stock
    .venv/bin/python -m scripts.photo_audit_use_stock --revert
"""

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from automation_control.audit import record_event
from automation_control.database import SessionLocal
from automation_control.inventory_audit import DEFAULT_THRESHOLD, find_mismatches
from automation_control.models import InventoryItem, PhotoAuditDismissal

JOURNAL_DIR = Path("backups")


def _journal_path(stamp: str | None = None) -> Path:
    stamp = stamp or datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    return JOURNAL_DIR / f"photo_audit_stock_swap-{stamp}.json"


def candidates(session, threshold: int) -> list[dict]:
    """Flagged items that are actually candidates for this.

    Dismissed items and self-matches are filtered here rather than in the
    caller, so --dry-run, the real run and --revert can never disagree
    about what the set is.
    """
    dismissed = {d.inventory_item_id for d in session.query(PhotoAuditDismissal).all()}
    return [
        row for row in find_mismatches(session, threshold)
        if row["item_id"] not in dismissed
        and row["looks_like_id"] != row["card_id"]
        and (row.get("_scan_image_path") or "").strip()
    ]


def apply(session, rows: list[dict], user: str) -> Path:
    journal = {
        "swapped_at": datetime.now(UTC).isoformat(),
        "threshold": DEFAULT_THRESHOLD,
        "items": [{"item_id": r["item_id"], "scan_image_path": r["_scan_image_path"],
                   "filed_label": r["filed_label"], "looks_like_label": r["looks_like_label"],
                   "own_distance": r["own_distance"]} for r in rows],
    }
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _journal_path()
    # Written before the update, not after: a journal that doesn't exist
    # because the process died mid-write is a swap that can't be undone.
    path.write_text(json.dumps(journal, indent=2))

    for row in rows:
        item = session.get(InventoryItem, row["item_id"])
        if item is not None:
            item.scan_image_path = None
    record_event(
        session, actor_type="user", actor_id=user, action="inventory.photo_audit_use_stock",
        resource_type="inventory_item", resource_id=None, outcome="success",
        correlation_id=str(uuid.uuid4()),
        details={"count": len(rows), "journal": str(path)},
    )
    session.commit()
    return path


def revert(session, path: Path, user: str) -> int:
    journal = json.loads(path.read_text())
    restored = 0
    for entry in journal["items"]:
        item = session.get(InventoryItem, entry["item_id"])
        # Only restore items still empty -- a photo re-added since the swap
        # is newer than this journal and must not be overwritten by it.
        if item is not None and not (item.scan_image_path or "").strip():
            item.scan_image_path = entry["scan_image_path"]
            restored += 1
    record_event(
        session, actor_type="user", actor_id=user, action="inventory.photo_audit_revert_stock",
        resource_type="inventory_item", resource_id=None, outcome="success",
        correlation_id=str(uuid.uuid4()), details={"restored": restored, "journal": str(path)},
    )
    session.commit()
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--dry-run", action="store_true", help="list what would change, write nothing")
    parser.add_argument("--revert", nargs="?", const="latest", metavar="JOURNAL",
                        help="restore paths from a journal (default: the most recent)")
    parser.add_argument("--user", default="cli")
    args = parser.parse_args(argv)

    session = SessionLocal()

    if args.revert:
        if args.revert == "latest":
            journals = sorted(JOURNAL_DIR.glob("photo_audit_stock_swap-*.json"))
            if not journals:
                print("no journal to revert from")
                return 1
            path = journals[-1]
        else:
            path = Path(args.revert)
        restored = revert(session, path, args.user)
        print(f"restored {restored} photo path(s) from {path}")
        return 0

    rows = candidates(session, args.threshold)
    if not rows:
        print("nothing flagged that isn't already dismissed or a self-match")
        return 0

    for row in sorted(rows, key=lambda r: -r["own_distance"])[:10]:
        print(f"  {row['filed_label']:46} photo looks like {row['looks_like_label']}"
              f"  (d={row['own_distance']})")
    if len(rows) > 10:
        print(f"  ... and {len(rows) - 10} more")

    if args.dry_run:
        print(f"\nwould clear the photo on {len(rows)} item(s); nothing written")
        return 0

    path = apply(session, rows, args.user)
    print(f"\ncleared the photo on {len(rows)} item(s) -- they now show stock art")
    print(f"journal: {path}")
    print("revert:  .venv/bin/python -m scripts.photo_audit_use_stock --revert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
