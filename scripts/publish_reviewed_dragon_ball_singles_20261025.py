"""Publish the exact reviewed Dragon Ball singles prepared on 2026-10-25.

The script is deliberately an explicit, resumable operator action:

* dry-run is the default;
* the 27 ShopListing IDs, card numbers, quantities and reviewed prices are
  fixed below rather than selected by a broad database query;
* every listing must have its matched front/back photo pair on disk;
* its eBay quantity is synchronised to the previously confirmed inventory;
* an active eBay title containing the same exact card number blocks the run;
* apply mode requires an exact confirmation token; and
* the existing one-click publication service remains the only eBay writer.

Porunga BT30-068 is raised from AU$0.98 to AU$1.25 because eBay rejects
fixed-price listings below AU$1.00.  All other prices were reviewed against
the fresh 2026-10-24 market observations and remain unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_publish import publication_preflight, publish_listing_to_ebay
from automation_control.listing_quality import (
    generated_ebay_description,
    generated_ebay_title,
)
from automation_control.models import (
    CardSet,
    CatalogItem,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    InventoryItem,
    ListingPhotoPair,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)


CONFIRMATION = "PUBLISH-REVIEWED-DB-20261025"
ACTOR = "codex-reviewed-dragon-ball-publish-20261025"


@dataclass(frozen=True)
class Target:
    listing_id: str
    number: str
    expected_price: Decimal
    publish_price: Decimal
    quantity: int


def _target(
    listing_id: str, number: str, price: str, quantity: int, *, publish_price: str | None = None,
) -> Target:
    return Target(
        listing_id, number, Decimal(price), Decimal(publish_price or price), quantity,
    )


TARGETS = (
    _target("24512ff5-0a61-41d2-91d7-d2f054793e57", "BT21-052", "4.95", 1),
    _target("896b2358-0e81-4678-aeda-10ce4a1e462e", "BT21-088", "8.95", 1),
    _target("52d13e21-f836-4971-8b5b-9df746582180", "BT30-054", "12.95", 1),
    _target("a75b967e-dd7a-4583-917e-97c28ac708c9", "BT30-068", "0.98", 1, publish_price="1.25"),
    _target("50b34830-38f9-4e64-859b-af34a8773f03", "BT30-137", "3.30", 1),
    _target("5ec08827-37a3-477f-a50c-659f6038d622", "EX25-02", "1.26", 1),
    _target("9c0ff3ed-7fab-4ea2-b0e2-f28bdb66b857", "EX25-04", "2.12", 1),
    _target("a203e5a7-83e5-4eb5-b677-8d1ebdba4a33", "EX25-05", "2.12", 2),
    _target("27088e53-a770-49ce-849c-f745ffde26b3", "EX25-07", "1.24", 2),
    _target("798df130-cae5-4314-8d74-72cdc34e37ee", "EX25-09", "2.12", 1),
    _target("a4670fd2-9944-4c3b-b00b-c42c0a8baa95", "EX25-18", "2.12", 2),
    _target("060e239f-9edd-4fb4-a440-15b4bba24f94", "EX25-23", "1.62", 2),
    _target("e4c13033-1078-438e-a1b0-61eaa61d715e", "EX25-25", "1.22", 2),
    _target("eee18aee-6adf-4604-a3dc-64221dacd080", "EX25-28", "3.95", 3),
    _target("0d5b7d20-59a8-43d3-bf3b-98c1d3e7475a", "EX25-33", "2.12", 1),
    _target("48108282-e303-4bdb-9185-757693e21ae2", "EX25-34", "1.80", 2),
    _target("1412eea1-d41d-4016-ac1e-4a7495308cbc", "EX25-36", "1.44", 1),
    _target("45f4c6d7-ef61-4905-b561-cc3ba44ae02e", "EX25-37", "1.90", 2),
    _target("90ceff50-6d3f-48d1-96e7-077217956fb5", "EX25-38", "2.12", 2),
    _target("4bf36501-4d77-4ec2-a69e-1249e23b2519", "EX25-39", "4.95", 3),
    _target("88ec07e8-33fc-4709-9879-0c0931dfe648", "EX25-40", "7.95", 2),
    _target("fff9e9a7-c82d-49af-8a8d-4bf8331935ec", "FB04-045", "9.95", 1),
    _target("1d9c3700-2ffb-4d48-a205-301171fd44d4", "FB04-094", "2.95", 1),
    _target("9fc2551a-b591-4746-a794-4d68d947bdbf", "FB04-129", "17.95", 1),
    _target("df73cc1d-1243-4a25-a606-24b3030a4d4f", "FB08-112", "34.95", 1),
    _target("29c43655-f7f2-49bf-b848-12ce5b135cab", "SB02-022", "6.95", 1),
    _target("e9cd1626-1332-40d9-a293-68cfbe3817dc", "SB02-058", "4.95", 1),
)


TITLE_OVERRIDES = {
    "EX25-39": "Kale & Caulifla EX25-39 EX Rare Holo Dragon Ball Super TCG English",
    "FB04-045": "Vegeta Mini DA FB04-045 Alternate Art Holo DB Fusion World English",
}


def _load_exact(session, target: Target):
    listing = session.get(ShopListing, target.listing_id)
    if listing is None:
        raise RuntimeError(f"missing ShopListing {target.listing_id}")
    if listing.status != ShopListingStatus.ACTIVE:
        raise RuntimeError(f"{target.number}: expected active CRM listing, got {listing.status.value}")
    inventory = session.get(InventoryItem, listing.inventory_item_id)
    if inventory is None:
        raise RuntimeError(f"{target.number}: inventory row is missing")
    card = session.get(TcgCard, listing.catalog_item_id)
    item = session.get(CatalogItem, listing.catalog_item_id)
    if card is None or item is None or card.number != target.number:
        found = card.number if card else "missing"
        raise RuntimeError(f"{target.listing_id}: expected {target.number}, found {found}")
    card_set = session.get(CardSet, card.set_id)
    if card_set is None:
        raise RuntimeError(f"{target.number}: card set is missing")
    if card.language != "English":
        raise RuntimeError(f"{target.number}: expected English, found {card.language!r}")
    if inventory.quantity != target.quantity:
        raise RuntimeError(
            f"{target.number}: expected quantity {target.quantity}; "
            f"inventory={inventory.quantity}, listing={listing.quantity}"
        )
    if listing.quantity < 0 or listing.quantity > inventory.quantity:
        raise RuntimeError(
            f"{target.number}: unsafe listing quantity {listing.quantity}; "
            f"inventory only contains {inventory.quantity}"
        )
    if Decimal(listing.price) not in {target.expected_price, target.publish_price}:
        raise RuntimeError(
            f"{target.number}: expected AU ${target.expected_price}"
            f"{' or AU $' + str(target.publish_price) if target.publish_price != target.expected_price else ''}; "
            f"found AU ${listing.price}"
        )

    pairs = list(session.scalars(
        select(ListingPhotoPair).where(ListingPhotoPair.matched_shop_listing_id == listing.id)
    ))
    if len(pairs) != 1:
        raise RuntimeError(f"{target.number}: expected exactly one matched photo pair, found {len(pairs)}")
    pair = pairs[0]
    if not pair.front_image_path or not pair.back_image_path:
        raise RuntimeError(f"{target.number}: front/back photo pair is incomplete")
    for label, value in (("front", pair.front_image_path), ("back", pair.back_image_path)):
        if not Path(value).is_file():
            raise RuntimeError(f"{target.number}: {label} photo is missing: {value}")

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    if link and link.status == EbayListingLinkStatus.ACTIVE:
        return listing, inventory, card, card_set, item, pair, True

    remote_duplicate = session.scalar(
        select(EbayListing).where(
            EbayListing.listing_status == "Active",
            EbayListing.title.contains(target.number),
        ).limit(1)
    )
    if remote_duplicate is not None:
        raise RuntimeError(
            f"{target.number}: live eBay cache already contains {remote_duplicate.ebay_listing_id} "
            f"({remote_duplicate.title})"
        )
    return listing, inventory, card, card_set, item, pair, False


def _prepare_listing(session, target: Target):
    listing, inventory, card, card_set, item, pair, already_live = _load_exact(session, target)
    if already_live:
        return listing, card, True

    listing.price = target.publish_price
    listing.quantity = target.quantity
    listing.ebay_photo_paths = [pair.front_image_path, pair.back_image_path]
    listing.ebay_title = TITLE_OVERRIDES.get(
        target.number,
        generated_ebay_title(listing, item, card, card_set, inventory),
    )
    listing.ebay_description = generated_ebay_description(
        listing, item, card, card_set, inventory, has_own_photo=True,
    )
    check = publication_preflight(session, listing, get_settings())
    if not check.ready:
        raise RuntimeError(f"{target.number}: " + "; ".join(check.errors))
    if len(check.photos) != 2:
        raise RuntimeError(f"{target.number}: publication selected {len(check.photos)} photos, expected 2")
    return listing, card, False


async def run(*, apply: bool, confirmation: str) -> int:
    if apply and confirmation != CONFIRMATION:
        raise RuntimeError(f"apply requires --confirm {CONFIRMATION}")

    with SessionLocal() as session:
        prepared: list[Target] = []
        already_live: list[Target] = []
        for target in TARGETS:
            listing, card, live = _prepare_listing(session, target)
            if live:
                already_live.append(target)
                print(f"already live  {target.number:9} AU ${listing.price:>6}  {listing.ebay_title or listing.title}")
            else:
                prepared.append(target)
                marker = "would publish" if not apply else "prepared"
                print(f"{marker:12} {target.number:9} AU ${listing.price:>6}  {listing.ebay_title}")

        if not apply:
            session.rollback()
            print(f"\nDry run: {len(prepared)} ready, {len(already_live)} already live; nothing written.")
            return 0

        # Persist the exact reviewed copy, photos and Porunga correction before
        # the first network call. A failed publication can then be retried from
        # the same complete Listing Studio record without reconstructing it.
        session.commit()
        record_event(
            session,
            actor_type="user",
            actor_id=ACTOR,
            action="ebay.prepare_reviewed_dragon_ball_batch",
            resource_type="shop_listing_batch",
            resource_id="20261025-dragon-ball-27",
            outcome="success",
            correlation_id="20261025-dragon-ball-27",
            details={
                "prepared_count": len(prepared),
                "already_live_count": len(already_live),
                "porunga_price_aud": "1.25",
                "photo_count_per_listing": 2,
            },
        )

        published = 0
        for target in prepared:
            try:
                await publish_listing_to_ebay(
                    target.listing_id,
                    confirmation="publish",
                    user=ACTOR,
                    session=session,
                )
            except HTTPException as exc:
                print(f"FAILED        {target.number}: {exc.detail}")
                print(
                    f"Stopped after {published} successful publication(s). "
                    "The script is resumable after the reported issue is corrected."
                )
                return 1
            published += 1
            link = session.scalar(
                select(EbayListingLink).where(EbayListingLink.shop_listing_id == target.listing_id)
            )
            print(f"published     {target.number:9} eBay {link.ebay_listing_id if link else 'unknown'}")

        print(
            f"\nPublished {published} listing(s); {len(already_live)} exact target(s) were already live."
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write reviewed CRM data and publish to eBay")
    parser.add_argument("--confirm", default="", help="exact apply confirmation token")
    args = parser.parse_args()
    return asyncio.run(run(apply=args.apply, confirmation=args.confirm))


if __name__ == "__main__":
    raise SystemExit(main())
