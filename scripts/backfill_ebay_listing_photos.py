"""One-time (or occasional) backfill: photos and description for eBay
listings that only ever came through GetMyeBaySelling's compact
ActiveList sync -- title/price/quantity only, no PictureDetails or
Description at all (see automation_control/ebay_listings.py's _upsert
docstring for why that sync stays deliberately compact rather than
fetching full item detail on every run).

Only 5 of 60 real listings had photos cached as of 2026-09-25, from an
old one-off GetItem import that set image_urls/details_source by hand
for a handful of bundle listings. This does the same thing for every
listing missing that data, via the same GetItem call
(EbayTradingReadAdapter.get_item_details), one listing at a time.

Skips a listing that already has image_urls cached, so a re-run only
fills in whatever's still missing -- safe to run again after a batch of
new listings goes live. --refresh re-reads every active listing instead,
picking up photos revised on eBay since they were first cached (the NL
Vault catalogue shows exactly these cached photos).

    .venv/bin/python -m scripts.backfill_ebay_listing_photos --dry-run
    .venv/bin/python -m scripts.backfill_ebay_listing_photos
    .venv/bin/python -m scripts.backfill_ebay_listing_photos --refresh
"""

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from automation_control.adapters.ebay import EbayApiError
from automation_control.adapters.ebay_trading import EbayTradingReadAdapter
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_listings import SOURCE
from automation_control.ebay_oauth import OAuthError, configured, ebay_services, valid_user_access_token
from automation_control.models import EbayListing

# A courtesy pause between GetItem calls -- this is a one-off backfill run
# by hand, not a scheduled poll, so there's no real time pressure to rush
# through 55 calls as fast as possible.
DELAY_SECONDS = 0.3


async def run(args) -> int:
    settings = get_settings()
    if not configured(settings):
        raise SystemExit("eBay is not configured -- see ebay_oauth.configured.")

    backfilled = failed = skipped = changed = 0

    with SessionLocal() as session:
        cipher, oauth = ebay_services(settings)
        token = await valid_user_access_token(session, cipher, oauth)
        adapter = EbayTradingReadAdapter(token, settings.ebay_client_id, environment=settings.ebay_env,
                                         marketplace_id=settings.ebay_marketplace_id)

        rows = session.scalars(
            select(EbayListing).where(EbayListing.listing_status == "Active")
        ).all()
        targets = [
            r for r in rows
            if (r.raw_snapshot or {}).get("source") == SOURCE
            and (
                args.refresh
                or not (r.raw_snapshot or {}).get("image_urls")
                or not str((r.raw_snapshot or {}).get("description") or "").strip()
            )
        ]

        print(
            f"Re-reading all {len(targets)} active cached listing(s).\n" if args.refresh else
            f"{len(targets)} of {len(rows)} active cached listing(s) are missing "
            "photos or a description.\n"
        )

        for row in targets:
            try:
                details = await adapter.get_item_details(row.ebay_listing_id)
            except EbayApiError as exc:
                print(f"  FAILED {row.ebay_listing_id} ({row.title[:50]}): {exc}")
                failed += 1
                continue

            if not details.picture_urls and not details.description:
                print(f"  no details returned for {row.ebay_listing_id} ({row.title[:50]}) -- skipped")
                skipped += 1
                continue

            photos_changed = list(details.picture_urls or []) != list((row.raw_snapshot or {}).get("image_urls") or [])
            changed += photos_changed
            if args.refresh and not photos_changed:
                print(f"  unchanged: {row.ebay_listing_id} ({row.title[:50]})")
            else:
                print(f"  {'would update' if args.dry_run else 'updated'}: {row.ebay_listing_id} "
                      f"({row.title[:50]}) -- {len(details.picture_urls)} photo(s)"
                      f"{', has description' if details.description else ', no description'}")
            if args.dry_run:
                continue

            row.raw_snapshot = {
                **(row.raw_snapshot or {}),
                "image_urls": details.picture_urls or (row.raw_snapshot or {}).get("image_urls") or [],
                "description": details.description or (row.raw_snapshot or {}).get("description") or "",
                "details_source": "ebay_get_item",
                "details_imported_at": datetime.now(UTC).isoformat(),
            }
            session.commit()
            backfilled += 1
            await asyncio.sleep(DELAY_SECONDS)

    print(f"\n{backfilled} backfilled ({changed} with new photos), {failed} failed, "
          f"{skipped} had nothing to backfill.")
    if args.dry_run:
        print("--dry-run: nothing written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--refresh", action="store_true",
                        help="re-read every active listing, not just those missing photos")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except OAuthError as exc:
        raise SystemExit(f"eBay account not connected: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
