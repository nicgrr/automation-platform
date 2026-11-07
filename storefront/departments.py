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

from . import live_inventory
from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from automation_control.config import get_settings

from automation_control.models import (
    BulkLot, CardSet, CatalogItem, CatalogItemType, SealedProduct, ShopListing, ShopListingStatus, TcgCard,
)

# How a game's TcgCard.game value ("pokemon") is written for people
# ("Pokémon"). A game not in here falls back to a title-cased version of
# its own code, so a new game still gets a readable name with no edit
# needed here -- this dict is cosmetic, never a gate on what can appear.
# Owner-curated placement of a multi-card display; retain its real product type.
_POKEMON_DISPLAY_IDS = ("pokemon-promo-display-svp079-081",)

GAME_LABELS = {"pokemon": "Pokémon", "one_piece": "One Piece", "dragon_ball": "Dragon Ball"}

# Sealed products entered through Listing Studio store the game as display
# text ("One Piece", "Dragon Ball Super Fusion World") rather than a code.
# Without this they became departments of their own, next to the real ones.
_GAME_ALIASES = {"dragon_ball_super_fusion_world": "dragon_ball_fusion_world"}


def canonical_game(game: str) -> str:
    code = "_".join((game or "").strip().lower().replace("-", " ").replace("é", "e").split())
    return _GAME_ALIASES.get(code, code)


# Several catalogue games are really ranges of one franchise, and a shopper
# looks for "Dragon Ball", not "Dragon Ball Fusion World" vs "Dragon Ball
# Super Divers". Games are grouped into one department by franchise, and the
# ranges inside it become sub-categories (see `subranges`).
def _group_of(game: str) -> str:
    game = canonical_game(game)
    if "pokemon" in game:
        return "pokemon"
    if "dragon_ball" in game:
        return "dragon_ball"
    return game


# Sub-category names inside a franchise. A range not listed here falls back
# to its code, title-cased.
SUB_LABELS = {
    "pokemon": "English", "pokemon_japan": "Japanese",
    "dragon_ball_super": "Dragon Ball Super", "dragon_ball_fusion_world": "Fusion World",
    "dragon_ball_super_divers": "Super Divers", "super_dragon_ball_heroes": "Super Dragon Ball Heroes",
    "dragon_ball_artbox": "Artbox", "dragon_ball_carddass": "Carddass", "dragon_ball_itajaga": "Itajaga",
}

# Navigation order. Anything not listed (a game scanned in later) follows.
_ORDER = ("game-pokemon", "game-one_piece", "game-dragon_ball", "collectibles", "sealed", "other")

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
    game: str | None = None       # the franchise this department is (slug, logo, label)
    includes_bulk: bool = False   # BulkLot rows count too (scoped by `game` if set)
    games: tuple[str, ...] = ()   # the catalogue games it covers; empty means just `game`
    by_franchise: bool = False    # spans every game; tabs narrow it to one franchise
    sealed_supplies: str = ""     # "include" / "exclude" sealed sleeve packs (see sealed_supply_ids)

    @property
    def game_codes(self) -> tuple[str, ...]:
        return self.games or ((self.game,) if self.game else ())


FIXED_DEPARTMENTS: tuple[Department, ...] = (
    Department(
        slug="collectibles",
        name="Collectables",
        blurb="Sonny Angel, Smiski and blind-box figures, secrets included.",
        item_types=(CatalogItemType.COLLECTIBLE,),
    ),
    Department(
        slug="sealed",
        name="Sealed products",
        blurb="Booster boxes, bundles, tins and decks from every game.",
        item_types=(CatalogItemType.SEALED_PRODUCT,),
        by_franchise=True, sealed_supplies="include",
    ),
    Department(
        slug="other",
        name="Other",
        blurb="Apparel, accessories and everything else.",
        item_types=(CatalogItemType.APPAREL, CatalogItemType.ACCESSORY, CatalogItemType.OTHER),
        sealed_supplies="exclude",
    ),
)

# Old department slugs, kept working for bookmarks and search results.
RENAMED_SLUGS = {"apparel": "other"}

_TCG_ITEM_TYPES = (CatalogItemType.TCG_CARD, CatalogItemType.SEALED_PRODUCT, CatalogItemType.GRADED_CARD)
# Sealed product lives only in the Sealed products department, all games
# together (the owner's call); a game department holds its singles and bulk.
_GAME_ITEM_TYPES = (CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD)


def sealed_supply_ids():
    """Card-sleeve packs. They're catalogued as accessories (the eBay
    category and Sleeve Studio rely on that), but they're sold sealed, so
    the shop lists them with sealed product. Their brand stands in for a
    game when narrowing by franchise."""
    from automation_control.models import AccessoryProduct

    return select(AccessoryProduct.catalog_item_id).where(
        func.lower(AccessoryProduct.product_type) == "card sleeves")


def _game_blurb(group: str) -> str:
    return f"{_game_label(group)} singles and sorted bulk by set."


def _game_label(game: str) -> str:
    return GAME_LABELS.get(game, game.replace("_", " ").title())


def _active_games(session: Session) -> list[str]:
    """Every TCG game with at least one active single or bulk lot -- the
    set of games actually worth their own department. Sealed product isn't
    counted: it has its own department (see FIXED_DEPARTMENTS)."""
    from_cards = session.scalars(
        select(TcgCard.game).distinct()
        .join(CatalogItem, CatalogItem.id == TcgCard.catalog_item_id)
        .join(ShopListing, ShopListing.catalog_item_id == CatalogItem.id)
        .where(live_inventory.available())
    ).all()
    from_bulk = session.scalars(
        select(BulkLot.game).distinct()
        .where(BulkLot.status == ShopListingStatus.ACTIVE, BulkLot.quantity > 0)
    ).all()
    games = {g for g in (*from_cards, *from_bulk) if g}
    if session.scalar(select(ShopListing.id).where(
            live_inventory.available(), ShopListing.catalog_item_id.in_(_POKEMON_DISPLAY_IDS)).limit(1)):
        games.add("pokemon")
    return sorted(games)


def department_list(session: Session) -> list[Department]:
    """Every department that could plausibly exist -- filtered down to
    what actually has stock happens in in_stock(), not here, so a page
    that needs to resolve a specific slug (a direct link, a bookmark)
    still works even for a department that's currently empty."""
    grouped: dict[str, list[str]] = {}
    for game in _active_games(session):
        grouped.setdefault(_group_of(game), []).append(game)
    game_departments = [
        Department(
            slug=f"game-{group}",
            name=_game_label(group),
            blurb=_game_blurb(group),
            item_types=_GAME_ITEM_TYPES, game=group, includes_bulk=True, games=tuple(sorted(games)),
        )
        for group, games in grouped.items()
    ]
    everything = game_departments + list(FIXED_DEPARTMENTS)
    return sorted(everything, key=lambda d: (_ORDER.index(d.slug) if d.slug in _ORDER else len(_ORDER), d.name))


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
    if item.id in _POKEMON_DISPLAY_IDS:
        return find(session, "game-pokemon")
    is_sleeve_pack = item.item_type == CatalogItemType.ACCESSORY and session.scalar(
        select(func.count()).select_from(CatalogItem)
        .where(CatalogItem.id == item.id, CatalogItem.id.in_(sealed_supply_ids())))
    if item.item_type == CatalogItemType.SEALED_PRODUCT or is_sleeve_pack:
        return next(d for d in FIXED_DEPARTMENTS if d.slug == "sealed")
    if item.item_type in _TCG_ITEM_TYPES:
        game = session.scalar(select(TcgCard.game).where(TcgCard.catalog_item_id == item.id))
        if game is None:
            game = session.scalar(select(SealedProduct.game).where(SealedProduct.catalog_item_id == item.id))
        if not game:
            return None
        group = _group_of(game)
        return Department(
            slug=f"game-{group}", name=_game_label(group),
            blurb=_game_blurb(group),
            item_types=_GAME_ITEM_TYPES, game=group, includes_bulk=True, games=(game,),
        )
    return next((d for d in FIXED_DEPARTMENTS if item.item_type in d.item_types), None)


def listing_filter(department: Department):
    """A WHERE-clause condition selecting this department's single
    listings, joined against CatalogItem -- a listing whose catalogue row
    is missing resolves to no department and simply doesn't appear,
    rather than defaulting into one and being mislabelled.
    """
    from automation_control.models import AccessoryProduct

    condition = CatalogItem.item_type.in_(department.item_types)
    if department.sealed_supplies == "include":
        condition = condition | CatalogItem.id.in_(sealed_supply_ids())
    elif department.sealed_supplies == "exclude":
        condition = condition & CatalogItem.id.not_in(sealed_supply_ids())
    if department.slug == "other":
        condition = condition & CatalogItem.id.not_in(_POKEMON_DISPLAY_IDS)
    codes = department.game_codes
    if not codes:
        return condition
    scoped = condition & (
        CatalogItem.id.in_(select(TcgCard.catalog_item_id).where(TcgCard.game.in_(codes)))
        | CatalogItem.id.in_(select(SealedProduct.catalog_item_id).where(SealedProduct.game.in_(codes)))
        | CatalogItem.id.in_(select(AccessoryProduct.catalog_item_id).where(AccessoryProduct.brand.in_(codes)))
    )
    if department.game == "pokemon" and "pokemon" in codes:
        scoped = scoped | CatalogItem.id.in_(_POKEMON_DISPLAY_IDS)
    return scoped


def _bulk_filter(department: Department):
    condition = BulkLot.status == ShopListingStatus.ACTIVE
    if department.game_codes:
        condition = condition & BulkLot.game.in_(department.game_codes)
    return condition


def counts(session: Session) -> dict[str, int]:
    """How many things are actually buyable per department slug."""
    totals = {}
    for department in department_list(session):
        singles = session.scalar(
            select(func.count()).select_from(ShopListing)
            .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
            .where(live_inventory.available(),
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
    """Departments with something in them, for navigation -- plus any
    marked coming soon, which keep their tile and link even while their
    items are hidden (callers check is_coming_soon() to label them)."""
    totals = counts(session)
    soon = coming_soon_slugs()
    return [(d, totals[d.slug]) for d in department_list(session) if totals[d.slug] > 0 or d.slug in soon]


def coming_soon_slugs() -> set[str]:
    """Departments switched to "coming soon" via STOREFRONT_COMING_SOON.

    Only the fixed (non-game) departments can be: they're defined by item
    type alone, which is what visible_listing_filter() hides by."""
    raw = get_settings().storefront_coming_soon or ""
    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return wanted & {d.slug for d in FIXED_DEPARTMENTS}


def is_coming_soon(department: Department | None) -> bool:
    return department is not None and department.slug in coming_soon_slugs()


def _hidden_item_types() -> tuple[CatalogItemType, ...]:
    soon = coming_soon_slugs()
    return tuple(t for d in FIXED_DEPARTMENTS if d.slug in soon for t in d.item_types)


def visible_listing_filter():
    """A WHERE condition (against an outer-joined CatalogItem) that drops
    listings belonging to a coming-soon department, so they don't turn up
    in search, browse or the sitemap before they're ready to sell."""
    hidden = _hidden_item_types()
    if not hidden:
        return true()
    return or_(CatalogItem.item_type.is_(None), CatalogItem.item_type.not_in(hidden))


def item_is_hidden(item: CatalogItem | None) -> bool:
    return item is not None and item.item_type in _hidden_item_types()


def price_range(session: Session, department: Department) -> tuple[Decimal, Decimal] | None:
    """Cheapest and dearest in a department, for the department card."""
    row = session.execute(
        select(func.min(live_inventory.price()), func.max(live_inventory.price()))
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(
            live_inventory.available(),
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
        return live_inventory.price() >= PREMIUM_THRESHOLD
    if segment == "sealed":
        return CatalogItem.item_type == CatalogItemType.SEALED_PRODUCT
    if segment == "singles":
        return CatalogItem.item_type.in_((CatalogItemType.TCG_CARD, CatalogItemType.GRADED_CARD))
    return None


def has_sealed_stock(session: Session, department: Department) -> bool:
    """Whether this game's franchise has sealed product in the Sealed
    products department, for the game page's link across to it."""
    return any(_group_of(g) == department.game for g in _sealed_games(session))


def has_premium_stock(session: Session, department: Department) -> bool:
    return bool(session.scalar(
        select(func.count()).select_from(ShopListing)
        .join(CatalogItem, CatalogItem.id == ShopListing.catalog_item_id)
        .where(live_inventory.available(),
              listing_filter(department), live_inventory.price() >= PREMIUM_THRESHOLD)
    ))


def sets_in_stock(session: Session, department: Department) -> list[tuple[str, str]]:
    """(set_id, set_name) pairs with at least one active listing in this
    department, ordered by name. Only a game department has sets at all
    -- a fixed department (Collectibles, Apparel) groups by brand/series
    instead, which isn't modelled here yet, so this returns empty for one
    rather than guessing at a grouping."""
    if not department.game_codes:
        return []
    rows = session.execute(
        select(TcgCard.set_id, CardSet.name).distinct()
        .join(CatalogItem, CatalogItem.id == TcgCard.catalog_item_id)
        .join(ShopListing, ShopListing.catalog_item_id == CatalogItem.id)
        .join(CardSet, CardSet.id == TcgCard.set_id, isouter=True)
        .where(live_inventory.available(),
              TcgCard.game.in_(department.game_codes), TcgCard.set_id.isnot(None))
    ).all()
    pairs = {(set_id, name or set_id) for set_id, name in rows}
    return sorted(pairs, key=lambda pair: pair[1])


def _sub_label(game: str) -> str:
    return SUB_LABELS.get(game) or game.replace("_", " ").title()


def _sealed_games(session: Session) -> list[str]:
    """Raw game values behind every sealed product in stock, plus the brand
    of each sealed sleeve pack."""
    from automation_control.models import AccessoryProduct

    games = session.scalars(
        select(SealedProduct.game).distinct()
        .join(ShopListing, ShopListing.catalog_item_id == SealedProduct.catalog_item_id)
        .join(CatalogItem, CatalogItem.id == SealedProduct.catalog_item_id)
        .where(live_inventory.available(), CatalogItem.item_type == CatalogItemType.SEALED_PRODUCT)
    ).all()
    brands = session.scalars(
        select(AccessoryProduct.brand).distinct()
        .join(ShopListing, ShopListing.catalog_item_id == AccessoryProduct.catalog_item_id)
        .where(live_inventory.available(), AccessoryProduct.catalog_item_id.in_(sealed_supply_ids()))
    ).all()
    return sorted({g for g in (*games, *brands) if g})


def narrowed(department: Department, sub: str, session: Session | None = None) -> Department:
    """The same department restricted to one range ("dragon_ball_fusion_world")
    or, for an all-games department, one franchise ("pokemon"); unchanged when
    `sub` isn't one of its options."""
    from dataclasses import replace

    if department.by_franchise and session is not None:
        codes = tuple(g for g in _sealed_games(session) if _group_of(g) == sub)
        return replace(department, games=codes) if codes else department
    codes = tuple(g for g in department.game_codes if canonical_game(g) == sub)
    return replace(department, games=codes) if codes else department


def subranges(session: Session, department: Department) -> list[tuple[str, str]]:
    """(code, name) tabs: each range of a franchise that has stock, e.g.
    Dragon Ball -> Dragon Ball Super, Fusion World ..., or each franchise in an
    all-games department. Only worth showing when there is more than one."""
    if department.by_franchise:
        groups = sorted({_group_of(g) for g in _sealed_games(session)},
                        key=lambda g: (_ORDER.index(f"game-{g}") if f"game-{g}" in _ORDER else len(_ORDER), g))
        return [(g, _game_label(g)) for g in groups] if len(groups) > 1 else []
    active = {canonical_game(g) for g in _active_games(session)}
    ranges = list(dict.fromkeys(canonical_game(g) for g in department.game_codes))
    ranges = [(code, _sub_label(code)) for code in ranges if code in active]
    return ranges if len(ranges) > 1 else []


def brands_in_stock(session: Session) -> list[str]:
    """Collectable brands (Sonny Angel, Smiski ...) with an active listing."""
    from automation_control.models import CollectibleProduct

    return sorted(session.scalars(
        select(CollectibleProduct.brand).distinct()
        .join(ShopListing, ShopListing.catalog_item_id == CollectibleProduct.catalog_item_id)
        .where(live_inventory.available(),
              CollectibleProduct.brand.isnot(None), CollectibleProduct.brand != "")
    ).all(), key=str.lower)


def brand_filter(brand: str):
    from automation_control.models import CollectibleProduct

    return ShopListing.catalog_item_id.in_(
        select(CollectibleProduct.catalog_item_id).where(func.lower(CollectibleProduct.brand) == brand.lower())
    )
