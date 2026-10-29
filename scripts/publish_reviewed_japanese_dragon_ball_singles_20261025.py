"""Price-correct and publish two reviewed Japanese Fusion World singles.

Dry-run is the default. Applying requires the exact confirmation token below.
The price review used current Japanese retail evidence checked on 2026-10-25:

* FB08-056 R-star: JPY 3,980-4,730; AU$44.95 remains appropriate.
* FB10-059 SR-star: JPY 480-680; corrected from AU$29.95 to AU$7.95.

The broad AU$120 eBay observation for FB10-059 is a mismatched/outlier comp
and is superseded by these exact Japanese-printing manual valuations.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from automation_control.audit import record_event
from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.ebay_publish import publication_preflight, publish_listing_to_ebay
from automation_control.listing_quality import generated_ebay_description
from automation_control.models import (
    CardPrice,
    CardSet,
    CardVariant,
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


CONFIRMATION = "PUBLISH-JAPANESE-DB-20261025"
ACTOR = "codex-reviewed-japanese-dragon-ball-publish-20261025"


@dataclass(frozen=True)
class Target:
    listing_id: str
    number: str
    expected_price: Decimal
    publish_price: Decimal
    title: str
    price_note: str


TARGETS = (
    Target(
        "7abc5616-46ac-4f81-947d-7150d1b63b52",
        "FB08-056",
        Decimal("44.95"),
        Decimal("44.95"),
        "Son Gohan Youth FB08-056 R Star Alt Art Holo Fusion World Japanese",
        "2026-10-25 exact Japanese-printing review: current Japanese retail JPY 3,980-4,730; AU$44.95 retained.",
    ),
    Target(
        "f1cd4d50-9a13-45bd-af01-61819cd5b6a1",
        "FB10-059",
        Decimal("29.95"),
        Decimal("7.95"),
        "Son Gohan Future FB10-059 SR Star Alt Art Holo Fusion World Japanese",
        "2026-10-25 exact Japanese-printing review: current Japanese retail JPY 480-680; corrected to AU$7.95. Broad AU$120 eBay comp rejected as mismatched/outlier.",
    ),
)


def _append_note(inventory: InventoryItem, note: str) -> None:
    current = (inventory.notes or "").strip()
    if note not in current:
        inventory.notes = "\n".join(value for value in (current, note) if value)


def _prepare(session, target: Target):
    listing = session.get(ShopListing, target.listing_id)
    if listing is None or listing.status != ShopListingStatus.ACTIVE:
        raise RuntimeError(f"{target.number}: exact active ShopListing is missing")
    inventory = session.get(InventoryItem, listing.inventory_item_id)
    item = session.get(CatalogItem, listing.catalog_item_id)
    card = session.get(TcgCard, listing.catalog_item_id)
    if inventory is None or item is None or card is None or card.number != target.number:
        raise RuntimeError(f"{target.number}: catalogue/inventory identity mismatch")
    card_set = session.get(CardSet, card.set_id)
    if card_set is None or card.language != "Japanese":
        raise RuntimeError(f"{target.number}: expected a Japanese card with a valid set")
    if Decimal(listing.price) not in {target.expected_price, target.publish_price}:
        raise RuntimeError(f"{target.number}: unexpected CRM price AU ${listing.price}")
    if inventory.quantity != 1 or listing.quantity != 1:
        raise RuntimeError(f"{target.number}: expected exactly one owned copy")

    pair = session.scalar(
        select(ListingPhotoPair).where(ListingPhotoPair.matched_shop_listing_id == listing.id)
    )
    if pair is None or not pair.front_image_path or not pair.back_image_path:
        raise RuntimeError(f"{target.number}: matched front/back photo pair is incomplete")
    if not Path(pair.front_image_path).is_file() or not Path(pair.back_image_path).is_file():
        raise RuntimeError(f"{target.number}: matched photo file is missing")

    link = session.scalar(select(EbayListingLink).where(EbayListingLink.shop_listing_id == listing.id))
    if link and link.status == EbayListingLinkStatus.ACTIVE:
        return listing, True
    duplicate = session.scalar(select(EbayListing).where(
        EbayListing.listing_status == "Active", EbayListing.title.contains(target.number),
    ).limit(1))
    if duplicate:
        raise RuntimeError(f"{target.number}: eBay {duplicate.ebay_listing_id} is already active")

    listing.price = target.publish_price
    listing.ebay_title = target.title
    listing.ebay_photo_paths = [pair.front_image_path, pair.back_image_path]
    listing.ebay_description = generated_ebay_description(
        listing, item, card, card_set, inventory, has_own_photo=True,
    )
    _append_note(inventory, target.price_note)

    latest_manual = session.scalar(
        select(CardPrice).where(
            CardPrice.catalog_item_id == listing.catalog_item_id,
            CardPrice.source == "manual",
            CardPrice.variant == CardVariant.HOLO,
        ).order_by(CardPrice.fetched_at.desc()).limit(1)
    )
    if latest_manual is None or Decimal(latest_manual.price) != target.publish_price:
        session.add(CardPrice(
            id=str(uuid.uuid4()),
            catalog_item_id=listing.catalog_item_id,
            variant=CardVariant.HOLO,
            source="manual",
            price=target.publish_price,
            currency="AUD",
            fetched_at=datetime.now(UTC),
        ))
        session.flush()

    check = publication_preflight(session, listing, get_settings())
    if not check.ready:
        raise RuntimeError(f"{target.number}: " + "; ".join(check.errors))
    if len(check.photos) != 2:
        raise RuntimeError(f"{target.number}: expected two publication photos")
    return listing, False


async def run(*, apply: bool, confirmation: str) -> int:
    if apply and confirmation != CONFIRMATION:
        raise RuntimeError(f"apply requires --confirm {CONFIRMATION}")
    with SessionLocal() as session:
        pending: list[Target] = []
        live = 0
        for target in TARGETS:
            listing, already_live = _prepare(session, target)
            if already_live:
                live += 1
                print(f"already live  {target.number}  eBay price AU ${listing.price}")
            else:
                pending.append(target)
                print(f"{'prepared' if apply else 'would publish'}  {target.number}  AU ${listing.price}  {listing.ebay_title}")
        if not apply:
            session.rollback()
            print(f"Dry run: {len(pending)} ready, {live} already live; nothing written.")
            return 0

        session.commit()
        for target in pending:
            try:
                await publish_listing_to_ebay(
                    target.listing_id, confirmation="publish", user=ACTOR, session=session,
                )
            except HTTPException as exc:
                print(f"FAILED {target.number}: {exc.detail}")
                return 1
            link = session.scalar(
                select(EbayListingLink).where(EbayListingLink.shop_listing_id == target.listing_id)
            )
            print(f"published {target.number} eBay {link.ebay_listing_id if link else 'unknown'}")
        record_event(
            session,
            actor_type="user",
            actor_id=ACTOR,
            action="ebay.publish_reviewed_japanese_dragon_ball_batch",
            resource_type="shop_listing_batch",
            resource_id="20261025-japanese-dragon-ball-2",
            outcome="success",
            correlation_id="20261025-japanese-dragon-ball-2",
            details={"published_count": len(pending), "already_live_count": live},
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    return asyncio.run(run(apply=args.apply, confirmation=args.confirm))


if __name__ == "__main__":
    raise SystemExit(main())
