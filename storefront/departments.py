"""Departments -- the shop's top-level split.

One flat grid of everything worked while the shop was cards only. It
stops working the moment a Sonny Angel sits next to a Charizard, and it
stops working again the moment One Piece sits next to Pokémon: different
buyers come looking for different games, and a shopper who came for one
shouldn't have to scroll past the other.

TCG-like items (singles, sealed product, graded cards) are split one
department PER GAME rather than lumped into one "Trading cards" bucket --
built from whatever `TcgCard.game` / `SealedProduct.game` values actually
have active stock, not a hardcoded list. Scan in a new game and its
department appears on its own the moment something from it is listed; no
code change needed. Collectibles and Apparel stay as fixed, non-game
departments, since collectibles group by series/brand instead of a game.

The split is derived from `CatalogItem.item_type` (and `game`, for TCG
departments) rather than stored as a column on ShopListing. That keeps
one source of truth -- an item is in Pokémon *because* its TcgCard.game
says so, not because someone also remembered to tag the listing -- and
means an item can never appear in two departments or none.

Apparel ships with no stock. That's deliberate and not a placeholder: the
department exists so the first shirt has somewhere to go, and
`in_stock` below keeps empty departments out of the navigation, so the
shop never advertises a section that lands on an empty page.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from automation_control.models import (
    BulkLot, CatalogItem, CatalogItemType, SealedProduct, ShopListing, ShopListingStatus, TcgCard,
)

# How a game's TcgCard.game value ("pokemon") is written for people
# ("Pokémon"). A game not in here falls back to a title-cased version of
# its own code, so a new game still gets a readable name with no edit
# needed here -- this dict is cosmetic, never a gate on what can appear.
GAME_LABELS = {"pokemon": "Pokémon", "one_piece": "One Piece"}

# Singles at or above this are shown in a game department's "Premium"
# segment. A plain constant, not a config table: this is a browsing
# facet, not a financial figure anything downstream depends on.
PREMIUM_THRESHOLD = Decimal("15.00")


@dataclass(frozen=True)
class Department:
    slug: str
    name: str
    blurb: str
    item_types: tuple[CatalogItemType, ...]
    game: str | None = None       # restrict to this TcgCard/SealedProduct game, if set
    includes_bulk: bool = False   # BulkLot rows count too (scoped by `game` if set)


FIXED_DEPARTMENTS: tuple[Department, ...] = (
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

_TCG_ITEM_TYPES = (CatalogItemType.TCG_CARD, CatalogItemType.SEALED_PRODUCT, CatalogItemType.GRADED_CARD)


def _game_label(game: str) -> str:
    return GAME_LABELS.get(game, game.replace("_", " ").title())


def _active_games(session: Session) -> list[str]:
    """Every TCG game with at least one active single, sealed listing, or
    bulk lot -- the set of games actually worth their own department."""
    from_cards = session.scalars(
        select(TcgCard.game).distinct()
        .join(CatalogItem, CatalogItem.id == TcgCard.catalog_item_id)
        .join(ShopListing, ShopListing.catalog_item_id == CatalogItem.id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0)
    ).all()
    from_sealed = session.scalars(
        select(SealedProduct.game).distinct()
        .join(CatalogItem, CatalogItem.id == SealedProduct.catalog_item_id)
        .join(ShopListing, ShopListing.catalog_item_id == CatalogItem.id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
              SealedProduct.game.isnot(None))
    ).all()
    from_bulk = session.scalars(
        select(BulkLot.game).distinct()
        .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
    ).all()
    return sorted({g for g in (*from_cards, *from_sealed, *from_bulk) if g})


def department_list(session: Session) -> list[Department]:
    """Every department that could plausibly exist -- filtered down to
    what actually has stock happens in in_stock(), not here, so a page
    that needs to resolve a specific slug (a direct link, a bookmark)
    still works even for a department that's currently empty."""
    game_departments = [
        Department(
            slug=f"game-{game}",
            name=_game_label(game),
            blurb=f"{_game_label(game)} singles, sealed product and sorted bulk by set.",
            item_types=_TCG_ITEM_TYPES, game=game, includes_bulk=True,
        )
        for game in _active_games(session)
    ]
    return game_departments + list(FIXED_DEPARTMENTS)


def find(session: Session, slug: str) -> Department | None:
    slug = (slug or "").strip().lower()
    return next((d for d in department_list(session) if d.slug == slug), None)


def department_for_item(session: Session, item: CatalogItem | None) -> Department | None:
    """The department a given catalogue item belongs to -- for breadcrumbs
    on a page that only has the item, not the department slug, to work
    from. Built the same way department_list() derives a game department
    (no stored column to join against), so a card's breadcrumb can never
    disagree with which grid it actually shows up in."""
    if item is None:
        return None
    if item.item_type in _TCG_ITEM_TYPES:
        game = session.scalar(select(TcgCard.game).where(TcgCard.catalog_item_id == item.id))
        if game is None:
            game = session.scalar(select(SealedProduct.game).where(SealedProduct.catalog_item_id == item.id))
        if not game:
            return None
        return Department(
            slug=f"game-{game}", name=_game_label(game),
            blurb=f"{_game_label(game)} singles, sealed product and sorted bulk by set.",
            item_types=_TCG_ITEM_TYPES, game=game, includes_bulk=True,
        )
    return next((d for d in FIXED_DEPARTMENTS if item.item_type in d.item_types), None)


def listing_filter(department: Department):
    """A WHERE-clause condition selecting this department's single
    listings, joined against CatalogItem -- a listing whose catalogue row
    is missing resolves to no department and simply doesn't appear,
    rather than defaulting into one and being mislabelled.
    """
    condition = CatalogItem.item_type.in_(department.item_types)
    if department.game is None:
        return condition
    return condition & (
        CatalogItem.id.in_(select(TcgCard.catalog_item_id).where(TcgCard.game == department.game))
        | CatalogItem.id.in_(select(SealedProduct.catalog_item_id).where(SealedProduct.game == department.game))
    )


def _bulk_filter(department: Department):
    condition = BulkLot.status == ShopListingStatus.ACTIVE
    if department.game:
        condition = condition & (BulkLot.game == department.game)
    return condition


def counts(session: Session) -> dict[str, int]:
    """How many things are actually buyable per department slug."""
    totals = {}
    for department in department_list(session):
        singles = session.scalar(
            select(func.count()).select_from(ShopListing)
            .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
                  listing_filter(department))
        ) or 0
        total = singles
        if department.includes_bulk:
            total += session.scalar(
                select(func.count()).select_from(BulkLot)
                .where(_bulk_filter(department), BulkLot.quantity > 0)
            ) or 0
        totals[department.slug] = total
    return totals


def in_stock(session: Session) -> list[tuple[Department, int]]:
    """Departments with something in them, for navigation."""
    totals = counts(session)
    return [(d, totals[d.slug]) for d in department_list(session) if totals[d.slug] > 0]


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
            .where(_bulk_filter(department), BulkLot.quantity > 0)
        ).one()
        low = min([v for v in (low, bulk_row[0]) if v is not None], default=None)
        high = max([v for v in (high, bulk_row[1]) if v is not None], default=None)
    if low is None or high is None:
        return None
    return Decimal(str(low)), Decimal(str(high))


SEGMENTS = {
    "all": "Everything",
    "singles": "Singles",
    "premium": f"Premium (${PREMIUM_THRESHOLD:,.0f}+)",
    "sealed": "Sealed product",
}


def segment_filter(department: Department, segment: str):
    """A further WHERE condition narrowing a game department's singles by
    segment -- price tier or sealed-vs-not. Returns None for a segment
    that adds no extra condition ("all"), or one that isn't valid for
    this department, rather than raising: an unrecognised segment behaves
    like "all" instead of erroring the page.
    """
    if segment == "premium":
        return ShopListing.price >= PREMIUM_THRESHOLD
    if segment == "sealed":
        return CatalogItem.item_type == CatalogItemType.SEALED_PRODUCT
    if segment == "singles":
        return CatalogItem.item_type.in_((CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD))
    return None


def has_sealed_stock(session: Session, department: Department) -> bool:
    return bool(session.scalar(
        select(func.count()).select_from(ShopListing)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
              listing_filter(department), CatalogItem.item_type == CatalogItemType.SEALED_PRODUCT)
    ))


def has_premium_stock(session: Session, department: Department) -> bool:
    return bool(session.scalar(
        select(func.count()).select_from(ShopListing)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(ShopListing.status == ShopListingStatus.ACTIVE, ShopListing.quantity > 0,
              listing_filter(department), ShopListing.price >= PREMIUM_THRESHOLD)
    ))
