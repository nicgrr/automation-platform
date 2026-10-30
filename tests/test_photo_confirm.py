from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    InventoryStatus,
    ListingPhotoBatch,
    ListingPhotoPair,
    ListingPhotoPairStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)
from automation_control.photo_confirm import catalogue_guess, guess_finish

CONFIRM = "/ebay-listing-bulk-photos/batch/confirm"


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/confirm.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # Production enforces this from a migration; the model doesn't declare it.
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE UNIQUE INDEX uq_inventory_card_finish ON inventory_items '
                             '(catalog_item_id, variant, condition)')
    with Session(engine) as s:
        s.add(CardSet(id="wht", name="White Flare", game="pokemon"))
        s.add(CardSet(id="cn", name="Chasing Glory Together", game="pokemon"))
        s.add(CatalogItem(id="trubbish", name="Trubbish", item_type=CatalogItemType.TCG_CARD))
        s.add(CatalogItem(id="mamoswine", name="Mamoswine ex", item_type=CatalogItemType.TCG_CARD))
        s.flush()
        s.add(TcgCard(catalog_item_id="trubbish", game="pokemon", set_id="wht", number="059/086", language="English", rarity="Common"))
        s.add(TcgCard(catalog_item_id="mamoswine", game="pokemon", set_id="cn", number="104/222", language="Simplified Chinese", rarity="RR"))
        s.add(ListingPhotoBatch(id="batch", total_pairs=3))
        s.flush()
        s.add(ListingPhotoPair(id="p1", batch_id="batch", position=1, front_image_path="/front1.jpg",
                               status=ListingPhotoPairStatus.NEEDS_REVIEW, proposed_catalog_item_id="trubbish",
                               extracted={"character": "Trubbish", "card_number": "059/086",
                                          "foil_observation": "Reverse-holo pattern of Master Ball shapes"}))
        s.add(ListingPhotoPair(id="p2", batch_id="batch", position=2, front_image_path="/front2.jpg",
                               status=ListingPhotoPairStatus.NEEDS_REVIEW,
                               extracted={"character": "象牙猪ex (Mamoswine ex)", "card_number": "104/222",
                                          "language": "Chinese (Simplified)"}))
        s.add(ListingPhotoPair(id="p3", batch_id="batch", position=3, front_image_path="/front3.jpg",
                               status=ListingPhotoPairStatus.NEEDS_REVIEW,
                               extracted={"character": "可达鸭 (Psyduck)", "card_number": "043/222",
                                          "language": "Chinese (Simplified)"}))
        s.commit()
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[require_dashboard_user] = lambda: "test"
        # No `with`: entering TestClient runs the lifespan's production schema check.
        yield s, TestClient(app)
        app.dependency_overrides.clear()


def _confirm(client, pair_id, **data):
    return client.post(f"{CONFIRM}/{pair_id}", data=data, follow_redirects=False)


def test_finish_is_preselected_from_the_photo_notes():
    assert guess_finish({"foil_observation": "Reverse-holo pattern of Master Ball shapes"}) == "masterball_pattern"
    assert guess_finish({"foil_observation": "Reverse-holo Poké Ball pattern"}) == "pokeball_pattern"
    assert guess_finish({"foil_observation": "Reverse-holo sheen across the border"}) == "reverse_holo"
    assert guess_finish({}) == "normal"


def test_a_bilingual_name_and_printed_number_find_the_catalogue_card(context):
    s, _ = context
    item, card = catalogue_guess(s, s.get(ListingPhotoPair, "p2"))
    assert item.id == "mamoswine"


def test_the_page_shows_the_first_held_card_with_its_guessed_finish(context):
    _, client = context
    body = client.get(CONFIRM).text
    assert "Trubbish" in body and "059/086" in body
    assert "value='masterball_pattern' checked" in body
    assert "/ebay-listing-bulk-photos/batch/image/p1/front" in body
    assert "Extra copy" in body


def test_an_extra_copy_adds_stock_and_a_draft_then_moves_on(context):
    s, client = context
    resp = _confirm(client, "p1", catalog="trubbish", finish="masterball_pattern", choice="extra", quantity="1")
    assert resp.status_code == 303 and resp.headers["location"].startswith(CONFIRM + "?done=")
    stock = s.scalars(select(InventoryItem).where(InventoryItem.catalog_item_id == "trubbish")).one()
    assert (stock.variant, stock.quantity, stock.status) == (CardVariant.MASTERBALL_PATTERN, 1, InventoryStatus.AVAILABLE)
    listing = s.scalars(select(ShopListing).where(ShopListing.inventory_item_id == stock.id)).one()
    assert listing.status == ShopListingStatus.DRAFT and listing.finish == "masterball_pattern"
    assert s.get(ListingPhotoPair, "p1").status == ListingPhotoPairStatus.APPLIED
    assert "Mamoswine" in client.get(CONFIRM).text  # next card comes up


def test_a_card_already_in_stock_gets_a_draft_and_its_finish_corrected(context):
    s, client = context
    s.add(InventoryItem(id="inv", catalog_item_id="trubbish", variant=CardVariant.REVERSE_HOLO,
                        condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE))
    s.commit()
    assert "value='stock:inv'" in client.get(CONFIRM).text
    _confirm(client, "p1", catalog="trubbish", finish="pokeball_pattern", choice="stock:inv")
    s.expire_all()
    assert s.get(InventoryItem, "inv").variant == CardVariant.POKEBALL_PATTERN
    assert s.scalar(select(func.count()).select_from(InventoryItem)) == 1  # no extra stock
    assert s.scalars(select(ShopListing).where(ShopListing.inventory_item_id == "inv")).one().status == ShopListingStatus.DRAFT


def test_a_new_photo_for_an_existing_listing_keeps_stock_and_price(context):
    s, client = context
    s.add(InventoryItem(id="inv", catalog_item_id="mamoswine", variant=CardVariant.HOLO,
                        condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE))
    s.add(ShopListing(id="live", catalog_item_id="mamoswine", inventory_item_id="inv", title="Mamoswine ex",
                      price=Decimal("7.50"), currency="AUD", quantity=1, status=ShopListingStatus.ACTIVE))
    s.commit()
    _confirm(client, "p2", catalog="mamoswine", finish="holo", choice="existing:live")
    s.expire_all()
    listing = s.get(ShopListing, "live")
    assert (listing.price, listing.quantity, listing.status) == (Decimal("7.50"), 1, ShopListingStatus.ACTIVE)
    assert listing.ebay_photo_paths[0] == "/front2.jpg"
    assert s.scalar(select(func.count()).select_from(InventoryItem)) == 1


def test_a_card_missing_from_the_catalogue_can_be_added_into_its_set(context):
    s, client = context
    for pid in ("p1", "p2"):
        s.get(ListingPhotoPair, pid).status = ListingPhotoPairStatus.APPLIED
    s.commit()
    body = client.get(CONFIRM).text
    assert "Not in the catalogue yet" in body and "Chasing Glory Together" in body
    client.post(f"{CONFIRM}/p3/catalogue", data={"name": "Psyduck", "number": "043/222", "set_id": "cn",
                                                  "language": "Simplified Chinese", "rarity": "Common"})
    s.expire_all()
    cid = s.get(ListingPhotoPair, "p3").proposed_catalog_item_id
    assert s.get(TcgCard, cid).set_id == "cn"
    assert "Psyduck" in client.get(CONFIRM).text


def test_a_second_tap_on_confirm_changes_nothing(context):
    s, client = context
    _confirm(client, "p1", catalog="trubbish", finish="masterball_pattern", choice="extra", quantity="1")
    _confirm(client, "p1", catalog="trubbish", finish="masterball_pattern", choice="extra", quantity="1")
    assert s.scalar(select(func.count()).select_from(InventoryItem)) == 1


def test_skipping_shows_the_next_card(context):
    _, client = context
    assert "Mamoswine" in client.get(CONFIRM + "?skip=1").text


def test_a_number_without_its_set_total_prefers_the_entry_you_hold(context):
    s, _ = context
    s.add(CatalogItem(id="dup", name="Mamoswine ex", item_type=CatalogItemType.TCG_CARD))
    s.flush()
    s.add(TcgCard(catalog_item_id="dup", game="pokemon", set_id="cn", number="104", language="Chinese", rarity="RR"))
    s.add(InventoryItem(id="held", catalog_item_id="mamoswine", variant=CardVariant.HOLO,
                        condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE))
    pair = s.get(ListingPhotoPair, "p2")
    pair.extracted = {**pair.extracted, "card_number": "104"}
    s.commit()
    item, _ = catalogue_guess(s, pair)
    assert item.id == "mamoswine"


def test_two_equally_likely_entries_are_not_guessed(context):
    s, _ = context
    s.add(CatalogItem(id="dup", name="Mamoswine ex", item_type=CatalogItemType.TCG_CARD))
    s.flush()
    s.add(TcgCard(catalog_item_id="dup", game="pokemon", set_id="cn", number="104", language="Chinese", rarity="RR"))
    pair = s.get(ListingPhotoPair, "p2")
    pair.extracted = {**pair.extracted, "card_number": "104"}
    s.commit()
    assert catalogue_guess(s, pair) is None


def test_an_extra_copy_of_a_finish_already_on_record_adds_to_that_record(context):
    s, client = context
    s.add(InventoryItem(id="bulked", catalog_item_id="trubbish", variant=CardVariant.MASTERBALL_PATTERN,
                        condition="Near Mint", quantity=0, status=InventoryStatus.RESERVED))
    s.commit()
    _confirm(client, "p1", catalog="trubbish", finish="masterball_pattern", choice="extra", quantity="1")
    s.expire_all()
    stock = s.scalars(select(InventoryItem).where(InventoryItem.catalog_item_id == "trubbish")).all()
    assert [(i.id, i.quantity, i.status) for i in stock] == [("bulked", 1, InventoryStatus.AVAILABLE)]
    assert s.get(ListingPhotoPair, "p1").status == ListingPhotoPairStatus.APPLIED


def test_correcting_a_finish_onto_an_existing_record_asks_instead_of_crashing(context):
    s, client = context
    s.add(InventoryItem(id="rev", catalog_item_id="trubbish", variant=CardVariant.REVERSE_HOLO,
                        condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE))
    s.add(InventoryItem(id="ball", catalog_item_id="trubbish", variant=CardVariant.POKEBALL_PATTERN,
                        condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE))
    s.commit()
    resp = _confirm(client, "p1", catalog="trubbish", finish="pokeball_pattern", choice="stock:rev")
    assert "error=" in resp.headers["location"]
    s.expire_all()
    assert s.get(InventoryItem, "rev").variant == CardVariant.REVERSE_HOLO
    assert s.get(ListingPhotoPair, "p1").status == ListingPhotoPairStatus.NEEDS_REVIEW
