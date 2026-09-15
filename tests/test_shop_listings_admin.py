from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus, TcgCard,
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
