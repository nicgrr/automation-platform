import pytest

from decimal import Decimal
from io import BytesIO

from fastapi.testclient import TestClient
from fastapi.responses import RedirectResponse
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import automation_control.ebay_photo_batch as photo_batch
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet,
    CardPrice,
    CardVariant,
    CatalogCard,
    CatalogItem,
    CatalogItemType,
    EbayListingLink,
    EbayListingLinkStatus,
    EbayListing,
    InventoryPlacement,
    InventoryItem,
    ListingPhotoBatch,
    ListingPhotoBatchStatus,
    ListingPhotoPair,
    ListingPhotoPairStatus,
    ShopListing,
    ShopListingStatus,
    StorageLocation,
    TcgCard,
)


def _jpeg() -> bytes:
    out = BytesIO()
    Image.new("RGB", (600, 800), "white").save(out, "JPEG")
    return out.getvalue()


def _listing(session: Session) -> ShopListing:
    session.add(CardSet(id="op17", name="The World's Strongest Warriors", code="OP17", game="one_piece"))
    session.add(CatalogItem(id="op17-119", item_type=CatalogItemType.TCG_CARD, name="Loki"))
    session.add(TcgCard(
        catalog_item_id="op17-119", game="one_piece", set_id="op17", set_code="OP17",
        number="119", rarity="SEC", language="English",
    ))
    session.add(CatalogCard(id="op17-119", set_id="op17", number="119", name="Loki", rarity="SEC"))
    inventory = InventoryItem(
        id="inventory-1", card_id="op17-119", catalog_item_id="op17-119",
        variant=CardVariant.HOLO, condition="Near Mint", quantity=1,
    )
    listing = ShopListing(
        id="listing-1", catalog_item_id="op17-119", inventory_item_id="inventory-1",
        title="Loki #119", condition="Near Mint · Holo", price=Decimal("80"),
        quantity=1, status=ShopListingStatus.ACTIVE,
    )
    session.add_all([inventory, listing])
    session.commit()
    return listing


def test_name_fallback_does_not_attach_a_different_number(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CardSet(id="fs11", name="Starter Deck EX", code="FS11", game="dragon_ball"))
        session.add_all([
            CatalogItem(id="fs11-14", item_type=CatalogItemType.TCG_CARD, name="Vegeta"),
            CatalogItem(id="carddass-597", item_type=CatalogItemType.TCG_CARD, name="Vegeta"),
        ])
        session.add_all([
            TcgCard(catalog_item_id="fs11-14", game="dragon_ball", set_id="fs11", set_code="FS11", number="14"),
            TcgCard(catalog_item_id="carddass-597", game="dragon_ball", set_code="H15", number="597"),
        ])
        wrong = ShopListing(
            id="vegeta-597", catalog_item_id="carddass-597", title="Vegeta #597",
            condition="Near Mint", price=Decimal("10"), quantity=1,
            status=ShopListingStatus.ACTIVE,
        )
        session.add(wrong)
        session.commit()

        matches = photo_batch._active_listing_candidates(
            session, session.get(CatalogItem, "fs11-14"), "Vegeta",
        )

        assert matches == []


def test_bulk_page_offers_fronts_only_and_paired_modes(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            body = TestClient(app).get("/ebay-listing-bulk-photos").text
        finally:
            app.dependency_overrides.clear()
    assert "front, back, front, back" in body
    assert "type='radio' name='has_backs' value='no' checked" in body
    assert "type='radio' name='has_backs' value='yes'>" in body
    assert "Fronts only — one photo per card" in body
    assert "Front + back pairs — two photos per card" in body
    assert "A representative back is added automatically after identification" in body
    assert "Mixed / detect automatically" in body
    assert "Nothing is sent to eBay automatically" in body
    assert "upload-progress-panel" in body
    assert "Photo upload progress" in body
    assert "xhr.upload.addEventListener('progress'" in body
    assert "accept='image/*,.heic,.heif'" in body
    assert "Add another group" in body
    assert "select smaller groups" in body
    assert "selectedFiles.forEach(file=>payload.append('photos',file,file.name))" in body
    assert "previewLimit=12" in body


def test_running_batch_page_shows_processing_progress(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = ListingPhotoBatch(
            id="running-batch", total_pairs=4, processed_pairs=2,
            ready_pairs=2, status=ListingPhotoBatchStatus.RUNNING,
        )
        session.add(batch)
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            body = TestClient(app).get("/ebay-listing-bulk-photos/running-batch").text
        finally:
            app.dependency_overrides.clear()

    assert "Identifying card 3 of 4" in body
    assert "50% complete" in body
    assert "aria-valuenow='50'" in body
    assert "processing-shimmer" in body
    assert "location.reload()" in body
    assert "document.visibilityState !== 'visible'" in body
    assert "pagehide" in body


def test_run_batch_resumes_only_unfinished_pairs(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(photo_batch, "SessionLocal", lambda: Session(engine))
    completed = ListingPhotoPair(
        id="pair-complete", batch_id="resume-batch", position=1,
        front_image_path="front-1.jpg", extracted={}, status=ListingPhotoPairStatus.NEEDS_REVIEW,
    )
    pending_one = ListingPhotoPair(
        id="pair-pending-1", batch_id="resume-batch", position=2,
        front_image_path="front-2.jpg", extracted={}, status=ListingPhotoPairStatus.PROCESSING,
    )
    pending_two = ListingPhotoPair(
        id="pair-pending-2", batch_id="resume-batch", position=3,
        front_image_path="front-3.jpg", extracted={}, status=ListingPhotoPairStatus.PROCESSING,
    )
    with Session(engine) as session:
        session.add(ListingPhotoBatch(
            id="resume-batch", total_pairs=3, processed_pairs=1, review_pairs=1,
            status=ListingPhotoBatchStatus.RUNNING,
        ))
        session.add_all([completed, pending_one, pending_two])
        session.commit()

    prepared = []

    def fake_prepare(pair_id, _settings):
        prepared.append(pair_id)
        with Session(engine) as session:
            pair = session.get(ListingPhotoPair, pair_id)
            pair.status = ListingPhotoPairStatus.NEEDS_REVIEW
            session.commit()

    monkeypatch.setattr(photo_batch, "_prepare_pair", fake_prepare)
    photo_batch._run_batch("resume-batch", Settings(), "testuser")

    assert prepared == ["pair-pending-1", "pair-pending-2"]
    with Session(engine) as session:
        batch = session.get(ListingPhotoBatch, "resume-batch")
        assert batch.processed_pairs == 3
        assert batch.review_pairs == 3
        assert batch.status == ListingPhotoBatchStatus.DONE
        assert batch.finished_at is not None


def test_held_card_explains_what_was_detected(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = ListingPhotoBatch(id="held-batch", total_pairs=1, processed_pairs=1, review_pairs=1)
        session.add_all([batch, ListingPhotoPair(
            id="held-pair", batch_id=batch.id, position=1,
            front_image_path="front.jpg", back_image_path="back.jpg",
            extracted={
                "character": "Turles", "set_name": "10th Anniversary Special Contents",
                "card_number": "", "rarity": "", "language": "Japanese",
                "foil_observation": "Rainbow holographic finish across the card face",
            },
            status=ListingPhotoPairStatus.NEEDS_REVIEW,
            issue="Card name and number are required before it can be listed.",
        )])
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            body = TestClient(app).get("/ebay-listing-bulk-photos/held-batch").text
        finally:
            app.dependency_overrides.clear()

    assert "What the photos tell us" in body
    assert "10th Anniversary Special Contents" in body
    assert "Not visible / not printed" in body
    assert "Japanese" in body
    assert "Rainbow holographic finish" in body


@pytest.mark.parametrize("mode,expected_cards", [("yes", 2), ("no", 4)])
def test_bulk_upload_pairs_files_in_selection_order(tmp_path, monkeypatch, mode, expected_cards):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        old_settings = app.state.settings
        app.state.settings = Settings(anthropic_api_key="test", scan_media_dir=str(tmp_path / "media"))
        monkeypatch.setattr(photo_batch, "_run_batch", lambda *_args: None)
        files = [
            ("photos", (name, _jpeg(), "image/jpeg"))
            for name in ("one-front.jpg", "one-back.jpg", "two-front.jpg", "two-back.jpg")
        ]
        try:
            response = TestClient(app).post(
                "/ebay-listing-bulk-photos",
                data={"game_hint": "one_piece", "has_backs": mode},
                files=files,
                follow_redirects=False,
            )
        finally:
            app.state.settings = old_settings
            app.dependency_overrides.clear()

        assert response.status_code == 303
        batch = session.query(ListingPhotoBatch).one()
        pairs = session.query(ListingPhotoPair).order_by(ListingPhotoPair.position).all()
        assert batch.total_pairs == expected_cards
        assert [pair.position for pair in pairs] == list(range(1, expected_cards + 1))
        assert all(pair.front_image_path for pair in pairs)
        assert all(bool(pair.back_image_path) == (mode == "yes") for pair in pairs)


def test_bulk_upload_reserves_stable_sequential_tray_slots(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        old_settings = app.state.settings
        app.state.settings = Settings(anthropic_api_key="test", scan_media_dir=str(tmp_path / "media"))
        monkeypatch.setattr(photo_batch, "_run_batch", lambda *_args: None)
        files = [
            ("photos", (name, _jpeg(), "image/jpeg"))
            for name in ("one-front.jpg", "one-back.jpg", "two-front.jpg", "two-back.jpg")
        ]
        try:
            response = TestClient(app).post(
                "/ebay-listing-bulk-photos",
                data={
                    "game_hint": "dragon_ball", "has_backs": "yes",
                    "storage_tray": "db 01", "starting_slot": "",
                },
                files=files,
                follow_redirects=False,
            )
            batch = session.query(ListingPhotoBatch).one()
            page_body = TestClient(app).get(f"/ebay-listing-bulk-photos/{batch.id}").text
            labels_body = TestClient(app).get(
                f"/ebay-listing-bulk-photos/{batch.id}/storage-labels"
            ).text
        finally:
            app.state.settings = old_settings
            app.dependency_overrides.clear()

        assert response.status_code == 303
        assert batch.storage_tray == "DB-01"
        assert batch.starting_slot == 1
        placements = session.query(InventoryPlacement).order_by(InventoryPlacement.created_at).all()
        names = [session.get(StorageLocation, row.storage_location_id).name for row in placements]
        assert names == ["DB-01-001", "DB-01-002"]
        assert "Put this card in DB-01-001" in page_body
        assert "Print slot labels" in page_body
        assert "DB-01-001" in labels_body
        assert "DB-01-002" in labels_body


def test_tray_auto_allocation_continues_after_last_reserved_slot(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(StorageLocation(name="DB-01-007", kind="card_slot"))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        old_settings = app.state.settings
        app.state.settings = Settings(anthropic_api_key="test", scan_media_dir=str(tmp_path / "media"))
        monkeypatch.setattr(photo_batch, "_run_batch", lambda *_args: None)
        try:
            response = TestClient(app).post(
                "/ebay-listing-bulk-photos",
                data={"has_backs": "yes", "storage_tray": "DB-01"},
                files=[
                    ("photos", ("front.jpg", _jpeg(), "image/jpeg")),
                    ("photos", ("back.jpg", _jpeg(), "image/jpeg")),
                ],
                follow_redirects=False,
            )
        finally:
            app.state.settings = old_settings
            app.dependency_overrides.clear()

        assert response.status_code == 303
        batch = session.query(ListingPhotoBatch).one()
        placement = session.query(InventoryPlacement).one()
        assert batch.starting_slot == 8
        assert session.get(StorageLocation, placement.storage_location_id).name == "DB-01-008"


def test_separate_physical_copies_keep_separate_slots_when_inventory_is_aggregated(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        listing = _listing(session)
        inventory = session.get(InventoryItem, listing.inventory_item_id)
        inventory.quantity = 2
        batch = ListingPhotoBatch(id="copies", total_pairs=2)
        session.add(batch)
        for position in (1, 2):
            pair = ListingPhotoPair(
                id=f"copy-{position}", batch_id=batch.id, position=position,
                front_image_path=f"front-{position}.jpg", status=ListingPhotoPairStatus.PROCESSING,
            )
            location = StorageLocation(name=f"DB-01-{position:03d}", kind="card_slot")
            session.add_all([pair, location])
            session.flush()
            session.add(InventoryPlacement(
                listing_photo_pair_id=pair.id, storage_location_id=location.id,
            ))
            session.flush()
            photo_batch._link_pair_inventory(session, pair, inventory)
        session.commit()

        placements = session.query(InventoryPlacement).order_by(InventoryPlacement.created_at).all()
        assert [row.inventory_item_id for row in placements] == [inventory.id, inventory.id]
        assert len({row.storage_location_id for row in placements}) == 2
        assert inventory.storage_location_id is None

        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            response = TestClient(app).post(
                f"/ebay-listing-bulk-photos/{batch.id}/confirm-storage",
                data={"confirmation": "stored"},
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 303
        session.refresh(inventory)
        for placement in placements:
            session.refresh(placement)
            assert placement.confirmed_at is not None
            assert placement.confirmed_by == "testuser"
        assert inventory.storage_location_id in {row.storage_location_id for row in placements}


def test_storage_confirmation_requires_physical_putaway_checkbox(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ListingPhotoBatch(id="unconfirmed", total_pairs=0))
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            response = TestClient(app).post(
                "/ebay-listing-bulk-photos/unconfirmed/confirm-storage",
                data={"confirmation": ""},
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 400


def test_odd_front_back_upload_is_rejected(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        old_settings = app.state.settings
        app.state.settings = Settings(anthropic_api_key="test")
        try:
            response = TestClient(app).post(
                "/ebay-listing-bulk-photos",
                data={"has_backs": "yes"},
                files=[("photos", ("front.jpg", _jpeg(), "image/jpeg"))],
                follow_redirects=False,
            )
        finally:
            app.state.settings = old_settings
            app.dependency_overrides.clear()
    assert response.status_code == 303
    assert "even+number" in response.headers["location"]
    assert session.query(ListingPhotoBatch).count() == 0


def test_apply_pair_updates_only_the_crm_draft(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        listing = _listing(session)
        batch = ListingPhotoBatch(id="batch-1", total_pairs=1)
        pair = ListingPhotoPair(
            id="pair-1", batch_id=batch.id, position=1,
            front_image_path="front.jpg", back_image_path="back.jpg",
            matched_shop_listing_id=listing.id, proposed_catalog_item_id="op17-119",
            proposed_title="Loki OP17-119 SEC Holo One Piece TCG English Alternate Art Near Mint",
            proposed_description="The actual front and back photographs show the card offered for sale.",
            proposed_price=Decimal("85"), price_source="manual",
            status=ListingPhotoPairStatus.READY,
        )
        session.add_all([batch, pair])
        session.commit()

        location = StorageLocation(name="OP-01-001", kind="card_slot")
        session.add(location)
        session.flush()
        placement = InventoryPlacement(
            listing_photo_pair_id=pair.id, storage_location_id=location.id, quantity=1,
        )
        session.add(placement)
        session.flush()

        assert photo_batch._apply_pair(session, pair, quantity=3) is None
        session.commit()

        session.refresh(listing)
        inventory = session.get(InventoryItem, listing.inventory_item_id)
        session.refresh(placement)
        assert listing.title == "Loki #119"
        assert listing.ebay_title.startswith("Loki OP17-119")
        assert listing.ebay_photo_paths == ["front.jpg", "back.jpg"]
        assert listing.price == Decimal("85")
        assert listing.quantity == 3
        assert inventory.quantity == 3
        assert placement.quantity == 3
        assert pair.status == ListingPhotoPairStatus.APPLIED


def test_ready_pair_can_apply_and_update_its_verified_live_ebay_listing(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    called = {}

    async def fake_revise(listing_id, *, confirmation, user, session):
        called.update(listing_id=listing_id, confirmation=confirmation, user=user)
        return RedirectResponse(
            f"/shop-listings/{listing_id}/ebay-preview?notice=updated", status_code=303,
        )

    monkeypatch.setattr(photo_batch, "revise_listing_on_ebay", fake_revise)
    with Session(engine) as session:
        listing = _listing(session)
        session.add(EbayListingLink(
            shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
            ebay_listing_id="267800000001", status=EbayListingLinkStatus.ACTIVE,
        ))
        batch = ListingPhotoBatch(id="batch-live", total_pairs=1, ready_pairs=1, processed_pairs=1)
        pair = ListingPhotoPair(
            id="pair-live", batch_id=batch.id, position=1,
            front_image_path="front.jpg", back_image_path="back.jpg",
            matched_shop_listing_id=listing.id, proposed_catalog_item_id="op17-119",
            proposed_title="Loki OP17-119 SEC Holo One Piece TCG English",
            proposed_description="Actual front and back photographs of the card offered for sale.",
            proposed_price=Decimal("85"), price_source="manual",
            status=ListingPhotoPairStatus.READY,
        )
        session.add_all([batch, pair])
        session.commit()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
        try:
            page_body = TestClient(app).get(f"/ebay-listing-bulk-photos/{batch.id}").text
            response = TestClient(app).post(
                f"/ebay-listing-bulk-photos/pair/{pair.id}/publish",
                data={"confirmation": "live", "quantity": "4"}, follow_redirects=False,
            )
            published_page = TestClient(app).get(f"/ebay-listing-bulk-photos/{batch.id}").text
        finally:
            app.dependency_overrides.clear()

        assert "Apply &amp; update eBay 267800000001" in page_body
        assert "front?thumb=1" in page_body
        assert "back?thumb=1" in page_body
        assert "loading='lazy'" in page_body
        assert response.status_code == 303
        session.refresh(pair)
        session.refresh(listing)
        assert pair.status == ListingPhotoPairStatus.PUBLISHED
        assert listing.ebay_photo_paths == ["front.jpg", "back.jpg"]
        assert listing.quantity == 4
        assert session.get(InventoryItem, listing.inventory_item_id).quantity == 4
        assert "<details class='pair-done'>" in published_page
        assert "eBay 267800000001" in published_page
        assert "tap to view" in published_page
        assert "<details class='pair-done' open>" not in published_page
        assert called == {
            "listing_id": listing.id,
            "confirmation": "revise",
            "user": "testuser",
        }


def test_large_automated_price_jump_keeps_current_live_price_for_one_click(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        listing = _listing(session)
        session.add_all([
            EbayListingLink(
                shop_listing_id=listing.id, sku=listing.id, marketplace_id="EBAY_AU",
                ebay_listing_id="267800000002", status=EbayListingLinkStatus.ACTIVE,
            ),
            EbayListing(
                ebay_listing_id="267800000002", title="Loki OP17-119", currency="AUD",
                current_price_minor=8500, listing_status="Active", quantity=1, raw_snapshot={},
            ),
            CardPrice(
                catalog_item_id="op17-119", source="ebay_active_median",
                price=Decimal("250"), currency="AUD",
            ),
        ])
        session.commit()

        price, source = photo_batch._proposal_price(
            session, session.get(CatalogItem, "op17-119"), listing,
        )

        assert price == Decimal("85.00")
        assert source == "current live eBay (market outlier held: ebay_active_median)"


def test_catalogue_resolution_accepts_full_code_against_split_number(tmp_path):
    from automation_control.capture_promotion import resolve_catalogue_card
    from automation_control.models import CapturedCard

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _listing(session)
        capture = CapturedCard(
            front_image_path="front.jpg", character="Loki",
            set_name="OP17", card_number="OP17-119", language="English",
        )
        resolved = resolve_catalogue_card(session, capture)
        assert not isinstance(resolved, str)
        assert resolved[0].id == "op17-119"
