from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base
from automation_control.models import (
    BulkLot, CardSet, CardVariant, CatalogItem, CatalogItemType, InventoryItem, ShopListing, ShopListingStatus, TcgCard,
)
from scripts.export_ebay_bulk_listing import (
    build_lot_rows, build_set_grouped_rows, build_single_rows, build_variation_rows, lot_shipping_policy,
    single_image_url,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _item(session, **kwargs):
    item = CatalogItem(id="item-1", name="Test Card", item_type=CatalogItemType.TCG_CARD, **kwargs)
    session.add(item)
    session.commit()
    return item


# --- shipping is by card count, not price -----------------------------

def test_lot_max_cards_boundary_is_inclusive():
    assert lot_shipping_policy(20, 20, "Letter", "Parcel") == "Letter"
    assert lot_shipping_policy(21, 20, "Letter", "Parcel") == "Parcel"


def test_a_single_card_listing_is_always_letter_regardless_of_price(session):
    """One card is one card whatever it's worth -- price never enters
    into which shipping policy a single-card listing gets."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
    session.add(ShopListing(catalog_item_id=item.id, title="Cheap Card", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.add(ShopListing(catalog_item_id=item.id, title="Pricey Card", price=Decimal("500"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter Mail")
    assert {r["shipping_policy"] for r in rows} == {"Letter Mail"}


def test_lot_weight_scales_with_card_count_for_calculated_shipping(session):
    session.add(BulkLot(title="Small Lot", card_count=10, price=Decimal("8"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.add(BulkLot(title="Huge Lot", card_count=185, price=Decimal("150"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter", parcel_policy="Parcel")
    by_title = {r["title"]: r["weight_grams"] for r in rows}
    assert by_title["Small Lot"] == 10 * 3 + 80
    assert by_title["Huge Lot"] == 185 * 3 + 80


def test_bulk_lots_are_tiered_by_card_count(session):
    session.add(BulkLot(title="Thin Lot", card_count=18, price=Decimal("15"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.add(BulkLot(title="Big Lot", card_count=140, price=Decimal("95"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter Mail", parcel_policy="Tracked Parcel")
    by_title = {r["title"]: r["shipping_policy"] for r in rows}
    assert by_title["Thin Lot"] == "Letter Mail"
    assert by_title["Big Lot"] == "Tracked Parcel"


# --- image resolution ---------------------------------------------------

def test_single_image_url_prefers_the_listings_own_hosted_image(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="https://example.test/listing.jpg")
    assert single_image_url(session, listing, item) == "https://example.test/listing.jpg"


def test_single_image_url_falls_back_to_the_catalog_items_image(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"), image_url=None)
    assert single_image_url(session, listing, item) == "https://example.test/item.jpg"


def test_a_local_cache_path_is_never_treated_as_a_usable_image_url(session):
    """Regression guard for the exact bug storefront.common._is_web_url exists
    to catch: a local scan-ingest cache path is not something eBay's own
    servers could ever fetch (this app is Tailscale-only)."""
    item = _item(session, image_url="scan_ingest_data/cache/xy1/4.png")
    listing = ShopListing(catalog_item_id=item.id, title="Test Card", price=Decimal("5"),
                          image_url="scan_ingest_data/cache/xy1/4-local.png")
    assert single_image_url(session, listing, item) is None


def test_a_lot_with_a_local_image_path_is_skipped_not_broken(session):
    session.add(BulkLot(title="No Photo Lot", card_count=10, price=Decimal("8"), image_url=None, status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                                   letter_policy="Letter", parcel_policy="Parcel")
    assert rows == []
    assert "No Photo Lot" in skipped[0]


# --- row building skips what it can't safely export ---------------------

def test_build_single_rows_skips_listings_with_no_usable_image(session):
    item = _item(session, image_url=None)
    session.add(ShopListing(catalog_item_id=item.id, title="No Photo Card", price=Decimal("5"), image_url=None,
                            status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "No Photo Card" in skipped[0]


def test_build_single_rows_includes_a_different_games_card(session):
    """Fixed 2026-09-22 (Nic explicitly asked for this once Dragon Ball
    listings existed): any TCG card qualifies, not just Pokemon -- a
    Dragon Ball card is included, carrying its own "game" so
    convert_row's GAME_ITEM_SPECIFICS can label it correctly instead of
    the old hardcoded Franchise=Pokémon on every row."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="dragon_ball_super_divers"))
    session.add(ShopListing(catalog_item_id=item.id, title="Son Goku", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert skipped == []
    assert rows[0]["game"] == "dragon_ball_super_divers"


def test_build_single_rows_excludes_an_item_with_no_tcgcard_row_at_all(session):
    """A Collectible/Accessory/etc. has no TcgCard row at all -- still
    excluded, since this export is card-shaped, not catalogue-wide,
    regardless of which game a real card would have belonged to."""
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(ShopListing(catalog_item_id=item.id, title="Ultra Pro Sleeves", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "not a TCG card" in skipped[0]


def test_build_single_rows_only_exports_the_requested_status(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
    session.add(ShopListing(catalog_item_id=item.id, title="Active Card", price=Decimal("5"), status=ShopListingStatus.ACTIVE))
    session.add(ShopListing(catalog_item_id=item.id, title="Draft Card", price=Decimal("5"), status=ShopListingStatus.DRAFT))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Active Card"]


def test_single_row_set_name_comes_from_the_cards_own_set(session):
    session.add(CardSet(id="xy11", name="Steam Siege"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["set_name"] == "Steam Siege"


def test_lot_row_set_name_comes_from_the_lots_own_card_set(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(BulkLot(title="Obsidian Flames — bulk lot (21 cards)", set_id="sv3", card_count=21,
                        price=Decimal("15"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_lot_rows(session, status=ShopListingStatus.ACTIVE, lot_max_cards=20,
                             letter_policy="Letter", parcel_policy="Parcel")
    assert rows[0]["set_name"] == "Obsidian Flames"


# --- excluding singles already covered by an active bulk lot -----------

def _set_with_lot(session, set_id="sv3", set_name="Obsidian Flames"):
    session.add(CardSet(id=set_id, name=set_name))
    session.add(BulkLot(title=f"{set_name} — bulk lot (30 cards)", set_id=set_id, card_count=30,
                        price=Decimal("20"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE))


def test_a_cheap_single_from_a_lot_covered_set_is_excluded(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, skipped = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows == []
    assert "already covered by an active bulk lot" in skipped[0]


def test_a_single_above_the_lot_threshold_from_a_lot_covered_set_is_still_exported(session):
    """A card priced above the bulk lot's own price ceiling was never
    eligible for the lot in the first place, so it isn't the same
    physical card and is safe to sell individually."""
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="199"))
    session.add(ShopListing(catalog_item_id=item.id, title="Rare #199", price=Decimal("50"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Rare #199"]


def test_a_cheap_single_from_a_set_with_no_lot_is_still_exported(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="no-lot-set", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert [r["title"] for r in rows] == ["Common #4"]


def test_passing_none_disables_the_lot_overlap_check(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter",
                                lot_overlap_max_price=None)
    assert [r["title"] for r in rows] == ["Common #4"]


# --- build_variation_rows: the exact population build_single_rows excludes --

def test_variation_rows_is_exactly_what_single_rows_excludes(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    singles, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert singles == []
    assert [r["title"] for r in variations] == ["Common #4"]


def test_variation_rows_discounts_the_normal_printing_only(session):
    """Per the user's request (2026-09-21): individual market pricing
    stays, but a Normal (non-foil) printing -- the least desirable, bulk
    of a set's filler -- undercuts a bit. Reverse Holo/Holo/pattern
    printings of the same card are never touched."""
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    normal_inv = InventoryItem(card_id=item.id, variant=CardVariant.NORMAL, condition="Near Mint", quantity=1)
    rh_inv = InventoryItem(card_id=item.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=1)
    session.add_all([normal_inv, rh_inv])
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4 Normal", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=normal_inv.id))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4 RH", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=rh_inv.id))
    session.commit()

    rows, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter",
                                   normal_discount_pct=Decimal("40"))
    by_title = {r["title"]: r["price"] for r in rows}
    assert by_title["Common #4 Normal"] == "1.20"  # 40% off $2.00
    assert by_title["Common #4 RH"] == "2.00"  # untouched


def test_variation_rows_discount_defaults_to_forty_percent(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="4"))
    inv = InventoryItem(card_id=item.id, variant=CardVariant.NORMAL, condition="Near Mint", quantity=1)
    session.add(inv)
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2.00"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=inv.id))
    session.commit()

    rows, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["price"] == "1.20"


def test_variation_rows_ignores_a_card_above_max_price_even_in_a_lot_covered_set(session):
    _set_with_lot(session)
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="sv3", number="199"))
    session.add(ShopListing(catalog_item_id=item.id, title="Rare #199", price=Decimal("50"), status=ShopListingStatus.ACTIVE))
    session.commit()

    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert variations == []


def test_variation_rows_ignores_a_cheap_card_from_a_set_with_no_lot(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="no-lot-set", number="4"))
    session.add(ShopListing(catalog_item_id=item.id, title="Common #4", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    variations, _ = build_variation_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert variations == []


# --- variant and set logo, for variation-listing labels/pictures --------

def test_single_variant_comes_from_the_listings_own_inventory_item(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    inv = InventoryItem(card_id=item.id, variant=CardVariant.REVERSE_HOLO, condition="Near Mint", quantity=1)
    session.add(inv)
    session.commit()
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"),
                            status=ShopListingStatus.ACTIVE, inventory_item_id=inv.id))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["variant"] == "Reverse Holo"


def test_single_variant_is_blank_with_no_linked_inventory_item(session):
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["variant"] == ""


def test_single_set_logo_url_comes_from_the_cards_set(session):
    session.add(CardSet(id="xy11", name="Steam Siege", logo_url="https://example.test/xy11-logo.png"))
    item = _item(session, image_url="https://example.test/item.jpg")
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id="xy11", number="54"))
    session.add(ShopListing(catalog_item_id=item.id, title="Abra #54", price=Decimal("2"), status=ShopListingStatus.ACTIVE))
    session.commit()

    rows, _ = build_single_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter")
    assert rows[0]["set_logo_url"] == "https://example.test/xy11-logo.png"


# --- build_set_grouped_rows: one variation listing per set at min_set_size,
# no price ceiling, no Bulk Master lot dependency -- Nic's own request
# 2026-09-22 ("every set becomes one listing"), independent of build_variation_rows
# above (still cheap-commons-in-an-active-lot only, for whatever else uses it) --

def _card(session, item_id: str, *, set_id: str, number: str, title: str, price: str, game: str = "pokemon", rarity: str | None = "Common"):
    # rarity defaults to "Common" -- the eBay-fee-markup tests below rely
    # on that default meaning "not marked up" for a plain Pokemon card;
    # the discount/no-discount tests above this section don't care about
    # rarity at all, so the default keeps them unaffected by the markup
    # feature added after they were written.
    session.add(CatalogItem(id=item_id, name=title, item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number=number, rarity=rarity))
    session.add(ShopListing(catalog_item_id=item_id, title=title, price=Decimal(price), status=ShopListingStatus.ACTIVE))


def test_set_grouped_rows_groups_a_set_at_or_above_min_size(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    _card(session, "c1", set_id="sv3", number="1", title="Common #1", price="0.50")
    _card(session, "c2", set_id="sv3", number="2", title="Common #2", price="0.50")
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert singles == []
    assert {r["title"] for r in groups["Obsidian Flames"]} == {"Common #1", "Common #2"}


def test_set_grouped_rows_leaves_a_smaller_set_as_singles(session):
    session.add(CardSet(id="sv4", name="Paldean Fates"))
    _card(session, "c1", set_id="sv4", number="1", title="Rare #1", price="50.00")
    session.commit()

    singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert groups == {}
    assert [r["title"] for r in singles] == ["Rare #1"]


def test_set_grouped_rows_applies_no_price_discount(session):
    """Unlike build_variation_rows, every card keeps its own real price --
    Nic's explicit choice (2026-09-22) once expensive cards join the
    grouping too, not just the cheap commons that function discounts."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    _card(session, "c1", set_id="sv3", number="1", title="Expensive Card", price="120.00")
    _card(session, "c2", set_id="sv3", number="2", title="Another Card", price="80.00")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    by_title = {r["title"]: r["price"] for r in groups["Obsidian Flames"]}
    assert by_title == {"Expensive Card": "120.00", "Another Card": "80.00"}


def test_set_grouped_rows_ignores_bulk_lot_status_entirely(session):
    """No active Bulk Master lot needed -- purely a set-size cutoff,
    unlike build_variation_rows's lot-gating."""
    session.add(CardSet(id="sv5", name="No Lot Set"))
    _card(session, "c1", set_id="sv5", number="1", title="Card A", price="5.00")
    _card(session, "c2", set_id="sv5", number="2", title="Card B", price="5.00")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert len(groups["No Lot Set"]) == 2


# --- eBay fee markup: Dragon Ball (any rarity) + rare-or-better Pokemon --
# Nic's request 2026-09-22, added to build_set_grouped_rows only --
# build_single_rows itself keeps returning the real, unmodified price
# (see the test right above this section, and build_single_rows' own
# tests elsewhere in this file).

def test_set_grouped_rows_marks_up_a_dragon_ball_card_regardless_of_rarity(session):
    session.add(CardSet(id="dbsd-sdv11", name="Dragon Ball Super Divers SDV11"))
    _card(session, "c1", set_id="dbsd-sdv11", number="1", title="Card A", price="4.02", game="dragon_ball_super_divers")
    _card(session, "c2", set_id="dbsd-sdv11", number="2", title="Card B", price="3.08", game="dragon_ball_super_divers")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    by_title = {r["title"]: r["price"] for r in groups["Dragon Ball Super Divers SDV11"]}
    assert by_title == {"Card A": "4.54", "Card B": "3.48"}  # +13%, rounded


def test_set_grouped_rows_marks_up_a_rare_pokemon_card(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(TcgCard(catalog_item_id="c1", game="pokemon", set_id="sv3", number="1", rarity="Rare"))
    session.add(CatalogItem(id="c1", name="Rare Card", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(ShopListing(catalog_item_id="c1", title="Rare Card", price=Decimal("10.00"), status=ShopListingStatus.ACTIVE))
    session.add(TcgCard(catalog_item_id="c2", game="pokemon", set_id="sv3", number="2", rarity="Rare"))
    session.add(CatalogItem(id="c2", name="Rare Card 2", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
    session.add(ShopListing(catalog_item_id="c2", title="Rare Card 2", price=Decimal("10.00"), status=ShopListingStatus.ACTIVE))
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert groups["Obsidian Flames"][0]["price"] == "11.30"  # +13% of $10.00


def test_set_grouped_rows_does_not_mark_up_common_or_uncommon_pokemon(session):
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    for i, rarity in enumerate(["Common", "Uncommon"], start=1):
        session.add(TcgCard(catalog_item_id=f"c{i}", game="pokemon", set_id="sv3", number=str(i), rarity=rarity))
        session.add(CatalogItem(id=f"c{i}", name=f"Card {i}", item_type=CatalogItemType.TCG_CARD, image_url="https://example.test/item.jpg"))
        session.add(ShopListing(catalog_item_id=f"c{i}", title=f"Card {i}", price=Decimal("2.00"), status=ShopListingStatus.ACTIVE))
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert {r["price"] for r in groups["Obsidian Flames"]} == {"2.00"}


def test_markup_also_applies_to_a_single_not_a_group(session):
    """A Dragon Ball card whose set has only 1 active listing (stays a
    single, not a variation group) still gets the markup -- it's applied
    to build_set_grouped_rows' whole row pool before the set-size split."""
    session.add(CardSet(id="dbsd-sdv8", name="Dragon Ball Super Divers SDV8"))
    _card(session, "c1", set_id="dbsd-sdv8", number="1", title="Lone Card", price="10.00", game="dragon_ball_super_divers")
    session.commit()

    singles, _groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    assert singles[0]["price"] == "11.30"


def test_set_grouped_rows_includes_a_non_pokemon_set(session):
    session.add(CardSet(id="dbsd-sdv11", name="Dragon Ball Super Divers SDV11"))
    _card(session, "c1", set_id="dbsd-sdv11", number="SDV11-PUR1", title="Son Goku: BR", price="4.02", game="dragon_ball_super_divers")
    _card(session, "c2", set_id="dbsd-sdv11", number="SDV11-PUR2", title="Son Gohan: SH", price="3.08", game="dragon_ball_super_divers")
    session.commit()

    _singles, groups, _ = build_set_grouped_rows(session, status=ShopListingStatus.ACTIVE, letter_policy="Letter", min_set_size=2)
    rows = groups["Dragon Ball Super Divers SDV11"]
    assert {r["game"] for r in rows} == {"dragon_ball_super_divers"}
