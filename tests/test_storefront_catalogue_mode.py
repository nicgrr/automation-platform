"""Display-only mode: no cart or checkout, outbound buttons to the marketplaces."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import (
    CardVariant, CatalogItem, CatalogItemType, EbayListing, EbayListingLink, EbayListingLinkStatus,
    InventoryItem, ShopListing, ShopListingStatus, SocialLink, TcgCard,
)
from storefront import catalogue
from storefront.app import create_app

EBAY_ITEM = "https://www.ebay.com.au/itm/Pikachu-Card-/267800000001"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def catalogue_mode(monkeypatch):
    class _S:
        storefront_mode = "catalogue"
    monkeypatch.setattr(catalogue, "get_settings", lambda: _S())


def _listing(session, listing_id="L1", linked=True, ebay_status="Active"):
    session.add(CatalogItem(id=f"c-{listing_id}", item_type=CatalogItemType.TCG_CARD, name="Pikachu"))
    session.add(TcgCard(catalog_item_id=f"c-{listing_id}", game="pokemon", number="1"))
    session.add(InventoryItem(id=f"i-{listing_id}", card_id=None, catalog_item_id=f"c-{listing_id}",
                               variant=CardVariant.NORMAL, quantity=1))
    session.add(ShopListing(id=listing_id, catalog_item_id=f"c-{listing_id}", title="Pikachu", price="5.00",
                             status=ShopListingStatus.ACTIVE, quantity=1))
    if linked:
        session.add(EbayListing(ebay_listing_id="267800000001", title="Pikachu", currency="AUD",
                                current_price_minor=500, listing_status=ebay_status,
                                raw_snapshot={"view_item_url": EBAY_ITEM}))
        session.add(EbayListingLink(shop_listing_id=listing_id, sku="SKU1", marketplace_id="EBAY_AU",
                                    ebay_listing_id="267800000001", status=EbayListingLinkStatus.ACTIVE))
    session.add_all([
        SocialLink(platform="ebay", label="eBay", url="https://www.ebay.com.au/str/nlvault", is_active=True, sort_order=0),
        SocialLink(platform="vinted", label="Vinted", url="https://www.vinted.com.au/member/1", is_active=True, sort_order=1),
    ])
    session.commit()


def test_shop_mode_is_still_the_default(client, session):
    _listing(session)
    body = client.get("/item/L1").text
    assert "Add to cart" in body and "href='/cart'" in body


def test_catalogue_item_links_to_its_exact_ebay_listing(client, session, catalogue_mode):
    _listing(session)
    body = client.get("/item/L1").text
    assert "Add to cart" not in body
    assert f"href='{EBAY_ITEM}'" in body and "View on eBay" in body
    assert "vinted.com.au/member/1" in body  # shop-wide link beside it


def test_catalogue_item_without_an_ebay_listing_falls_back_to_the_channels(client, session, catalogue_mode):
    _listing(session, linked=False)
    body = client.get("/item/L1").text
    assert "View on eBay" not in body
    assert "Find us on eBay" in body and "Find us on Vinted" in body


def test_an_ended_ebay_listing_is_not_linked(client, session, catalogue_mode):
    _listing(session, ebay_status="Ended")
    assert EBAY_ITEM not in client.get("/item/L1").text


def test_non_ebay_urls_are_never_used_as_the_item_link(client, session, catalogue_mode):
    _listing(session)
    listing = session.query(EbayListing).one()
    listing.raw_snapshot = {"view_item_url": "https://evil.example.com/phish"}
    session.commit()
    assert "evil.example.com" not in client.get("/item/L1").text


def test_catalogue_hides_the_cart_and_redirects_till_urls(client, session, catalogue_mode):
    _listing(session)
    assert "href='/cart'" not in client.get("/").text
    for path in ("/cart", "/checkout", "/checkout/complete"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/"
    resp = client.post("/cart/add", data={"listing_id": "L1"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/"


def test_stripe_webhook_still_reachable_in_catalogue_mode(client, session, catalogue_mode):
    """A payment already in flight must still be able to land."""
    resp = client.post("/webhooks/stripe", content=b"{}", follow_redirects=False)
    assert resp.status_code != 303


def test_catalogue_shipping_page_explains_buying_elsewhere(client, session, catalogue_mode):
    _listing(session)
    body = client.get("/shipping").text
    assert "How buying works" in body and "ebay.com.au/str/nlvault" in body


def _export(session, listing_id, ebay_id):
    from automation_control.models import EbayExportBatch, EbayExportItem, EbayExportItemStatus

    batch = session.query(EbayExportBatch).first()
    if batch is None:
        batch = EbayExportBatch(id="b1", filename="x.csv", row_count=1, selected_count=1, created_by="t")
        session.add(batch)
        session.flush()
    session.add(EbayExportItem(batch_id=batch.id, sku=listing_id, kind="single",
                               status=EbayExportItemStatus.CONFIRMED, ebay_listing_id=ebay_id))
    session.commit()


def test_older_listings_link_through_their_confirmed_export(client, session, catalogue_mode):
    _listing(session, linked=False)
    session.add(EbayListing(ebay_listing_id="111", title="Different title", currency="AUD", current_price_minor=500,
                            listing_status="Active", raw_snapshot={"view_item_url": EBAY_ITEM}))
    session.commit()
    _export(session, "L1", "111")
    assert f"href='{EBAY_ITEM}'" in client.get("/item/L1").text


def test_a_relisted_item_links_by_identical_title_when_unambiguous(client, session, catalogue_mode):
    _listing(session, linked=False)
    session.add(EbayListing(ebay_listing_id="222", title="pikachu", currency="AUD", current_price_minor=500,
                            listing_status="Active", raw_snapshot={"view_item_url": EBAY_ITEM}))
    session.commit()
    assert f"href='{EBAY_ITEM}'" in client.get("/item/L1").text


def test_ambiguous_or_different_titles_never_link(client, session, catalogue_mode):
    _listing(session, linked=False)
    for i, title in enumerate(("Pikachu", "Pikachu", "Pikachu (Alternate Art)")):
        session.add(EbayListing(ebay_listing_id=f"3{i}", title=title, currency="AUD", current_price_minor=500,
                                listing_status="Active", raw_snapshot={"view_item_url": EBAY_ITEM}))
    session.commit()
    assert "View on eBay" not in client.get("/item/L1").text  # two identical -> ambiguous; alt art is a different title


def test_catalogue_privacy_page_describes_the_contact_form_not_a_checkout(client, catalogue_mode):
    body = client.get("/privacy").text
    assert "Purchases are made on eBay or Vinted" in body
    assert "Stripe" not in body


def test_catalogue_terms_say_where_sales_happen(client, catalogue_mode):
    body = client.get("/terms").text
    assert "sold through our eBay and Vinted shops" in body


def test_shop_mode_privacy_page_still_covers_checkout(client):
    assert "Stripe" in client.get("/privacy").text
