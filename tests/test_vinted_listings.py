from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CatalogItem,
    CatalogItemType,
    CollectibleProduct,
    InventoryItem,
    InventoryStatus,
    ListingPhotoBatch,
    ListingPhotoPair,
    Marketplace,
    PlatformConfig,
    Sale,
    SaleItem,
    VintedListing,
    VintedListingStatus,
)
from automation_control.vinted_listings import mark_removed, parse_vinted_url, record_vinted_sale

URL = "https://www.vinted.com.au/items/4567891234-sonny-angel-cat-life?referrer=catalog"


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/vinted.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Marketplace(id="m-vinted", name="Vinted"))
        s.add(CatalogItem(id="sonny", name="Sonny Angel Cat Life Persian", item_type=CatalogItemType.COLLECTIBLE))
        s.flush()
        s.add(CollectibleProduct(catalog_item_id="sonny", brand="Sonny Angel", series="Cat Life"))
        s.add(InventoryItem(id="inv", catalog_item_id="sonny", condition="Used", quantity=1,
                            status=InventoryStatus.AVAILABLE, scan_image_path="/photo.jpg"))
        s.add(PlatformConfig(key="vinted_draft:i-inv", value={"title": "Sonny Angel Cat Life Persian",
                             "description": "Opened figure.", "confirmed_price": "14.99"}))
        s.commit()
        app.dependency_overrides[get_session] = lambda: s
        app.dependency_overrides[require_dashboard_user] = lambda: "test"
        # No `with`: entering TestClient runs the lifespan's production schema check.
        yield s, TestClient(app)
        app.dependency_overrides.clear()


@pytest.mark.parametrize("raw, expected", [
    (URL, ("4567891234", "https://www.vinted.com.au/items/4567891234")),
    ("Check this out https://vinted.co.uk/items/12345678-x", ("12345678", "https://vinted.co.uk/items/12345678")),
    ("https://www.vinted.com.au/items/4567891234", ("4567891234", "https://www.vinted.com.au/items/4567891234")),
])
def test_vinted_item_links_are_recognised(raw, expected):
    assert parse_vinted_url(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "not a link", "https://www.vinted.com.au/member/123", "https://evil.example/items/12345678",
    "https://vinted.com.au.evil.example/items/12345678",
    "https://vinted.evil.com/items/12345678", "https://vinted.xyz/items/12345678",
    "https://evil.vinted.com.au/items/12345678", "https://user:password@vinted.com.au/items/12345678",
    "https://vinted.com.au:broken/items/12345678", "https://[broken/items/12345678",
])
def test_anything_else_is_refused(raw):
    assert parse_vinted_url(raw) is None


def _post(client, url=URL):
    return client.post("/collectibles/vinted/i-inv/posted", data={"url": url, "price": "14.99"},
                       follow_redirects=False)


def test_the_post_screen_has_the_draft_ready_to_copy(context):
    _, client = context
    body = client.get("/collectibles/vinted/i-inv/post").text
    assert "Sonny Angel Cat Life Persian" in body and "14.99" in body
    assert "data-copy='v-title'" in body and "Save all photos" in body


def test_marking_it_live_reserves_the_stock(context):
    s, client = context
    resp = _post(client)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/collectibles/vinted/live")
    s.expire_all()
    listing = s.scalars(select(VintedListing)).one()
    assert (listing.vinted_item_id, listing.price, listing.status) == ("4567891234", Decimal("14.99"), VintedListingStatus.ACTIVE)
    inventory = s.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (0, InventoryStatus.RESERVED)


def test_it_cannot_be_posted_twice_without_more_stock(context):
    s, client = context
    _post(client)
    resp = _post(client, "https://www.vinted.com.au/items/9999999999-other")
    assert resp.headers["location"].startswith("/collectibles/vinted/live")
    assert len(s.scalars(select(VintedListing)).all()) == 1


def test_the_same_vinted_item_cannot_be_recorded_twice(context):
    s, client = context
    s.get(InventoryItem, "inv").quantity = 2
    s.commit()
    _post(client)
    resp = _post(client)
    assert "error=" in resp.headers["location"]
    s.expire_all()
    assert s.get(InventoryItem, "inv").quantity == 1  # the second reservation was rolled back


def test_a_bad_link_is_explained_not_saved(context):
    s, client = context
    resp = _post(client, "https://www.vinted.com.au/member/123")
    assert "error=" in resp.headers["location"]
    assert s.scalars(select(VintedListing)).all() == []


def test_selling_it_records_a_sale_and_uses_up_the_stock(context):
    s, client = context
    _post(client)
    listing = s.scalars(select(VintedListing)).one()
    client.post(f"/collectibles/vinted/listing/{listing.id}/sold", data={"price": "14.99", "fees": "0"})
    s.expire_all()
    assert s.get(VintedListing, listing.id).status == VintedListingStatus.SOLD
    assert s.get(InventoryItem, "inv").status == InventoryStatus.SOLD
    sale = s.scalars(select(Sale)).one()
    assert (sale.source, sale.marketplace_id, sale.gross_amount) == ("vinted", "m-vinted", Decimal("14.99"))
    assert s.scalars(select(SaleItem)).one().inventory_item_id == "inv"
    # a second tap doesn't sell it twice
    client.post(f"/collectibles/vinted/listing/{listing.id}/sold", data={"price": "14.99", "fees": "0"})
    assert len(s.scalars(select(Sale)).all()) == 1


def test_removing_it_puts_the_stock_back(context):
    s, client = context
    _post(client)
    listing = s.scalars(select(VintedListing)).one()
    client.post(f"/collectibles/vinted/listing/{listing.id}/remove")
    s.expire_all()
    assert s.get(VintedListing, listing.id).status == VintedListingStatus.REMOVED
    inventory = s.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (1, InventoryStatus.AVAILABLE)


def test_the_live_page_lists_what_is_on_vinted(context):
    _, client = context
    _post(client)
    body = client.get("/collectibles/vinted/live").text
    assert "https://www.vinted.com.au/items/4567891234" in body and "Sold on Vinted" in body


def test_the_photos_download_for_desktop_upload(context, tmp_path):
    s, client = context
    photo = tmp_path / "front.jpg"
    photo.write_bytes(b"jpeg-bytes")
    s.get(InventoryItem, "inv").scan_image_path = str(photo)
    s.commit()
    resp = client.get("/collectibles/vinted/i-inv/photos.zip")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert names == ["1-Sonny-Angel-Cat-Life-Persian.jpg"]


@pytest.mark.parametrize("price", ["-10", "0", "NaN", "Infinity", "-Infinity", "1.001", "100000000"])
def test_invalid_posted_price_does_not_reserve_stock(context, price):
    s, client = context
    response = client.post("/collectibles/vinted/i-inv/posted", data={"url": URL, "price": price},
                           follow_redirects=False)
    assert "error=" in response.headers["location"]
    assert s.scalars(select(VintedListing)).all() == []
    inventory = s.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (1, InventoryStatus.AVAILABLE)


@pytest.mark.parametrize("price,fees", [("0", "0"), ("NaN", "0"), ("Infinity", "0"),
                                       ("14.99", "-1"), ("14.99", "NaN"), ("14.99", "Infinity")])
def test_invalid_sale_amounts_do_not_consume_reserved_stock(context, price, fees):
    s, client = context
    _post(client)
    listing = s.scalars(select(VintedListing)).one()
    response = client.post(f"/collectibles/vinted/listing/{listing.id}/sold",
                           data={"price": price, "fees": fees}, follow_redirects=False)
    assert response.status_code == 400
    s.expire_all()
    assert s.get(VintedListing, listing.id).status == VintedListingStatus.ACTIVE
    assert s.get(InventoryItem, "inv").status == InventoryStatus.RESERVED
    assert s.scalars(select(Sale)).all() == []


def test_sale_helper_validates_before_claiming_the_listing(context):
    s, client = context
    _post(client)
    listing = s.scalars(select(VintedListing)).one()
    with pytest.raises(HTTPException) as exc:
        record_vinted_sale(s, listing, Decimal("NaN"), Decimal("0"), actor="test")
    assert exc.value.status_code == 400
    assert listing.status == VintedListingStatus.ACTIVE
    assert s.scalars(select(Sale)).all() == []


def test_selling_one_of_two_reserved_units_keeps_the_other_available_to_remove(context):
    s, client = context
    s.get(InventoryItem, "inv").quantity = 2
    s.commit()
    _post(client)
    _post(client, "https://www.vinted.com.au/items/4567891235-another")
    first, second = s.scalars(select(VintedListing).order_by(VintedListing.vinted_item_id)).all()
    client.post(f"/collectibles/vinted/listing/{first.id}/sold", data={"price": "14.99", "fees": "0"})
    s.expire_all()
    assert s.get(InventoryItem, "inv").status == InventoryStatus.RESERVED
    assert s.get(VintedListing, second.id).status == VintedListingStatus.ACTIVE
    client.post(f"/collectibles/vinted/listing/{second.id}/remove")
    s.expire_all()
    inventory = s.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (1, InventoryStatus.AVAILABLE)
    assert s.get(VintedListing, first.id).status == VintedListingStatus.SOLD
    assert len(s.scalars(select(Sale)).all()) == 1


def test_stale_removal_cannot_resurrect_a_sold_unit(context):
    s, client = context
    _post(client)
    stale_listing = s.scalars(select(VintedListing)).one()
    stale_inventory = s.get(InventoryItem, "inv")
    with Session(s.get_bind()) as other:
        record_vinted_sale(other, other.get(VintedListing, stale_listing.id), Decimal("14.99"),
                           Decimal("0"), actor="test")
    # These objects deliberately represent a Remove request begun before the sale finished.
    assert stale_listing.status == VintedListingStatus.ACTIVE
    assert stale_inventory.status == InventoryStatus.RESERVED
    mark_removed(stale_listing.id, user="test", session=s)
    s.expire_all()
    assert s.get(VintedListing, stale_listing.id).status == VintedListingStatus.SOLD
    inventory = s.get(InventoryItem, "inv")
    assert (inventory.quantity, inventory.status) == (0, InventoryStatus.SOLD)
    assert len(s.scalars(select(Sale)).all()) == 1


def test_repeated_removal_releases_only_one_unit(context):
    s, client = context
    _post(client)
    listing = s.scalars(select(VintedListing)).one()
    client.post(f"/collectibles/vinted/listing/{listing.id}/remove")
    client.post(f"/collectibles/vinted/listing/{listing.id}/remove")
    s.expire_all()
    assert s.get(InventoryItem, "inv").quantity == 1


def test_review_holds_remain_visible_and_recording_a_real_listing_does_not_clear_them(context):
    s, client = context
    s.add(ListingPhotoBatch(id="batch"))
    s.add(ListingPhotoPair(id="pair", batch_id="batch", position=1, front_image_path="/photo.jpg",
                           proposed_catalog_item_id="sonny",
                           extracted={"item_kind": "collectible", "identity_confidence": "low"}))
    draft = s.get(PlatformConfig, "vinted_draft:i-inv")
    draft.value = {**draft.value, "hold_reason": "Check the figure's edition."}
    s.commit()
    body = client.get("/collectibles/vinted/i-inv/post").text
    assert "Check the figure&#x27;s edition." in body
    assert "Confirm the exact identity / edition." in body
    response = _post(client)
    assert response.headers["location"].startswith("/collectibles/vinted/live")
    s.expire_all()
    assert s.get(PlatformConfig, "vinted_draft:i-inv").value["hold_reason"] == "Check the figure's edition."
    assert s.get(ListingPhotoPair, "pair").extracted["identity_confidence"] == "low"
