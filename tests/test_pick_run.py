from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.labels import LABEL_PRINTED_ACTION
from automation_control.models import (
    AuditEvent,
    CardSet,
    CardVariant,
    CatalogItem,
    CatalogItemType,
    EbayImportedOrder,
    InventoryItem,
    InventoryStatus,
    ShopListing,
    ShopListingStatus,
    StorageLocation,
    TcgCard,
)
from automation_control.nav_counts import queue_counts
from automation_control.pick_run import order_tags


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


@pytest.fixture
def printed(monkeypatch):
    jobs = []
    monkeypatch.setattr("automation_control.labels.send_job", lambda job: jobs.append(job))
    return jobs


def add_card(session, key, *, name, set_name="Romance Dawn", number="1", location=None):
    """A held card with a ShopListing; `location` is its storage location name."""
    if session.get(CardSet, f"set-{set_name}") is None:
        session.add(CardSet(id=f"set-{set_name}", name=set_name, game="one_piece"))
    location_id = None
    if location:
        loc = session.scalar(select(StorageLocation).where(StorageLocation.name == location))
        if loc is None:
            loc = StorageLocation(name=location, kind="card_slot")
            session.add(loc)
            session.flush()
        location_id = loc.id
    session.add(CatalogItem(id=f"cat-{key}", item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=f"cat-{key}", game="one_piece", set_id=f"set-{set_name}",
                        number=number, rarity="C", language="English"))
    session.add(InventoryItem(id=f"inv-{key}", catalog_item_id=f"cat-{key}", variant=CardVariant.NORMAL,
                              condition="Near Mint", quantity=1, status=InventoryStatus.AVAILABLE,
                              storage_location_id=location_id))
    session.add(ShopListing(id=f"sl-{key}", catalog_item_id=f"cat-{key}", inventory_item_id=f"inv-{key}",
                            title=name, price="1.00", quantity=1, status=ShopListingStatus.ACTIVE))
    session.flush()
    return f"sl-{key}"


def add_order(session, order_id, lines, *, age_minutes=0, buyer="A Buyer", dismissed=False):
    """lines: (sku, title, shop_listing_id | None) per order line."""
    observed = datetime.now(UTC) - timedelta(minutes=age_minutes)
    snapshot = {
        "buyer": {"username": buyer},
        "fulfillmentStartInstructions": [{"shippingStep": {"shipTo": {
            "fullName": buyer,
            "contactAddress": {"addressLine1": "1 Test St", "city": "Sydney", "stateOrProvince": "NSW",
                               "postalCode": "2000", "countryCode": "AU"},
        }}}],
        "lineItems": [
            ({"sku": sku} if not sku.startswith("item:") else {"legacyItemId": sku[5:]}) | {"title": title}
            for sku, title, _ in lines
        ],
    }
    for sku, _, listing_id in lines:
        session.add(EbayImportedOrder(
            ebay_order_id=order_id, sku=sku, quantity=1, marketplace_id="EBAY_AU", observed_at=observed,
            applied_at=observed, dismissed_at=observed if dismissed else None,
            shop_listing_id=listing_id, raw_snapshot=snapshot,
        ))
    session.commit()


def test_lines_from_every_open_order_are_sorted_by_drawer_then_slot(client, session):
    late = add_card(session, "late", name="Late Slot", location="OP-01-040")
    early = add_card(session, "early", name="Early Slot", location="OP-01-003")
    dragon = add_card(session, "db", name="Dragon Slot", location="DB-01-122")
    add_order(session, "ORD-1", [("sl-late", "Late Slot listing", late)], age_minutes=30)
    add_order(session, "ORD-2", [("sl-early", "Early Slot listing", early),
                                 ("sl-db", "Dragon Slot listing", dragon)], age_minutes=10)

    body = client.get("/ebay-sales/pick-run").text

    assert body.index("Dragon Slot") < body.index("Early Slot") < body.index("Late Slot")
    assert "slot 003" in body and "slot 122" in body
    assert "2 open order(s), 3 item(s) to pick" in body


def test_unslotted_and_unlisted_lines_get_their_own_sections(client, session):
    loose = add_card(session, "loose", name="Loose Pikachu", set_name="Surging Sparks", number="57")
    add_order(session, "ORD-1", [("sl-loose", "Pikachu listing", loose),
                                 ("item:267815002581", "One Piece TCG Bulk Lot Mixed Cards", None)])

    body = client.get("/ebay-sales/pick-run").text

    assert "No slot recorded — check the bulk drawers" in body
    assert "Loose Pikachu" in body
    assert "Pick by hand — listed directly on eBay" in body
    assert "One Piece TCG Bulk Lot Mixed Cards" in body
    assert body.index("Loose Pikachu") < body.index("One Piece TCG Bulk Lot Mixed Cards")


def test_completed_orders_are_left_off(client, session):
    done = add_card(session, "done", name="Already Packed", location="OP-01-001")
    add_order(session, "ORD-DONE", [("sl-done", "x", done)], dismissed=True)

    body = client.get("/ebay-sales/pick-run").text

    assert "Already Packed" not in body
    assert "Nothing to pick" in body


def test_orders_are_lettered_oldest_first(session):
    add_order(session, "NEW", [("item:1", "a", None)], age_minutes=1)
    add_order(session, "OLD", [("item:2", "b", None)], age_minutes=60)
    rows = {oid: session.scalars(select(EbayImportedOrder).where(EbayImportedOrder.ebay_order_id == oid)).all()
            for oid in ("NEW", "OLD")}

    assert order_tags(rows) == {"OLD": "A", "NEW": "B"}


def test_print_all_prints_each_open_order_once(client, session, printed):
    add_order(session, "ORD-1", [("item:1", "a", None)], age_minutes=20)
    add_order(session, "ORD-2", [("item:2", "b", None)], age_minutes=10)

    first = client.post("/ebay-sales/pick-run/print-labels").json()
    second = client.post("/ebay-sales/pick-run/print-labels").json()

    assert first == {"printed": ["ORD-1", "ORD-2"], "skipped": 0}
    assert second == {"printed": [], "skipped": 2}
    assert len(printed) == 2
    events = session.scalars(select(AuditEvent).where(AuditEvent.action == LABEL_PRINTED_ACTION)).all()
    assert sorted(e.resource_id for e in events) == ["ORD-1", "ORD-2"]


def test_a_label_printed_from_its_own_order_is_skipped_by_print_all(client, session, printed):
    add_order(session, "ORD-1", [("item:1", "a", None)], age_minutes=20)
    add_order(session, "ORD-2", [("item:2", "b", None)], age_minutes=10)

    client.post("/labels/ebay/ORD-1/print")
    result = client.post("/ebay-sales/pick-run/print-labels").json()

    assert result == {"printed": ["ORD-2"], "skipped": 1}
    assert len(printed) == 2


def test_a_printer_failure_stops_the_run_and_records_nothing_for_the_failed_order(client, session, monkeypatch):
    add_order(session, "ORD-1", [("item:1", "a", None)])

    def offline(job):
        raise RuntimeError("printer offline")
    monkeypatch.setattr("automation_control.labels.send_job", offline)

    response = client.post("/ebay-sales/pick-run/print-labels")

    assert response.status_code == 502
    assert "printer offline" in response.json()["error"]
    assert session.scalars(select(AuditEvent).where(AuditEvent.action == LABEL_PRINTED_ACTION)).all() == []


def test_open_orders_are_counted_for_the_nav_badge(session, tmp_path):
    add_order(session, "OPEN", [("item:1", "a", None)])
    add_order(session, "DONE", [("item:2", "b", None)], dismissed=True)

    class _Settings:
        scan_media_dir = str(tmp_path)

    assert queue_counts(session, _Settings())["/ebay-sales"] == 1


def test_pack_assistant_links_to_the_pick_run(client, session):
    assert "/ebay-sales/pick-run" in client.get("/ebay-sales").text
