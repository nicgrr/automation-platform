from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus, TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_listing(session, name, *, status=ShopListingStatus.DRAFT, price="10.00",
                 set_id="sv1", set_name="Scarlet & Violet"):
    session.merge(CardSet(id=set_id, name=set_name))
    item_id = f"i-{name}"
    listing_id = f"l-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game="pokemon", set_id=set_id, number="1"))
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=f"{name} #1",
                            price=Decimal(price), currency="AUD", quantity=1, status=status))
    session.commit()
    return listing_id


# --- the page itself ---------------------------------------------------

def test_shop_listings_page_defaults_to_the_draft_filter(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings").text
    assert "Charizard" in body
    assert "Pikachu" not in body


def test_a_draft_is_actually_visible_here(client, session):
    """The bug this page exists to fix: shop-pricing only ever showed
    ACTIVE, so a DRAFT listing was invisible everywhere in the admin app."""
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft").text
    assert "Charizard" in body
    assert "DRAFT" in body


def test_status_filter_tabs_switch_what_shows(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=active").text
    assert "Pikachu" in body
    assert "Charizard" not in body


def test_search_narrows_by_title(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft&q=char").text
    assert "Charizard" in body
    assert "Pikachu" not in body


def test_the_listing_shows_its_set(client, session):
    make_listing(session, "Charizard", set_id="sv1", set_name="Scarlet & Violet")
    body = client.get("/shop-listings").text
    assert "Scarlet &amp; Violet" in body


def test_the_row_carries_a_mobile_meta_line_with_qty_price_and_status(client, session):
    """Qty/Price/Status get their own columns on desktop but are hidden
    below 680px (see .listing-table media query) -- this duplicate line
    is what actually shows on a phone, so it has to carry real values."""
    make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE, price="12.50")
    body = client.get("/shop-listings?status=active").text
    assert "listing-mobile-meta" in body
    assert "12.50" in body


def test_a_listing_with_no_image_gets_an_empty_thumbnail_placeholder(client, session):
    make_listing(session, "Charizard")
    body = client.get("/shop-listings").text
    assert "listing-thumb-empty" in body


def test_a_listing_with_an_image_shows_it_as_a_thumbnail(client, session):
    listing_id = make_listing(session, "Charizard")
    session.get(ShopListing, listing_id).image_url = "https://example.com/charizard.jpg"
    session.commit()
    body = client.get("/shop-listings").text
    assert f"src='/shop-listings/{listing_id}/thumb'" in body
    assert f"href='/shop-listings/{listing_id}/image'" in body


def test_the_thumb_route_redirects_to_a_remote_image_url(client, session):
    listing_id = make_listing(session, "Charizard")
    session.get(ShopListing, listing_id).image_url = "https://example.com/charizard.jpg"
    session.commit()
    resp = client.get(f"/shop-listings/{listing_id}/thumb", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://example.com/charizard.jpg"


def test_the_image_route_redirects_to_a_remote_image_url(client, session):
    listing_id = make_listing(session, "Charizard")
    session.get(ShopListing, listing_id).image_url = "https://example.com/charizard.jpg"
    session.commit()
    resp = client.get(f"/shop-listings/{listing_id}/image", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://example.com/charizard.jpg"


def test_the_thumb_route_serves_a_local_cache_path(client, session, tmp_path):
    from PIL import Image

    listing_id = make_listing(session, "Charizard")
    img_path = tmp_path / "ref.png"
    Image.new("RGB", (10, 10)).save(img_path)
    session.get(ShopListing, listing_id).image_url = str(img_path)
    session.commit()
    resp = client.get(f"/shop-listings/{listing_id}/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_the_thumb_route_404s_when_the_local_file_is_missing(client, session):
    listing_id = make_listing(session, "Charizard")
    session.get(ShopListing, listing_id).image_url = "scan_ingest_data/cache/nope/1.png"
    session.commit()
    resp = client.get(f"/shop-listings/{listing_id}/thumb")
    assert resp.status_code == 404


def test_the_thumb_route_404s_when_there_is_no_image_at_all(client, session):
    listing_id = make_listing(session, "Charizard")
    resp = client.get(f"/shop-listings/{listing_id}/thumb")
    assert resp.status_code == 404


def make_listing_with_inventory(session, name, *, scan_image_path=None, image_url=None, **kwargs):
    """A listing actually linked to a stock row -- for the real-photo vs
    reference-art distinction, which only exists once inventory_item_id
    is set (see _resolve_image)."""
    listing_id = make_listing(session, name, **kwargs)
    listing = session.get(ShopListing, listing_id)
    listing.image_url = image_url
    item = InventoryItem(catalog_item_id=listing.catalog_item_id, status=InventoryStatus.AVAILABLE,
                         scan_image_path=scan_image_path)
    session.add(item)
    session.commit()
    listing.inventory_item_id = item.id
    session.commit()
    return listing_id


def test_a_listing_with_a_real_scan_is_labelled_your_photo(client, session, tmp_path):
    from PIL import Image

    img_path = tmp_path / "scan.jpg"
    Image.new("RGB", (10, 10)).save(img_path)
    listing_id = make_listing_with_inventory(session, "Charizard", scan_image_path=str(img_path),
                                             image_url="https://example.com/reference.jpg")
    body = client.get("/shop-listings").text
    assert "Your photo" in body
    assert "Reference art" not in body


def test_a_listing_with_only_a_reference_image_is_labelled_as_such(client, session):
    listing_id = make_listing_with_inventory(session, "Charizard", scan_image_path=None,
                                             image_url="https://example.com/reference.jpg")
    body = client.get("/shop-listings").text
    assert "Reference art" in body
    assert "Your photo" not in body


def test_the_real_scan_wins_over_the_reference_image_at_the_image_routes(client, session, tmp_path):
    from PIL import Image

    img_path = tmp_path / "scan.jpg"
    Image.new("RGB", (10, 10)).save(img_path)
    listing_id = make_listing_with_inventory(session, "Charizard", scan_image_path=str(img_path),
                                             image_url="https://example.com/reference.jpg")
    resp = client.get(f"/shop-listings/{listing_id}/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_a_withdrawn_listing_shows_why_it_was_withheld(client, session):
    from automation_control.audit import record_event

    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.WITHDRAWN)
    record_event(session, actor_type="user", actor_id="nic", action="listing.withhold_for_review",
                resource_type="shop_listing", resource_id=listing_id, outcome="success",
                correlation_id="c1", details={"reason": "Withheld pending a photo check."})
    session.commit()
    body = client.get("/shop-listings?status=withdrawn").text
    assert "Withheld pending a photo check." in body


def test_a_withdrawn_listing_with_no_recorded_reason_shows_none(client, session):
    """A listing withdrawn by hand via the Withdraw button has no
    listing.withhold_for_review audit row -- the reason area just
    shouldn't render, not show a blank or placeholder line."""
    make_listing(session, "Charizard", status=ShopListingStatus.WITHDRAWN)
    body = client.get("/shop-listings?status=withdrawn").text
    assert "class='listing-reason'" not in body


def test_withdraw_asks_for_confirmation(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=active").text
    assert "onsubmit=\"return confirm('Withdraw this listing?');\"" in body


def test_a_draft_has_an_activate_button(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings").text
    assert f"/shop-listings/{listing_id}/activate" in body


def test_the_bulk_activate_button_names_the_real_count(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft").text
    assert "Activate all 2 draft(s)" in body


def test_no_bulk_button_when_there_are_no_drafts(client, session):
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=draft").text
    assert "activate-all" not in body


# --- actions -------------------------------------------------------------

def test_activating_a_draft_makes_it_active(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    response = client.post(f"/shop-listings/{listing_id}/activate", follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    assert session.get(ShopListing, listing_id).status == ShopListingStatus.ACTIVE


def test_withdrawing_a_draft_marks_it_withdrawn(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    client.post(f"/shop-listings/{listing_id}/withdraw")
    session.expire_all()
    assert session.get(ShopListing, listing_id).status == ShopListingStatus.WITHDRAWN


def test_withdrawing_an_active_listing_also_works(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    client.post(f"/shop-listings/{listing_id}/withdraw")
    session.expire_all()
    assert session.get(ShopListing, listing_id).status == ShopListingStatus.WITHDRAWN


def test_activating_an_already_active_listing_is_a_harmless_no_op(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    response = client.post(f"/shop-listings/{listing_id}/activate", follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    assert session.get(ShopListing, listing_id).status == ShopListingStatus.ACTIVE


def test_activate_missing_listing_is_a_404(client, session):
    assert client.post("/shop-listings/does-not-exist/activate").status_code == 404


def test_bulk_activate_flips_every_matching_draft(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.DRAFT)
    make_listing(session, "Squirtle", status=ShopListingStatus.ACTIVE)
    response = client.post("/shop-listings/activate-all?status=draft", follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    statuses = {row.title: row.status for row in session.query(ShopListing).all()}
    assert statuses["Charizard #1"] == ShopListingStatus.ACTIVE
    assert statuses["Pikachu #1"] == ShopListingStatus.ACTIVE
    assert statuses["Squirtle #1"] == ShopListingStatus.ACTIVE  # was already active


def test_bulk_activate_respects_the_search_query(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.DRAFT)
    client.post("/shop-listings/activate-all?status=draft&q=char")
    session.expire_all()
    statuses = {row.title: row.status for row in session.query(ShopListing).all()}
    assert statuses["Charizard #1"] == ShopListingStatus.ACTIVE
    assert statuses["Pikachu #1"] == ShopListingStatus.DRAFT


def test_nav_links_to_the_new_page(client, session):
    body = client.get("/dashboard").text
    assert "href='/shop-listings'" in body


# --- editing a listing ------------------------------------------------
# The real bug this closes: a listing created while a collectible was
# (wrongly) flagged secret sat live at ~9.6x its correct price for a week
# after the flag was fixed, because nothing else could re-touch an
# already-created listing's title or price.

def test_the_edit_form_shows_current_values(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.ACTIVE, price="173.37")
    page = client.get(f"/shop-listings/{listing_id}/edit").text
    assert "value='Kappa #1'" in page
    assert "value='173.37'" in page


def test_the_edit_form_shows_the_photo_when_there_is_one(client, session):
    listing_id = make_listing(session, "Kappa")
    session.get(ShopListing, listing_id).image_url = "https://example.com/kappa.jpg"
    session.commit()
    page = client.get(f"/shop-listings/{listing_id}/edit").text
    assert f"src='/shop-listings/{listing_id}/thumb'" in page
    assert "edit-photo-empty" not in page
    assert "not a photo of your card" in page


def test_the_edit_form_shows_a_placeholder_when_there_is_no_photo(client, session):
    listing_id = make_listing(session, "Kappa")
    page = client.get(f"/shop-listings/{listing_id}/edit").text
    assert "edit-photo-empty" in page


def test_the_edit_form_shows_the_current_description(client, session):
    listing_id = make_listing(session, "Kappa")
    session.get(ShopListing, listing_id).description = "A lovely reverse holo."
    session.commit()
    page = client.get(f"/shop-listings/{listing_id}/edit").text
    assert "A lovely reverse holo." in page


def test_saving_updates_the_description(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.ACTIVE, price="173.37")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "18.04", "quantity": "1", "condition": "",
                             "description": "Corrected after a physical check."},
                       follow_redirects=False)
    assert resp.status_code == 303
    session.expire_all()
    listing = session.get(ShopListing, listing_id)
    assert listing.description == "Corrected after a physical check."


def test_saving_corrects_title_and_price(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.ACTIVE, price="173.37")

    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "18.04", "quantity": "1", "condition": "New"},
                       follow_redirects=False)

    assert resp.status_code == 303
    session.expire_all()
    listing = session.get(ShopListing, listing_id)
    assert listing.title == "Kappa"
    assert listing.price == Decimal("18.04")


def test_editing_redirects_back_to_the_listings_status_filter(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.WITHDRAWN, price="10.00")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "10.00", "quantity": "1", "condition": ""},
                       follow_redirects=False)
    assert resp.headers["location"] == "/shop-listings?status=withdrawn"


def test_an_empty_title_is_rejected(client, session):
    listing_id = make_listing(session, "Kappa", price="10.00")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "   ", "price": "10.00", "quantity": "1", "condition": ""},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    session.expire_all()
    assert session.get(ShopListing, listing_id).title != ""


def test_a_zero_or_negative_price_is_rejected(client, session):
    listing_id = make_listing(session, "Kappa", price="10.00")
    for bad_price in ("0", "-5"):
        resp = client.post(f"/shop-listings/{listing_id}/edit",
                           data={"title": "Kappa", "price": bad_price, "quantity": "1", "condition": ""},
                           follow_redirects=False)
        assert "error=" in resp.headers["location"]
    session.expire_all()
    assert session.get(ShopListing, listing_id).price == Decimal("10.00")


def test_a_non_numeric_price_is_rejected_cleanly(client, session):
    listing_id = make_listing(session, "Kappa", price="10.00")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "not-a-number", "quantity": "1", "condition": ""},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_a_negative_quantity_is_rejected(client, session):
    listing_id = make_listing(session, "Kappa", price="10.00")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "10.00", "quantity": "-1", "condition": ""},
                       follow_redirects=False)
    assert "error=" in resp.headers["location"]


def test_editing_works_regardless_of_status(client, session):
    """Correcting a wrong title or price isn't gated on the listing still
    being a draft or active -- a withdrawn or sold-out row's data can be
    just as wrong."""
    for status in (ShopListingStatus.WITHDRAWN, ShopListingStatus.SOLD_OUT, ShopListingStatus.DRAFT):
        listing_id = make_listing(session, f"Item-{status.value}", status=status, price="5.00")
        resp = client.post(f"/shop-listings/{listing_id}/edit",
                           data={"title": "Corrected", "price": "9.99", "quantity": "1", "condition": ""})
        session.expire_all()
        assert session.get(ShopListing, listing_id).title == "Corrected"


def test_editing_an_unknown_listing_404s(client, session):
    resp = client.post("/shop-listings/does-not-exist/edit",
                       data={"title": "X", "price": "1.00", "quantity": "1", "condition": ""})
    assert resp.status_code == 404


def test_an_edit_records_before_and_after_values(client, session):
    from automation_control.models import AuditEvent

    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.ACTIVE, price="173.37")
    client.post(f"/shop-listings/{listing_id}/edit",
               data={"title": "Kappa", "price": "18.04", "quantity": "1", "condition": ""})

    event = session.query(AuditEvent).filter_by(action="shop_listing.edit").one()
    assert event.details["before"]["price"] == "173.37"
    assert event.details["after"]["price"] == "18.04"


def test_the_row_links_to_edit_regardless_of_status(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.WITHDRAWN, price="10.00")
    page = client.get("/shop-listings?status=withdrawn").text
    assert f"href='/shop-listings/{listing_id}/edit'" in page
