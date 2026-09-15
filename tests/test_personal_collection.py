from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice, CatalogItem, CatalogItemType, InventoryItem, InventoryStatus, ShopListing, ShopListingStatus, TcgCard,
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


def make_personal_collection_item(session, name="Roronoa Zoro", *, price=None, image_url=None):
    item_id = f"i-{name}"
    inv_id = f"inv-{name}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name, image_url=image_url))
    session.add(TcgCard(catalog_item_id=item_id, game="one_piece", set_code="OP01", number="1", character=name))
    session.add(InventoryItem(id=inv_id, catalog_item_id=item_id, quantity=1, condition="Near Mint",
                              status=InventoryStatus.PERSONAL_COLLECTION))
    if price is not None:
        session.add(CardPrice(id=f"p-{name}", card_id=item_id, source="ebay_active_low", price=Decimal(price)))
    session.commit()
    return inv_id, item_id


def test_list_for_sale_buttons_share_one_form_instead_of_one_each(client, session):
    """810 near-identical single-button forms (one per row) were most of
    this page's DOM weight -- a real cost on a phone even though the raw
    HTML bytes barely change. Every row's button now references one
    shared external form via form=/formaction= rather than carrying its
    own <form>, and each button must still point at its own row's URL."""
    inv_a, _ = make_personal_collection_item(session, "Roronoa Zoro", price="10")
    inv_b, _ = make_personal_collection_item(session, "Nami", price="20")

    page = client.get("/personal-collection").text

    # Exactly one shared form, not one per row -- the old pattern being fixed.
    assert page.count("id='pc-list-form'") == 1
    assert f"<form method='post' action='/personal-collection/{inv_a}/list-for-sale'>" not in page
    assert f"<form method='post' action='/personal-collection/{inv_b}/list-for-sale'>" not in page
    assert f"form='pc-list-form' formaction='/personal-collection/{inv_a}/list-for-sale'" in page
    assert f"form='pc-list-form' formaction='/personal-collection/{inv_b}/list-for-sale'" in page


def test_page_lists_personal_collection_items(client, session):
    make_personal_collection_item(session, "Roronoa Zoro")
    resp = client.get("/personal-collection")
    assert resp.status_code == 200
    assert "Roronoa Zoro" in resp.text
    assert "1 item(s) held privately" in resp.text


def test_page_shows_no_image_placeholder_when_nothing_available(client, session):
    make_personal_collection_item(session, "Nobody")
    resp = client.get("/personal-collection")
    assert "pc-thumb-empty" in resp.text


def test_page_shows_real_image_when_catalog_item_has_one(client, session):
    make_personal_collection_item(session, "Somebody", image_url="https://example.test/real.jpg")
    resp = client.get("/personal-collection")
    assert "https://example.test/real.jpg" in resp.text


def test_list_for_sale_requires_a_price_on_file(client, session):
    inv_id, _ = make_personal_collection_item(session, "Unpriced")
    resp = client.post(f"/personal-collection/{inv_id}/list-for-sale")
    assert resp.status_code == 400


def test_list_for_sale_promotes_item_and_creates_a_draft_listing(client, session):
    inv_id, item_id = make_personal_collection_item(session, "Priced", price="10.00")
    resp = client.post(f"/personal-collection/{inv_id}/list-for-sale", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/shop-listings?status=draft"

    item = session.get(InventoryItem, inv_id)
    assert item.status == InventoryStatus.AVAILABLE
    listing = session.execute(select(ShopListing).where(ShopListing.inventory_item_id == inv_id)).scalar_one()
    assert listing.status == ShopListingStatus.DRAFT
    assert listing.catalog_item_id == item_id


def test_upload_photo_saves_file_and_overrides_catalogue_image(client, session, tmp_path, monkeypatch):
    from automation_control import personal_collection as pc

    monkeypatch.setattr(pc, "PHOTO_DIR", tmp_path / "photos")
    inv_id, _ = make_personal_collection_item(session, "Watermarked", image_url="https://example.test/sample.jpg")

    resp = client.post(
        f"/personal-collection/{inv_id}/upload-photo",
        files={"photo": ("mine.jpg", b"\xff\xd8\xff\xe0fakejpegdata", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    item = session.get(InventoryItem, inv_id)
    assert item.scan_image_path == f"/static/personal_collection_photos/{inv_id}.jpg"
    assert (tmp_path / "photos" / f"{inv_id}.jpg").exists()

    page = client.get("/personal-collection")
    # the uploaded photo wins over the catalogue's own (watermarked) image
    assert item.scan_image_path in page.text
    assert "example.test/sample.jpg" not in page.text


def test_upload_photo_404s_for_an_item_not_in_personal_collection(client, session):
    inv_id, _ = make_personal_collection_item(session, "Sold")
    item = session.get(InventoryItem, inv_id)
    item.status = InventoryStatus.AVAILABLE
    session.commit()

    resp = client.post(f"/personal-collection/{inv_id}/upload-photo", files={"photo": ("x.jpg", b"data", "image/jpeg")})
    assert resp.status_code == 404
