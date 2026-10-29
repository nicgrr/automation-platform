from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    EbayBulkPickItem,
    EbayBulkPickManifest,
    EbayExportBatch,
    EbayExportItem,
    EbayExportItemStatus,
    EbayImportedOrder,
    EbayListing,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
    StorageLocation,
    TcgCard,
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


def make_order(session, ebay_order_id, *, sku="sku-1", dismissed=False, applied=True,
               buyer="A Buyer", price="12.00"):
    row = EbayImportedOrder(
        ebay_order_id=ebay_order_id, sku=sku, quantity=1, marketplace_id="EBAY_AU",
        observed_at=datetime.now(UTC), applied_at=datetime.now(UTC) if applied else None,
        dismissed_at=datetime.now(UTC) if dismissed else None,
        shop_listing_id=None,
        raw_snapshot={
            "buyer": {"username": buyer},
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {
                    "fullName": buyer,
                    "contactAddress": {"addressLine1": "1 Test St", "city": "Sydney",
                                        "stateOrProvince": "NSW", "postalCode": "2000",
                                        "countryCode": "AU"},
                }}}
            ],
            "lineItems": [{"sku": sku, "title": "Charizard #4", "lineItemCost": {"value": price, "currency": "AUD"}}],
            "pricingSummary": {"total": {"value": price, "currency": "AUD"}},
        },
    )
    session.add(row)
    session.commit()
    return row


def test_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/ebay-sales")
    assert resp.status_code in (401, 403, 307, 302)


def test_the_page_has_a_mobile_breakpoint(client, session):
    assert "@media (max-width: 680px)" in client.get("/ebay-sales").text


def test_an_applied_order_shows_up_by_default(client, session):
    make_order(session, "ORD-1")
    body = client.get("/ebay-sales").text
    assert "ORD-1" in body
    assert "Charizard" in body
    assert "1 Test St" in body


def test_an_unapplied_row_is_never_shown(client, session):
    make_order(session, "ORD-1", applied=False)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_default_filter_hides_a_completed_order(client, session):
    make_order(session, "ORD-1", dismissed=True)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_all_filter_shows_a_completed_order_too(client, session):
    make_order(session, "ORD-1", dismissed=True)
    body = client.get("/ebay-sales?status=all").text
    assert "ORD-1" in body


def test_completed_filter_shows_only_completed_orders(client, session):
    make_order(session, "ORD-NEEDS-ACTION")
    make_order(session, "ORD-DONE", dismissed=True)
    body = client.get("/ebay-sales?status=completed").text
    assert "ORD-DONE" in body
    assert "ORD-NEEDS-ACTION" not in body


def test_a_multi_line_order_is_only_completed_once_every_line_is(client, session):
    make_order(session, "ORD-1", sku="sku-1", dismissed=True)
    make_order(session, "ORD-1", sku="sku-2", dismissed=False)
    body = client.get("/ebay-sales").text  # default: needs action
    assert "ORD-1" in body


def test_marking_complete_dismisses_every_line_on_the_order(client, session):
    make_order(session, "ORD-1", sku="sku-1")
    make_order(session, "ORD-1", sku="sku-2")
    resp = client.post("/ebay-sales/ORD-1/complete", follow_redirects=False)
    assert resp.status_code == 303

    rows = session.query(EbayImportedOrder).filter_by(ebay_order_id="ORD-1").all()
    assert all(r.dismissed_at is not None for r in rows)

    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body
    body = client.get("/ebay-sales?status=completed").text
    assert "ORD-1" in body


def test_discard_asks_for_confirmation(client, session):
    make_order(session, "ORD-1")
    body = client.get("/ebay-sales").text
    assert "onsubmit=\"return confirm('Discard this order from the active view?');\"" in body


def test_discarding_an_order_also_removes_it_from_the_active_view(client, session):
    make_order(session, "ORD-1")
    client.post("/ebay-sales/ORD-1/discard", follow_redirects=False)
    body = client.get("/ebay-sales").text
    assert "ORD-1" not in body


def test_discarding_records_a_distinct_audit_action_from_completing(client, session):
    from automation_control.models import AuditEvent

    make_order(session, "ORD-1")
    make_order(session, "ORD-2")
    client.post("/ebay-sales/ORD-1/complete")
    client.post("/ebay-sales/ORD-2/discard")

    complete_event = session.query(AuditEvent).filter_by(resource_id="ORD-1").one()
    discard_event = session.query(AuditEvent).filter_by(resource_id="ORD-2").one()
    assert complete_event.action == "ebay_sale.complete"
    assert discard_event.action == "ebay_sale.discard"


def test_a_needs_action_order_is_open_by_default(client, session):
    make_order(session, "ORD-OPEN")
    body = client.get("/ebay-sales").text
    assert "<details class='panel order-block' open>" in body


def test_a_completed_order_is_collapsed_by_default(client, session):
    make_order(session, "ORD-DONE", dismissed=True)
    body = client.get("/ebay-sales?status=completed").text
    assert "<details class='panel order-block' open>" not in body
    assert "<details class='panel order-block'>" in body


def make_bulk_order(session, ebay_order_id="ORD-BULK"):
    row = make_order(session, ebay_order_id)
    snapshot = dict(row.raw_snapshot)
    snapshot["lineItems"] = list(snapshot["lineItems"]) + [{
        "lineItemId": "bulk-line-1",
        "legacyItemId": "267785473779",
        "title": "Pokemon TCG Bulk Lot 150+ Cards Common Uncommon Mixed Sets NM-LP",
        "lineItemCost": {"value": "25.95", "currency": "AUD"},
    }]
    row.raw_snapshot = snapshot
    session.commit()
    return row


def seed_bulk_candidates(session):
    set_names = (
        "Prismatic Evolutions", "White Flare", "Phantasmal Flames", "Perfect Order", "Black Bolt",
    )
    batch = EbayExportBatch(filename="confirmed.csv", row_count=150, selected_count=150)
    session.add(batch)
    session.flush()
    for set_index, set_name in enumerate(set_names):
        set_id = f"set-{set_index}"
        session.add(CardSet(id=set_id, name=set_name, game="pokemon"))
        parent_id = f"parent-{set_index}"
        session.add(EbayListing(
            ebay_listing_id=parent_id, title=f"{set_name} variations", currency="AUD",
            current_price_minor=100, listing_status="Active", marketplace="EBAY_AU",
        ))
        for card_index in range(30):
            catalog_id = f"card-{set_index}-{card_index}"
            listing_id = f"listing-{set_index}-{card_index}"
            inventory_id = f"inventory-{set_index}-{card_index}"
            session.add(CatalogItem(
                id=catalog_id, item_type=CatalogItemType.TCG_CARD, name=f"Card {set_index}-{card_index}"
            ))
            session.add(TcgCard(
                catalog_item_id=catalog_id, game="pokemon", set_id=set_id,
                number=str(card_index + 1), rarity="Common" if card_index % 2 else "Uncommon",
                language="English",
            ))
            session.add(InventoryItem(
                id=inventory_id, catalog_item_id=catalog_id, card_id=None,
                variant=CardVariant.NORMAL, condition="Near Mint", quantity=2,
                status=InventoryStatus.AVAILABLE,
            ))
            session.add(ShopListing(
                id=listing_id, catalog_item_id=catalog_id, inventory_item_id=inventory_id,
                title=f"Card {set_index}-{card_index}", price="0.20", quantity=2,
                status=ShopListingStatus.ACTIVE,
            ))
            session.add(EbayExportItem(
                batch_id=batch.id, sku=listing_id, kind="variation_child",
                status=EbayExportItemStatus.CONFIRMED, ebay_listing_id=parent_id,
            ))
    session.commit()


def test_sku_less_bulk_line_is_visible_and_blocks_order_completion(client, session):
    make_bulk_order(session)
    body = client.get("/ebay-sales").text
    assert "150+ Cards" in body
    assert "Build 150-card pick list" in body
    assert "Finish the bulk-card checklist" in body

    client.post("/ebay-sales/ORD-BULK/complete", follow_redirects=False)
    row = session.query(EbayImportedOrder).filter_by(ebay_order_id="ORD-BULK").one()
    assert row.dismissed_at is None


def test_building_bulk_manifest_selects_30_distinct_duplicates_from_each_set(client, session):
    make_bulk_order(session)
    seed_bulk_candidates(session)
    response = client.post(
        "/ebay-sales/ORD-BULK/bulk-picks", data={"line_item_id": "bulk-line-1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    manifest = session.query(EbayBulkPickManifest).one()
    items = session.query(EbayBulkPickItem).filter_by(manifest_id=manifest.id).all()
    assert manifest.requested_quantity == 150
    assert len(items) == 150
    assert len({item.inventory_item_id for item in items}) == 150
    assert all(session.get(InventoryItem, item.inventory_item_id).quantity == 2 for item in items)

    body = client.get(f"/ebay-sales/bulk-picks/{manifest.id}").text
    assert "0 / 150 found" in body
    for set_name in ("Prismatic Evolutions", "White Flare", "Phantasmal Flames", "Perfect Order", "Black Bolt"):
        assert f"{set_name} <span class='muted'>0/30</span>" in body


def test_bulk_pick_cards_show_the_physical_location(client, session):
    make_bulk_order(session)
    seed_bulk_candidates(session)
    first = session.query(InventoryItem).order_by(InventoryItem.id).first()
    location = StorageLocation(name="Pokemon Tray-007", kind="card_slot")
    session.add(location)
    session.flush()
    first.storage_location_id = location.id
    session.commit()

    client.post("/ebay-sales/ORD-BULK/bulk-picks", data={"line_item_id": "bulk-line-1"})
    manifest = session.query(EbayBulkPickManifest).one()
    body = client.get(f"/ebay-sales/bulk-picks/{manifest.id}").text
    assert "Pick location" in body
    assert "Pokemon Tray-007" in body


def test_manifest_cannot_update_stock_until_every_card_is_marked_found(client, session):
    make_bulk_order(session)
    seed_bulk_candidates(session)
    client.post("/ebay-sales/ORD-BULK/bulk-picks", data={"line_item_id": "bulk-line-1"})
    manifest = session.query(EbayBulkPickManifest).one()

    response = client.post(
        f"/ebay-sales/bulk-picks/{manifest.id}/confirm",
        data={"confirmed": "yes"}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert "All+cards+must+be+found" in response.headers["location"]
    assert session.query(InventoryItem).filter(InventoryItem.quantity == 1).count() == 0


def test_pick_progress_counts_physical_quantity_not_database_rows(client, session):
    make_bulk_order(session)
    seed_bulk_candidates(session)
    client.post("/ebay-sales/ORD-BULK/bulk-picks", data={"line_item_id": "bulk-line-1"})
    manifest = session.query(EbayBulkPickManifest).one()
    items = session.query(EbayBulkPickItem).filter_by(manifest_id=manifest.id).all()
    items[0].quantity = 2
    items[0].picked_at = datetime.now(UTC)
    session.delete(items[1])
    session.commit()

    body = client.get(f"/ebay-sales/bulk-picks/{manifest.id}").text
    assert "2 / 150 found" in body
    assert "× 2" in body


def test_confirming_a_complete_pick_updates_ebay_then_local_stock(client, session, monkeypatch):
    make_bulk_order(session)
    seed_bulk_candidates(session)
    client.post("/ebay-sales/ORD-BULK/bulk-picks", data={"line_item_id": "bulk-line-1"})
    manifest = session.query(EbayBulkPickManifest).one()
    now = datetime.now(UTC)
    items = session.query(EbayBulkPickItem).filter_by(manifest_id=manifest.id).all()
    # Aggregate two physical copies into one manifest row, exhausting that
    # inventory identity. This is the voice-entered picker shape used by the
    # real 150-card order, and must be allowed to send quantity zero to eBay.
    items[0].quantity = 2
    session.delete(items[1])
    for item in items[0:1] + items[2:]:
        item.picked_at = now
    session.commit()

    sent = []

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            pass

        async def revise_inventory_status(self, revisions):
            assert 1 <= len(revisions) <= 4
            sent.extend(revisions)

    async def fake_token(*args, **kwargs):
        return "token"

    monkeypatch.setattr("automation_control.ebay_sales.ebay_services", lambda settings: (object(), object()))
    monkeypatch.setattr("automation_control.ebay_sales.valid_user_access_token", fake_token)
    monkeypatch.setattr("automation_control.ebay_sales.EbayTradingQuantityAdapter", FakeAdapter)

    response = client.post(
        f"/ebay-sales/bulk-picks/{manifest.id}/confirm",
        data={"confirmed": "yes"}, follow_redirects=False,
    )
    assert response.status_code == 303
    session.expire_all()
    assert session.get(EbayBulkPickManifest, manifest.id).status == "completed"
    assert len(sent) == 149
    assert sum(revision.quantity == 0 for revision in sent) == 1
    assert session.query(InventoryItem).filter(InventoryItem.quantity == 1).count() == 148
    assert session.query(ShopListing).filter(ShopListing.quantity == 1).count() == 148
    assert session.query(ShopListing).filter(ShopListing.status == ShopListingStatus.SOLD_OUT).count() == 1


def test_an_order_with_only_an_unlisted_line_shows_with_a_pack_by_hand_note(client, session):
    row = make_order(session, "ORD-OP", sku="item:267815002581")
    snapshot = dict(row.raw_snapshot)
    snapshot["lineItems"] = [{"lineItemId": "line-1", "legacyItemId": "267815002581",
                              "title": "One Piece TCG Bulk Lot Common Uncommon Rare Mixed Cards ENGLISH",
                              "lineItemCost": {"value": "22.95", "currency": "AUD"}}]
    row.raw_snapshot = snapshot
    session.commit()

    body = client.get("/ebay-sales").text
    assert "Order ORD-OP" in body
    assert "One Piece TCG Bulk Lot" in body
    assert "$22.95 AUD" in body
    assert "Listed directly on eBay" in body


def test_a_sku_less_bulk_line_with_its_own_row_is_not_drawn_twice(client, session):
    make_bulk_order(session)
    session.add(EbayImportedOrder(
        ebay_order_id="ORD-BULK", sku="item:267785473779", quantity=1, marketplace_id="EBAY_AU",
        observed_at=datetime.now(UTC), applied_at=datetime.now(UTC),
        raw_snapshot=session.query(EbayImportedOrder).filter_by(ebay_order_id="ORD-BULK").one().raw_snapshot,
    ))
    session.commit()

    body = client.get("/ebay-sales").text
    assert body.count("150+ Cards") == 1
    assert "Build 150-card pick list" in body
