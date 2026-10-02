"""Read-only readiness audit for the direct eBay publication path.

It runs the exact same local checks as each listing's Review eBay page and
never obtains an access token, uploads an image, or calls eBay. Source the
service environment first when invoking it by hand:

    set -a; source .env.local; set +a
    .venv/bin/python -m scripts.ebay_publish_preflight
    .venv/bin/python -m scripts.ebay_publish_preflight --listing-id UUID
"""

import argparse
from collections import Counter

from sqlalchemy import select

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_publish import publication_preflight
from automation_control.models import ShopListing, ShopListingStatus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--listing-id", help="audit one canonical shop listing instead of every sellable row")
    args = parser.parse_args(argv)
    settings = get_settings()

    with SessionLocal() as session:
        if args.listing_id:
            listing = session.get(ShopListing, args.listing_id)
            if listing is None:
                print(f"Listing not found: {args.listing_id}")
                return 2
            listings = [listing]
        else:
            listings = session.scalars(
                select(ShopListing)
                .where(ShopListing.status.in_([ShopListingStatus.ACTIVE, ShopListingStatus.DRAFT]))
                .order_by(ShopListing.created_at, ShopListing.id)
            ).all()

        ready = 0
        upload_on_publish = 0
        error_counts: Counter[str] = Counter()
        warning_counts: Counter[str] = Counter()
        for listing in listings:
            check = publication_preflight(session, listing, settings)
            ready += int(check.ready)
            upload_on_publish += int(check.image_will_upload)
            error_counts.update(check.errors)
            warning_counts.update(check.warnings)
            if args.listing_id:
                print(f"{listing.id}: {'READY' if check.ready else 'BLOCKED'} — {listing.title}")
                for message in check.errors:
                    print(f"  ERROR: {message}")
                for message in check.warnings:
                    print(f"  NOTE: {message}")

    print(f"Environment: {settings.ebay_env}")
    print(f"Audited: {len(listings)}")
    print(f"Ready: {ready}")
    print(f"Blocked: {len(listings) - ready}")
    print(f"Would upload scan on confirmed publish: {upload_on_publish}")
    if error_counts:
        print("Blockers:")
        for message, count in error_counts.most_common():
            print(f"  {count}: {message}")
    if warning_counts:
        print("Notes:")
        for message, count in warning_counts.most_common():
            print(f"  {count}: {message}")
    print("Read-only: no image uploads or eBay API calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
