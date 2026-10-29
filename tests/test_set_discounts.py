from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot, CardSet, CatalogItem, CatalogItemType, CollectibleProduct,
    SetDiscountTier, ShippingRate, ShippingZone, ShopListing, ShopListingStatus, TcgCard,
)
from storefront import discounts
from storefront.app import create_app
from storefront.common import CART_COOKIE, cart_lines


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            SetDiscountTier(min_quantity=3, discount_pct=Decimal("5.00"), label="3+"),
            SetDiscountTier(min_quantity=5, discount_pct=Decimal("10.00"), label="5+"),
            SetDiscountTier(min_quantity=10, discount_pct=Decimal("15.00"), label="10+"),
            ShippingRate(zone=ShippingZone.AU, label="Standard", price=Decimal("9.95"),
                        free_over=Decimal("150.00"), is_default=True),
        ])
        db.commit()
        yield db


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_card(session, name, *, set_id="base1", set_name="Base Set", price="5.00", quantity=1):
    session.merge(CardSet(id=set_id, name=set_name))
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id=set_id, number="1"))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=name,
                            price=Decimal(price), currency="AUD", quantity=quantity,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    return listing_id


def add_collectible(session, name, *, series="Fruit Series", price="20.00"):
    item_id = f"i-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE, name=name))
    session.add(CollectibleProduct(catalog_item_id=item_id, brand="Sonny Angel", series=series))
    listing_id = f"l-{name}"
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=name,
                            price=Decimal(price), currency="AUD", quantity=1,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    return listing_id


def add_bulk_lot(session, title="Base Set bulk", price="20.00"):
    lot = BulkLot(set_id="base1", game="pokemon", title=title, card_count=50,
                 price=Decimal(price), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.commit()
    return lot.id


def lines_from_cart(session, cart: dict[str, int]):
    return cart_lines(session, cart)


# --- core grouping / tier maths ---------------------------------------------

def test_below_the_lowest_tier_earns_nothing(session):
    a = add_card(session, "Card A"); b = add_card(session, "Card B")
    lines = lines_from_cart(session, {a: 1, b: 1})
    groups, near = discounts.evaluate(session, lines)
    assert groups == []


def test_three_from_one_set_earns_the_first_tier(session):
    ids = [add_card(session, f"Card {n}") for n in "ABC"]
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, _ = discounts.evaluate(session, lines)
    assert len(groups) == 1
    assert groups[0].discount_pct == Decimal("5.00")
    assert groups[0].quantity == 3
    assert groups[0].discount_amount == Decimal("0.75")  # 5% of $15


def test_the_highest_qualifying_tier_wins_not_the_first(session):
    ids = [add_card(session, f"Card {n}", quantity=1) for n in range(5)]
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, _ = discounts.evaluate(session, lines)
    assert groups[0].discount_pct == Decimal("10.00")


def test_quantity_of_one_line_can_alone_cross_a_tier(session):
    card = add_card(session, "Stack", quantity=5)
    lines = lines_from_cart(session, {card: 5})
    groups, _ = discounts.evaluate(session, lines)
    assert groups[0].discount_pct == Decimal("10.00")
    assert groups[0].quantity == 5


def test_two_different_sets_are_discounted_independently(session):
    a1 = add_card(session, "A1", set_id="s1", set_name="Set One")
    a2 = add_card(session, "A2", set_id="s1", set_name="Set One")
    a3 = add_card(session, "A3", set_id="s1", set_name="Set One")
    b1 = add_card(session, "B1", set_id="s2", set_name="Set Two")
    lines = lines_from_cart(session, {a1: 1, a2: 1, a3: 1, b1: 1})
    groups, _ = discounts.evaluate(session, lines)
    assert len(groups) == 1
    assert groups[0].label == "Set One"


def test_collectibles_group_by_series(session):
    ids = [add_collectible(session, f"Figure {n}") for n in range(3)]
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, _ = discounts.evaluate(session, lines)
    assert len(groups) == 1
    assert groups[0].label == "Fruit Series"


def test_bulk_lots_never_participate(session):
    """A lot already IS a whole set in one line -- discounting it against
    itself would be double-dipping on the same set."""
    ids = [add_card(session, f"Card {n}") for n in "ABC"]
    lot_id = add_bulk_lot(session)
    lines = lines_from_cart(session, {**{i: 1 for i in ids}, f"B:{lot_id}": 1})
    groups, _ = discounts.evaluate(session, lines)
    assert len(groups) == 1
    assert groups[0].quantity == 3  # the lot never inflates this


def test_an_item_with_no_set_on_file_is_left_out_of_grouping(session):
    """Conservative by construction: no recognised set/series means no
    guess, not a discount that shouldn't exist."""
    item_id = "i-loose"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name="Loose"))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id=None))
    session.add(ShopListing(id="l-loose", catalog_item_id=item_id, title="Loose",
                            price=Decimal("5.00"), currency="AUD", quantity=5,
                            status=ShopListingStatus.ACTIVE))
    session.commit()
    lines = lines_from_cart(session, {"l-loose": 5})
    groups, _ = discounts.evaluate(session, lines)
    assert groups == []


def test_no_active_tiers_means_no_discount_at_all(session):
    session.query(SetDiscountTier).delete()
    session.commit()
    ids = [add_card(session, f"Card {n}") for n in "ABCDE"]
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, near = discounts.evaluate(session, lines)
    assert groups == [] and near == []


def test_an_inactive_tier_is_ignored(session):
    session.add(SetDiscountTier(min_quantity=1, discount_pct=Decimal("50.00"),
                                label="broken", is_active=False))
    session.commit()
    ids = [add_card(session, f"Card {n}") for n in "AB"]
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, _ = discounts.evaluate(session, lines)
    assert groups == []  # the inactive 50% tier must not apply at qty 2


# --- near-miss nudge ---------------------------------------------------------

def test_near_the_next_tier_produces_a_nudge_not_a_discount(session):
    ids = [add_card(session, f"Card {n}") for n in "AB"]  # qty 2, tier at 3
    lines = lines_from_cart(session, {i: 1 for i in ids})
    groups, near = discounts.evaluate(session, lines)
    assert groups == []
    assert len(near) == 1
    assert near[0].needed == 1
    assert near[0].next_pct == Decimal("5.00")


def test_far_from_any_tier_produces_no_nudge(session):
    card = add_card(session, "Solo")
    lines = lines_from_cart(session, {card: 1})  # qty 1, tier at 3 -> 2 away
    _, near = discounts.evaluate(session, lines)
    assert near == []


def test_total_discount_sums_every_group(session):
    a = [add_card(session, f"A{n}", set_id="s1", set_name="One", price="10.00") for n in range(3)]
    b = [add_card(session, f"B{n}", set_id="s2", set_name="Two", price="10.00") for n in range(3)]
    lines = lines_from_cart(session, {i: 1 for i in a + b})
    groups, _ = discounts.evaluate(session, lines)
    assert discounts.total_discount(groups) == Decimal("3.00")  # 5% of $30 each


# --- cart page -----------------------------------------------------------

def test_cart_shows_the_earned_discount(client, session):
    ids = [add_card(session, f"Card {n}", price="10.00") for n in "ABC"]
    client.cookies.set(CART_COOKIE, __import__("json").dumps({i: 1 for i in ids}))
    body = client.get("/cart").text
    assert "Set discounts" in body
    assert "Base Set" in body
    assert "5%" in body
    assert "&minus;$1.50" in body or "−$1.50" in body


def test_cart_total_reflects_the_discount(client, session):
    import json

    ids = [add_card(session, f"Card {n}", price="10.00") for n in "ABC"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    body = client.get("/cart").text
    # $30 items - $1.50 discount + $9.95 AU shipping (free-over not hit) = $38.45
    assert "$38.45" in body


def test_cart_shows_a_nudge_when_close(client, session):
    import json

    ids = [add_card(session, f"Card {n}") for n in "AB"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    body = client.get("/cart").text
    assert "1 more from Base Set" in body


# --- checkout ---------------------------------------------------------------

def test_checkout_form_shows_the_discount(client, session):
    import json

    ids = [add_card(session, f"Card {n}", price="10.00") for n in "ABC"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    body = client.get("/checkout").text
    assert "Set discount" in body
    assert "$1.50" in body


def test_checkout_writes_a_discount_order_line(client, session):
    import json

    from sqlalchemy import select

    from automation_control.models import ShopOrder, ShopOrderItem

    ids = [add_card(session, f"Card {n}", price="10.00") for n in "ABC"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    client.post("/checkout", data={"email": "a@b.com", "name": "A B", "mobile": "0400000000", "address": "1 St", "zone": "au"})

    order = session.scalars(select(ShopOrder)).one()
    discount_items = session.scalars(
        select(ShopOrderItem).where(ShopOrderItem.order_id == order.id, ShopOrderItem.unit_price < 0)
    ).all()
    assert len(discount_items) == 1
    assert "Base Set" in discount_items[0].title
    assert discount_items[0].unit_price == Decimal("-1.50")


def test_the_order_total_is_the_discounted_amount(client, session):
    import json

    from sqlalchemy import select

    from automation_control.models import ShopOrder

    ids = [add_card(session, f"Card {n}", price="10.00") for n in "ABC"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    client.post("/checkout", data={"email": "a@b.com", "name": "A B", "mobile": "0400000000", "address": "1 St", "zone": "au"})

    order = session.scalars(select(ShopOrder)).one()
    assert order.items_total == Decimal("28.50")  # $30 - $1.50
    assert order.grand_total == Decimal("38.45")  # + $9.95 AU shipping


def test_reserving_stock_skips_the_discount_line(client, session):
    """A negative-price line must never be treated as a real product when
    stock is reserved at checkout."""
    import json

    from sqlalchemy import select

    from automation_control.models import ShopListing, ShopOrder

    ids = [add_card(session, f"Card {n}", price="10.00", quantity=1) for n in "ABC"]
    client.cookies.set(CART_COOKIE, json.dumps({i: 1 for i in ids}))
    client.post("/checkout", data={"email": "a@b.com", "name": "A B", "mobile": "0400000000", "address": "1 St", "zone": "au"})

    order = session.scalars(select(ShopOrder)).one()
    assert order.stock_reserved_at is not None
    for listing_id in ids:
        listing = session.get(ShopListing, listing_id)
        assert listing.quantity == 0
        assert listing.status == ShopListingStatus.SOLD_OUT
