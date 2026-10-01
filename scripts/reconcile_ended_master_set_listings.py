"""One-off: release every variation-listing ledger row whose real eBay
listing is gone, so the sets behind it become exportable again.

Why this exists (2026-09-28): the user manually ended every "Complete
Your Master Set" listing on eBay from Seller Hub directly -- not through
this app, and not through the results-import flow, so the export ledger
(EbayExportItem) never learned about it. A PENDING or CONFIRMED ledger
row is exactly what _build_populations uses to withhold a SKU from being
offered again (see ebay_bulk_export.py, "duplicate prevention"), so every
one of those sets is now stuck: the ledger still thinks each is live or
awaiting an eBay response, even though the real listing behind it is
gone. Only 8 of ~46 owned sets were ever reconciled by hand (the one
results file actually imported); this does the rest by checking the real
eBay mirror (EbayListing, refreshed live via GetMyeBaySelling) instead of
trusting stale ledger state.

Reuses this app's own `_latest_export_items`/`_blocks_export`/
`_variation_group_children` -- the same "which SKU is the current,
relevant ledger row" and "which children belong to this exact parent
build" logic `_import_result_rows` already relies on for a real result
file, just checked against the eBay mirror instead of a results CSV.

Scoped deliberately narrow: only the latest ledger row per SKU, and only
kind="variation_parent" rows (plus their same-batch variation_child
rows). Ordinary single-card ledger rows are never in scope -- the user's
own words were "all the Complete Your Master Set listings", not singles,
and this script has no reliable live signal for a single that was never
confirmed with a real ItemID.

    .venv/bin/python -m scripts.reconcile_ended_master_set_listings --dry-run
    .venv/bin/python -m scripts.reconcile_ended_master_set_listings
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_bulk_export import _blocks_export, _latest_export_items, _variation_group_children
from automation_control.ebay_listings import refresh_from_ebay
from automation_control.models import EbayExportItemStatus, EbayListing

REASON = ("Released: the real eBay listing behind this SKU is gone (ended by the seller directly "
          "in Seller Hub, never through this app's results-import flow), so the ledger's pending/confirmed "
          "state no longer reflects reality. See scripts/reconcile_ended_master_set_listings.py.")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", default="nic", help="attributed to this user in the audit log")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        settings = get_settings()
        await refresh_from_ebay(session, settings, args.user)

        active_titles = {
            row.title for row in session.scalars(
                select(EbayListing).where(EbayListing.listing_status == "Active")
            ).all()
        }

        latest = _latest_export_items(session)
        parents = [item for item in latest.values() if item.kind == "variation_parent" and _blocks_export(item)]

        released = 0
        kept = 0
        released_children_total = 0
        for parent in parents:
            expected_title = parent.title or ""
            if expected_title in active_titles:
                kept += 1
                print(f"  keep     {expected_title}  (still Active on eBay)")
                continue

            children = _variation_group_children(session, parent)
            released += 1
            released_children_total += len(children)
            print(f"  {'would release' if args.dry_run else 'releasing'}  {expected_title}  "
                 f"-> was {parent.status.value}, {len(children)} child row(s)")
            if not args.dry_run:
                for row in [parent, *children]:
                    row.status = EbayExportItemStatus.FAILED
                    row.error = REASON
                    row.confirmed_at = None

        print(f"\n{released} set(s) released ({released_children_total} child row(s) with them), "
             f"{kept} still genuinely active and left untouched")
        if args.dry_run:
            print("--dry-run: nothing written.")
            return 0

        record_event(session, actor_type="user", actor_id=args.user, action="ebay_export.reconcile_ended_master_sets",
                    resource_type="ebay_export_item", resource_id="bulk", outcome="success",
                    correlation_id=str(uuid.uuid4()), details={"released": released, "kept": kept})
        session.commit()
        print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
