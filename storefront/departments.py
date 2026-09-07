"""Departments -- the shop's top-level split into TCG, Collectibles and Apparel.

One flat grid of everything worked while the shop was cards only. It stops
working the moment a Sonny Angel sits next to a Charizard: the two have
different buyers, different price ranges and different questions ("what
condition?" vs "is it the secret?"), and a shopper who came for one has to
scroll past the other.

The split is derived from `CatalogItem.item_type` rather than stored as a
column on ShopListing. That keeps one source of truth -- an item is in
Collectibles *because* it's a COLLECTIBLE, not because someone also
remembered to tag the listing -- and means an item can never appear in two
departments or none.

Bulk lots belong to TCG by construction: a lot is a pile of cards from one
set, so there is no arrangement in which it is apparel.

Apparel ships with no stock. That's deliberate and not a placeholder: the
department exists so the first shirt has somewhere to go, and
`in_stock` below keeps empty departments out of the navigation, so the
shop never advertises a section that lands on an empty page.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus,
)


@dataclass(frozen=True)
class Department:
    slug: str
    name: str
    blurb: str
    item_types: tuple[CatalogItemType, ...]
    includes_bulk: bool = False


DEPARTMENTS: tuple[Department, ...] = (
    Department(
        slug="tcg",
        name="Trading cards",
        blurb="Pokémon and One Piece singles, sealed product and sorted bulk by set.",
        item_types=(
            CatalogItemType.TCG_CARD,
            CatalogItemType.SEALED_PRODUCT,
            CatalogItemType.GRADED_CARD,
        ),
        includes_bulk=True,
    ),
    Department(
        slug="collectibles",
        name="Collectibles",
        blurb="Sonny Angel, Smiski and blind-box figures, secrets included.",
        item_types=(CatalogItemType.COLLECTIBLE,),
    ),
    Department(
        slug="apparel",
        name="Apparel",
        blurb="Shirts and wearables. Just getting started.",
        item_types=(CatalogItemType.APPAREL, CatalogItemType.ACCESSORY),
    ),
)

BY_SLUG = {department.slug: department for department in DEPARTMENTS}


def find(slug: str) -> Department | None:
    return BY_SLUG.get((slug or "").strip().lower())


def listing_filter(department: Department):
    """A WHERE clause selecting this department's single listings.

    Joined against CatalogItem, so a listing whose catalogue row is missing
    resolves to no department and simply doesn't appear -- better than
    defaulting it into TCG and mislabelling it.
    """
    return CatalogItem.item_type.in_(department.item_types)


def counts(session: Session) -> dict[str, int]:
    """How many things are actually buyable per department slug."""
    rows = session.execute(
        select(CatalogItem.item_type, func.count())
        .join(ShopListing, ShopListing.catalog_item_id == CatalogItem.id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0)
        .group_by(CatalogItem.item_type)
    ).all()
    by_type = {item_type: count for item_type, count in rows}

    bulk = session.scalar(
        select(func.count()).select_from(BulkLot)
        .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
    ) or 0

    totals = {}
    for department in DEPARTMENTS:
        total = sum(by_type.get(item_type, 0) for item_type in department.item_types)
        if department.includes_bulk:
            total += bulk
        totals[department.slug] = total
    return totals


def in_stock(session: Session) -> list[tuple[Department, int]]:
    """Departments with something in them, for navigation."""
    totals = counts(session)
    return [(d, totals[d.slug]) for d in DEPARTMENTS if totals[d.slug] > 0]


def price_range(session: Session, department: Department) -> tuple[Decimal, Decimal] | None:
    """Cheapest and dearest in a department, for the department card."""
    row = session.execute(
        select(func.min(ShopListing.price), func.max(ShopListing.price))
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(
            ShopListing.status == ShopListingStatus.ACTIVE,
            ShopListing.quantity > 0,
            listing_filter(department),
        )
    ).one()
    low, high = row
    if department.includes_bulk:
        bulk_row = session.execute(
            select(func.min(BulkLot.price), func.max(BulkLot.price))
            .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
        ).one()
        low = min([v for v in (low, bulk_row[0]) if v is not None], default=None)
        high = max([v for v in (high, bulk_row[1]) if v is not None], default=None)
    if low is None or high is None:
        return None
    return Decimal(str(low)), Decimal(str(high))
