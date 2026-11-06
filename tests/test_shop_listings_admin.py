from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet, CatalogItem, CatalogItemType, EbayExportBatch, EbayExportItem, EbayExportItemStatus,
    EbayListing, EbayListingLink, EbayListingLinkStatus, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus, TcgCard,
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
                 set_id="sv1", set_name="Scarlet & Violet", game="pokemon"):
    session.merge(CardSet(id=set_id, name=set_name))
    item_id = f"i-{name}"
    listing_id = f"l-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game=game, set_id=set_id, number="1"))
    session.add(ShopListing(id=listing_id, catalog_item_id=item_id, title=f"{name} #1",
                            price=Decimal(price), currency="AUD", quantity=1, status=status))
    session.commit()
    return listing_id


# --- the page itself ---------------------------------------------------

def test_shop_listings_page_defaults_to_all_local_statuses(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings").text
    assert "Charizard" in body
    assert "Pikachu" in body


def test_shop_listings_page_defaults_to_not_listed_on_ebay(client, session):
    unlisted_id = make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    live_id = make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    session.add(EbayListingLink(
        shop_listing_id=live_id, sku=live_id, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="123456789",
    ))
    session.add(EbayListing(ebay_listing_id="123456789", title="Live card", currency="AUD",
        current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    body = client.get("/shop-listings").text
    assert "Charizard" in body
    assert "Pikachu" not in body


def test_shop_listings_uses_explicit_filters_without_hidden_redirects(client, session):
    make_listing(session, "Charizard")
    body = client.get("/shop-listings?status=draft&ebay=not_listed&category=pokemon").text
    assert "ezbay.shop-listings.filters" not in body
    assert "window.location.replace" not in body


def test_a_draft_is_actually_visible_here(client, session):
    """The bug this page exists to fix: shop-pricing only ever showed
    ACTIVE, so a DRAFT listing was invisible everywhere in the admin app."""
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft").text
    assert "Charizard" in body
    assert "DRAFT" in body


def test_listing_handoff_offers_direct_publish_and_bulk_upload(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft").text
    assert "Review & Publish" in body
    assert "/shop-listings/l-Charizard/ebay-preview" in body
    assert "Bulk Upload" in body
    assert "/ebay-export" in body


def test_status_filter_tabs_switch_what_shows(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=active").text
    assert "Pikachu" in body
    assert "Charizard" not in body


def test_ebay_filter_separates_live_direct_listings_from_unlisted(client, session):
    live_id = make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    session.add(EbayListingLink(
        shop_listing_id=live_id, sku=live_id, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="123456789",
    ))
    session.add(EbayListing(ebay_listing_id="123456789", title="Live card", currency="AUD",
        current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    live_page = client.get("/shop-listings?status=active&ebay=live").text
    assert "Charizard" in live_page
    assert "Pikachu" not in live_page
    assert "LIVE ON EBAY" in live_page

    unlisted_page = client.get("/shop-listings?status=active&ebay=not_listed").text
    assert "Pikachu" in unlisted_page
    assert "Charizard" not in unlisted_page


def test_bulk_confirmed_listing_is_live_and_has_no_publish_action(client, session):
    listing_id = make_listing(session, "Nami", status=ShopListingStatus.ACTIVE, game="one_piece")
    batch = EbayExportBatch(filename="upload.csv", row_count=1, selected_count=1, created_by="testuser")
    session.add(batch)
    session.flush()
    session.add(EbayExportItem(
        batch_id=batch.id, sku=listing_id, title="Nami #1", kind="single",
        status=EbayExportItemStatus.CONFIRMED, ebay_listing_id="123456789",
    ))
    session.add(EbayListing(ebay_listing_id="123456789", title="Live card", currency="AUD",
        current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()

    body = client.get("/shop-listings?status=active&ebay=live").text
    assert "Nami" in body
    assert "LIVE ON EBAY" in body
    # A live listing still needs a local-only improvement workspace; the
    # studio does not write revisions without a separate approval.
    assert f"/shop-listings/{listing_id}/ebay-preview" in body
    assert "Improve listing" in body


def test_category_filter_separates_card_games(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE, game="pokemon")
    make_listing(session, "Nami", status=ShopListingStatus.ACTIVE, game="one_piece", set_id="op01", set_name="Romance Dawn")

    body = client.get("/shop-listings?status=active&category=one_piece").text
    assert "Nami" in body
    assert "Charizard" not in body


def test_dragon_ball_category_includes_dbz_era_card_systems(client, session):
    make_listing(session, "Goku Artbox", status=ShopListingStatus.ACTIVE, game="dragon_ball_artbox")
    make_listing(
        session, "Gohan Carddass", status=ShopListingStatus.ACTIVE,
        game="dragon_ball_carddass", set_id="db-carddass", set_name="Dragon Ball Carddass",
    )

    body = client.get("/shop-listings?status=active&category=dragon_ball").text
    assert "Goku Artbox" in body
    assert "Gohan Carddass" in body


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


def test_mobile_listing_layout_uses_cards_and_full_width_actions(client, session):
    """The desktop table must not be squeezed into a phone viewport: its
    rows become cards and the action cell spans the whole card."""
    make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=active").text
    assert "panel listing-results" in body
    assert ".listing-table tr {" in body
    assert "grid-template-columns: 66px minmax(0, 1fr)" in body
    assert ".listing-actions {" in body
    assert "grid-column: 1 / -1" in body
    assert "min-height: 44px" in body


def test_mobile_filters_are_compact_and_do_not_use_horizontal_status_strips(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=all&ebay=not_listed").text
    assert "listing-desktop-filters" in body
    assert "listing-mobile-filters" in body
    assert "mobile-filter-summary" in body
    assert "All · Not listed / ended · All categories" in body
    assert "<select name='status'>" in body
    assert "<select name='ebay'>" in body
    assert "<select name='category'>" in body
    assert ".listing-desktop-filters { display: none; }" in body


def test_a_listing_with_no_image_gets_an_empty_thumbnail_placeholder(client, session):
    make_listing(session, "Charizard")
    body = client.get("/shop-listings").text
    assert "listing-thumb-empty" in body


def test_a_listing_with_an_image_shows_it_as_a_thumbnail(client, session):
    listing_id = make_listing(session, "Charizard")
    session.get(ShopListing, listing_id).image_url = "https://example.com/charizard.jpg"
    session.commit()
    body = client.get("/shop-listings").text
    assert f"src='/shop-listings/{listing_id}/thumb?v=" in body
    assert f"href='/shop-listings/{listing_id}/image?v=" in body


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
    assert "onsubmit=\"return confirm('Hide this record in EZBay? This does not end an eBay listing.');\"" in body


def test_a_draft_has_an_activate_button(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings").text
    assert f"/shop-listings/{listing_id}/activate" in body


def test_the_bulk_activate_button_names_the_real_count(client, session):
    make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    make_listing(session, "Pikachu", status=ShopListingStatus.DRAFT)
    body = client.get("/shop-listings?status=draft").text
    assert "Enable 2 draft(s) in EZBay" in body


def test_no_bulk_button_when_there_are_no_drafts(client, session):
    make_listing(session, "Pikachu", status=ShopListingStatus.ACTIVE)
    body = client.get("/shop-listings?status=draft").text
    assert "activate-all" not in body


# --- actions -------------------------------------------------------------

def test_activating_a_draft_makes_it_active(client, session):
    listing_id = make_listing(session, "Charizard", status=ShopListingStatus.DRAFT)
    response = client.post(f"/shop-listings/{listing_id}/activate", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/shop-listings"
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
    assert f"src='/shop-listings/{listing_id}/thumb?v=" in page
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


def test_editing_redirects_back_to_all_local_statuses(client, session):
    listing_id = make_listing(session, "Kappa", status=ShopListingStatus.WITHDRAWN, price="10.00")
    resp = client.post(f"/shop-listings/{listing_id}/edit",
                       data={"title": "Kappa", "price": "10.00", "quantity": "1", "condition": ""},
                       follow_redirects=False)
    assert resp.headers["location"] == "/shop-listings"


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


def test_ended_remote_overrides_stale_active_link(client, session):
    lid = make_listing(session, "Ended card", status=ShopListingStatus.ACTIVE)
    session.add(EbayListingLink(shop_listing_id=lid, sku=lid, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="ended-1"))
    session.add(EbayListing(ebay_listing_id="ended-1", title="Ended card", currency="AUD",
        current_price_minor=1000, listing_status="Ended", quantity=1))
    session.commit()
    assert "Ended card #1" not in client.get("/shop-listings?ebay=live").text
    body = client.get("/shop-listings?ebay=unpublished").text
    assert "Ended card #1" in body
    assert "NOT ON EBAY" in body
    assert "EZBAY: ENABLED" in body


def test_failed_publication_is_in_outstanding_view(client, session):
    lid = make_listing(session, "Failed card", status=ShopListingStatus.ACTIVE)
    session.add(EbayListingLink(shop_listing_id=lid, sku=lid, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.FAILED))
    session.commit()
    body = client.get("/shop-listings?ebay=unpublished").text
    assert "Failed card #1" in body
    assert "NEEDS ATTENTION" in body
    assert "More options" in body


def test_publish_awaiting_refresh_cannot_be_mistaken_for_unpublished(client, session):
    lid = make_listing(session, "New publication")
    session.add(EbayListingLink(shop_listing_id=lid, sku=lid, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.ACTIVE, ebay_listing_id="awaiting-sync"))
    session.commit()
    body = client.get("/shop-listings?ebay=pending").text
    assert "New publication #1" in body
    assert "EBAY UPDATE PENDING" in body
    assert "Review & Publish" not in body


def test_possible_live_match_requires_link_review_not_republication(client, session):
    make_listing(session, "Unlinked card", status=ShopListingStatus.ACTIVE)
    session.add(EbayListing(ebay_listing_id="unlinked-1", title="Unlinked card #1", currency="AUD",
        current_price_minor=1000, listing_status="Active", quantity=1))
    session.commit()
    assert "Unlinked card #1" not in client.get("/shop-listings?ebay=unpublished").text
    body = client.get("/shop-listings?ebay=attention").text
    assert "Unlinked card #1" in body
    assert "Check the link before publishing another copy" in body


def test_gallery_photo_overrides_old_inventory_scan_and_versions_url(client, session):
    lid = make_listing_with_inventory(session, "Updated", scan_image_path="old-scan.jpg")
    listing = session.get(ShopListing, lid)
    listing.ebay_photo_paths = ["https://example.com/new-front.jpg", "https://example.com/back.jpg"]
    session.commit()
    from automation_control.shop_listings_admin import _photo_version
    body = client.get("/shop-listings").text
    assert "Your photo" in body
    assert f"/thumb?v={_photo_version(listing.ebay_photo_paths[0])}" in body
    response = client.get(f"/shop-listings/{lid}/thumb", follow_redirects=False)
    assert response.headers["location"] == listing.ebay_photo_paths[0]
    assert client.get(f"/shop-listings/{lid}/image", follow_redirects=False).headers["location"] == listing.ebay_photo_paths[0]
    listing.ebay_photo_paths = ["https://example.com/replacement.jpg"]
    session.commit()
    assert f"/thumb?v={_photo_version(listing.ebay_photo_paths[0])}" in client.get("/shop-listings").text


def test_empty_gallery_does_not_restore_deleted_inventory_photo(client, session):
    lid = make_listing_with_inventory(session, "Removed", scan_image_path="old-scan.jpg")
    session.get(ShopListing, lid).ebay_photo_paths = []
    session.commit()
    assert client.get(f"/shop-listings/{lid}/thumb").status_code == 404
    assert "listing-thumb-empty" in client.get("/shop-listings").text


def test_out_of_stock_is_separate_from_all_and_publish_views(client, session):
    make_listing(session, "Available card", status=ShopListingStatus.ACTIVE)
    zero = make_listing(session, "Zero stock card", status=ShopListingStatus.ACTIVE)
    session.get(ShopListing, zero).quantity = 0
    make_listing(session, "Sold card", status=ShopListingStatus.SOLD_OUT)
    session.commit()
    for url in ("/shop-listings?ebay=all", "/shop-listings?ebay=unpublished"):
        body = client.get(url).text
        assert "Available card #1" in body
        assert "Zero stock card #1" not in body
        assert "Sold card #1" not in body
        assert "Out of stock (2)" in body
    body = client.get("/shop-listings?status=sold_out&ebay=all").text
    assert "Zero stock card #1" in body
    assert "Sold card #1" in body
    assert "Available card #1" not in body
    assert "Back to current listings" in body


def test_finish_selector_saves_listing_override_without_changing_rarity(client, session):
    lid = make_listing(session, 'Fossil')
    card = session.get(TcgCard, 'i-Fossil')
    card.rarity = 'Illustration Rare'
    session.commit()
    page = client.get(f'/shop-listings/{lid}/edit').text
    for label in ('Master Ball', 'Poké Ball', 'Reverse Holo', 'Cosmos Holo'):
        assert label in page
    response = client.post(f'/shop-listings/{lid}/edit',data={
        'title':'Fossil #1','price':'9.99','quantity':'1','condition':'Near Mint','finish':'masterball_pattern'},follow_redirects=False)
    assert response.status_code == 303
    listing = session.get(ShopListing,lid)
    assert listing.finish == 'masterball_pattern'
    assert card.rarity == 'Illustration Rare'
    assert 'value=\'masterball_pattern\' selected' in client.get(f'/shop-listings/{lid}/edit').text
    client.post(f'/shop-listings/{lid}/edit', data={
        'title':'Bad update','price':'9.99','quantity':'1','finish':'invented'},follow_redirects=False)
    session.refresh(listing)
    assert listing.title == 'Fossil #1'
    assert listing.finish == 'masterball_pattern'


def test_hiding_a_listing_returns_to_the_filtered_list_it_was_hidden_from(client, session):
    listing_id = make_listing(session, "Son Gohan", status=ShopListingStatus.ACTIVE)
    resp = client.post(f"/shop-listings/{listing_id}/withdraw",
                       params={"_return_to": "/shop-listings?status=active&q=gohan"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/shop-listings?status=active&q=gohan&_saved=Listing+hidden"


def test_saving_the_edit_page_returns_to_where_it_was_opened_from(client, session):
    listing_id = make_listing(session, "Gohan")
    edit_page = f"/shop-listings/{listing_id}/edit"
    resp = client.post(edit_page,
                       params={"_return_to": edit_page, "_return_prev": f"/shop-listings/{listing_id}/ebay-preview"},
                       data={"title": "Edited", "price": "5.00", "quantity": "1"}, follow_redirects=False)
    assert resp.headers["location"] == f"/shop-listings/{listing_id}/ebay-preview?_saved=Listing+saved"


def test_sonny_vinted_queue_preserves_timer_and_ebay_attention(session):
    from automation_control.models import CollectibleProduct, VintedListing, VintedListingStatus
    from automation_control.shop_listings_admin import _filtered
    listing_id = make_listing(session, "TimerSonny")
    listing = session.get(ShopListing, listing_id)
    session.add(CollectibleProduct(catalog_item_id=listing.catalog_item_id, brand="Sonny Angel"))
    stock = InventoryItem(catalog_item_id=listing.catalog_item_id, quantity=1)
    session.add(stock); session.flush()
    listing.inventory_item_id = stock.id
    vinted = VintedListing(inventory_item_id=stock.id, catalog_item_id=listing.catalog_item_id,
        vinted_item_id="timer123", url="https://www.vinted.com/items/timer123", title="TimerSonny", price=10)
    session.add(vinted); session.commit()
    posted_at = vinted.posted_at
    def visible():
        return listing_id in {r.id for r in session.scalars(_filtered("all", "", "all", "all"))}
    assert not visible()
    assert vinted.posted_at == posted_at and listing.status == ShopListingStatus.DRAFT
    vinted.status = VintedListingStatus.SOLD; session.commit()
    assert not visible()
    vinted.status = VintedListingStatus.REMOVED; session.commit()
    assert visible()
    vinted.status = VintedListingStatus.ACTIVE
    link = EbayListingLink(shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
        status=EbayListingLinkStatus.FAILED)
    session.add(link); session.commit()
    assert visible()
    link.status = EbayListingLinkStatus.ACTIVE; link.ebay_listing_id = "timer-live"
    session.commit()
    assert visible()
