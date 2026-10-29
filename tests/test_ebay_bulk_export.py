"""The eBay File Exchange export: preview every candidate listing read
live from the database, let a listing be deselected without touching its
own status, then build the real upload CSV from exactly what's ticked.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import (
    BulkLot,
    CardPrice,
    CardSet,
    CatalogItem,
    CatalogItemType,
    EbayExportBatch,
    EbayExportItem,
    EbayExportItemStatus,
    EbayListing,
    EbayListingLink,
    EbayListingLinkStatus,
    ShopListing,
    ShopListingStatus,
    TcgCard,
)

# A minimal but real-shaped File Exchange template: an Info row then a
# header row starting with "*Action(...)", \r-delimited exactly like the
# real download -- see convert_to_ebay_template.read_ebay_header. Built
# fresh per test in tmp_path (test_convert_to_ebay_template.py's own
# convention) rather than depending on the real repo-root file staying in
# whatever shape it happens to be today.
_TEMPLATE_HEADER = [
    "*Action(SiteID=Australia|Country=AU|Currency=AUD|Version=1193|CC=UTF-8)",
    "CustomLabel", "*Category", "*Title", "*ConditionID", "*C:Type", "*C:Franchise",
    "*C:Manufacturer", "PicURL", "*Description", "*Format", "*Duration", "*StartPrice",
    "*Quantity", "*Location", "*DispatchTimeMax", "*ReturnsAcceptedOption",
    "ShippingProfileName", "ReturnProfileName", "PaymentProfileName", "C:Item Weight",
    "*C:Set", "C:Card Name", "C:Card Number", "C:Rarity", "CD:Card Condition - (ID: 40001)",
    "Relationship", "RelationshipDetails",
]


@pytest.fixture
def template_path(tmp_path):
    path = tmp_path / "template.csv"
    path.write_bytes(("﻿Info,Version=1.0.0,Template=fx_category_template_EBAY_AU\r"
                      + ",".join(_TEMPLATE_HEADER) + "\r").encode("utf-8"))
    return str(path)


@pytest.fixture(autouse=True)
def _template(monkeypatch, template_path):
    import automation_control.ebay_bulk_export as m
    monkeypatch.setattr(m, "TEMPLATE_PATH", template_path)


def _settings(**overrides) -> Settings:
    base = dict(
        ebay_ship_from_location="Sydney, NSW, Australia", ebay_dispatch_days=1,
        ebay_listing_payment_policy_name="Trading Cards - Standard Payment Policy",
        ebay_listing_return_policy_name="Trading Cards & Sealed TCG - Refund Policy",
        ebay_letter_shipping_policy_name="Trading Cards & Sealed TCG - Australia Post",
        ebay_parcel_shipping_policy_name="Trading Cards & Sealed TCG - Tracked Parcel",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def engine(tmp_path):
    # A real file, not :memory: -- TestClient runs sync routes in a
    # threadpool, and a :memory: sqlite DB is a fresh, empty database on
    # every new connection a different thread checks out from the pool
    # (see test_price_review_routes.py's identical engine fixture).
    db_path = tmp_path / "test.db"
    return create_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})


@pytest.fixture
def session(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    original = app.state.settings
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "nic"
    app.state.settings = _settings()
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings = original


def _single(session, sku: str, title: str, *, number: str = "1", set_id: str = "xy11",
            set_name: str = "Steam Siege", price: str = "5.00", language: str = "English") -> str:
    if session.get(CardSet, set_id) is None:
        session.add(CardSet(id=set_id, name=set_name))
    item = CatalogItem(id=f"item-{sku}", name=title, item_type=CatalogItemType.TCG_CARD,
                       image_url="https://example.test/item.jpg")
    session.add(item)
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id=set_id, number=number, language=language))
    listing = ShopListing(id=sku, catalog_item_id=item.id, title=title, price=Decimal(price),
                          status=ShopListingStatus.ACTIVE, quantity=1)
    session.add(listing)
    session.commit()
    return sku


def _active_variation_parent(session, set_name: str, *, started_at: datetime) -> EbayListing:
    listing = EbayListing(
        ebay_listing_id=f"ebay-{set_name}",
        sku=None,
        title=f"{set_name} - Complete Your Master Set",
        currency="AUD",
        current_price_minor=100,
        listing_status="Active",
        quantity=1,
        raw_snapshot={
            "start_time": started_at.isoformat().replace("+00:00", "Z"),
            "view_item_url": f"https://www.ebay.com.au/itm/{set_name}",
        },
    )
    session.add(listing)
    session.commit()
    return listing


def test_the_page_gates_on_missing_config(client):
    app.state.settings = _settings(ebay_letter_shipping_policy_name=None,
                                   ebay_listing_payment_policy_name=None)
    page = client.get("/ebay-export").text
    assert "Can't build the export" in page
    assert "letter shipping policy name" in page
    assert "payment policy name" in page
    assert "dispatch days" not in page.split("missing from .env.local:")[1].split("</p>")[0]


def test_the_page_lists_a_real_single_once_configured(client, session):
    _single(session, "sku-1", "Abra #54")
    page = client.get("/ebay-export").text
    assert "Can't build" not in page
    assert "Abra #54" in page
    assert "value='sku-1'" in page


def test_a_card_priced_under_a_dollar_shows_ebays_real_minimum_on_the_page(client, session):
    """Real rejection (2026-09-26, ErrorCode 73): a genuinely, correctly
    priced card under eBay's $1 FixedPrice minimum must be raised to it
    before a human ever reviews this page -- otherwise what's shown here
    doesn't match what the actual upload would do."""
    _single(session, "sku-cheap", "Cheap Card #1", price="0.26")

    page = client.get("/ebay-export").text

    assert "AUD $1.00" in page
    assert "AUD $0.26" not in page


def test_export_download_keeps_the_page_open_and_offers_mobile_save(client, session):
    _single(session, "sku-1", "Abra #54")

    page = client.get("/ebay-export").text

    # The new-window target is the no-JavaScript safety net. Normal browsers
    # use the background fetch and, on iOS, a user-triggered share/save sheet.
    assert "target='_blank' data-no-progress data-export-form" in page
    assert "data-save-export" in page
    assert "fetch(form.action" in page
    assert "navigator.share" in page
    assert "This page stayed open" in page


def test_export_build_drives_the_shared_progress_bar_by_hand(client, session):
    """The build form is data-no-progress (it has its own richer status
    line, see showStatus), which means the shared bar's generic submit
    listener skips it -- so this form must drive EzBayProgress itself,
    same as quick_price.py/deal_check.py do for their own fetch() calls."""
    _single(session, "sku-1", "Abra #54")
    page = client.get("/ebay-export").text
    assert "window.EzBayProgress.begin('POST /ebay-export/build')" in page
    assert "window.EzBayProgress.done(built ? 'POST /ebay-export/build' : null)" in page


def test_build_refuses_without_config(client):
    app.state.settings = _settings(ebay_ship_from_location=None)
    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_build_refuses_when_nothing_is_selected(client):
    resp = client.post("/ebay-export/build", data={}, follow_redirects=False)
    assert resp.status_code == 303
    assert "Nothing" in resp.headers["location"]


def test_build_exports_only_the_selected_single(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    _single(session, "sku-2", "Kadabra #55", number="55")

    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.content.decode("utf-8-sig")
    assert "Abra" in body
    assert "Kadabra" not in body


def test_bulk_export_blocks_a_materially_underpriced_selected_card(client, session):
    sku = _single(session, "sku-risk", "Loki (Alternate Art) #OP17-119", price="36.36")
    session.add(CardPrice(
        catalog_item_id="item-sku-risk", source="ebay_active_median",
        price=Decimal("90.00"), currency="AUD",
    ))
    session.commit()

    response = client.post(
        "/ebay-export/build", data={"sku": [sku]}, follow_redirects=False,
    )

    assert response.status_code == 303
    assert "Pricing%20safety%20stopped%201%20selected%20card" in response.headers["location"]
    assert "%2467.50" in response.headers["location"]
    assert session.scalars(select(EbayExportBatch)).all() == []


def test_bulk_page_offers_an_explicit_override_for_a_pricing_hold(client, session):
    _single(session, "sku-risk", "Loki (Alternate Art) #OP17-119", price="36.36")
    session.add(CardPrice(
        catalog_item_id="item-sku-risk", source="ebay_active_median",
        price=Decimal("90.00"), currency="AUD",
    ))
    session.commit()

    page = client.get("/ebay-export").text

    assert "Pricing safety holds" in page
    assert "PRICING HOLD" in page
    assert "review floor $67.50" in page
    checkbox = page.split("value='sku-risk'", 1)[1].split(">", 1)[0]
    assert "disabled" not in checkbox
    assert "name='pricing_override'" in page
    assert "Tick to override the hold and include this listing" in page


def test_bulk_export_accepts_an_explicit_pricing_hold_override(client, session):
    sku = _single(session, "sku-risk", "Loki (Alternate Art) #OP17-119", price="36.36")
    session.add(CardPrice(
        catalog_item_id="item-sku-risk", source="ebay_active_median",
        price=Decimal("90.00"), currency="AUD",
    ))
    session.commit()

    response = client.post(
        "/ebay-export/build", data={"pricing_override": [sku]}, follow_redirects=False,
    )

    assert response.status_code == 200
    assert "attachment; filename=ebay_bulk_listing_ready.csv" in response.headers["content-disposition"]
    assert sku in response.content.decode("utf-8-sig")
    assert session.scalar(select(EbayExportBatch)).selected_count == 1


def test_build_streams_a_real_file_exchange_shaped_csv(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    assert resp.status_code == 200
    assert "attachment; filename=ebay_bulk_listing_ready.csv" in resp.headers["content-disposition"]
    body = resp.content.decode("utf-8-sig")
    assert body.startswith("Info,Version")
    rows = body.split("\r\n")
    assert rows[1] == ",".join(_TEMPLATE_HEADER)


def test_download_records_each_sku_pending_and_withholds_it_from_next_export(client, session):
    _single(session, "sku-1", "Abra #54", number="54")

    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    assert resp.status_code == 200

    batch = session.scalar(select(EbayExportBatch))
    item = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == "sku-1"))
    assert batch.row_count == 1
    assert item.status == EbayExportItemStatus.PENDING

    page = client.get("/ebay-export").text
    assert "1 awaiting eBay" in page
    assert "value='sku-1'" not in page


def test_active_direct_publish_link_is_withheld_from_bulk_export(client, session):
    """A directly published card must not remain selectable in the CSV
    workflow, or using both supported handoffs creates a duplicate listing."""
    _single(session, "sku-direct", "Abra #54", number="54")
    session.add(EbayListingLink(
        shop_listing_id="sku-direct",
        sku="sku-direct",
        marketplace_id="EBAY_AU",
        ebay_listing_id="267800000001",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.commit()

    page = client.get("/ebay-export").text
    assert "value='sku-direct'" not in page
    assert "No active singles ready to export" in page


def test_direct_publish_race_is_rechecked_when_building_csv(client, session):
    """The submit route rebuilds populations, so a direct publish that
    happened after the page opened still blocks a stale checked SKU."""
    _single(session, "sku-direct", "Abra #54", number="54")
    session.add(EbayListingLink(
        shop_listing_id="sku-direct",
        sku="sku-direct",
        marketplace_id="EBAY_AU",
        ebay_listing_id="267800000001",
        status=EbayListingLinkStatus.ACTIVE,
    ))
    session.commit()

    response = client.post(
        "/ebay-export/build", data={"sku": ["sku-direct"]}, follow_redirects=False,
    )
    assert response.status_code == 303
    assert "direct_active" in response.headers["location"]


def test_reselecting_an_already_pending_sku_gives_the_real_reason_not_a_duplicate_number_guess(client, session):
    """A build with an empty result was always reported as "every selected
    variation card was a duplicate number in its set" -- true for
    build_variation_group's own dedup, but not for this cause: selecting
    a SKU a moment after its own successful build, before that batch is
    confirmed/cancelled/failed. Real case, 2026-09-27: a 46-card
    re-selection right after a 1293-card batch had already sublled those
    same 46 into "pending", leaving nothing left to build the second time."""
    _single(session, "sku-1", "Abra #54", number="54")
    first = client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    assert first.status_code == 200

    from urllib.parse import unquote

    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]}, follow_redirects=False)
    assert resp.status_code == 303
    location = unquote(resp.headers["location"])
    assert "already in the export ledger" in location
    assert "duplicate number" not in location


def test_unselected_card_in_a_pending_variation_set_remains_exportable_as_a_single(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    _single(session, "sku-2", "Kadabra #55", number="55")

    client.post("/ebay-export/build", data={"sku": ["sku-1"]})

    page = client.get("/ebay-export").text
    assert "value='sku-1'" not in page
    assert "value='sku-2'" in page
    assert "No new variation listings are ready right now" in page


def test_active_ebay_variation_is_read_only_and_separate_from_new_candidates(client, session):
    first = _single(session, "sku-1", "Abra #54", number="54")
    second = _single(session, "sku-2", "Kadabra #55", number="55")
    parent = _active_variation_parent(
        session, "Steam Siege", started_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    page = client.get("/ebay-export").text

    assert "Already listed variations" in page
    assert "Steam Siege" in page
    assert f"View eBay #{parent.ebay_listing_id}" in page
    assert "2 cards" in page
    assert f"value='{first}'" not in page
    assert f"value='{second}'" not in page
    assert "No new variation listings are ready right now" in page


def test_card_created_after_active_parent_is_separate_and_exportable_as_a_single(client, session):
    _active_variation_parent(
        session, "Steam Siege", started_at=datetime.now(UTC) - timedelta(days=1),
    )
    sku = _single(session, "sku-new", "Newly scanned Abra #54", number="54")

    page = client.get("/ebay-export").text

    assert "New cards for existing eBay variations" in page
    assert "Newly scanned Abra #54" in page
    assert f"value='{sku}'" in page

    resp = client.post("/ebay-export/build", data={"sku": [sku]})
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "Newly scanned Abra" in body
    assert "set-variation-steam-siege" not in body


def test_build_rechecks_live_ebay_parent_and_refuses_duplicate_variation(client, session):
    first = _single(session, "sku-1", "Abra #54", number="54")
    second = _single(session, "sku-2", "Kadabra #55", number="55")
    _active_variation_parent(
        session, "Steam Siege", started_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    resp = client.post(
        "/ebay-export/build", data={"sku": [first, second]}, follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "Nothing%20exportable" in resp.headers["location"]


def test_a_language_suffixed_parents_result_confirms_its_real_children(client, session):
    """Real bug, confirmed live 2026-09-28: _variation_group_children
    matched a parent's children by bare CardSet name, which can never
    equal a language-qualified parent SKU like
    "set-variation-saiyan-s-pride-japanese" -- so a real, successful
    eBay result for any non-English set confirmed only the synthetic
    parent row, leaving its actual child SKUs stuck pending forever."""
    first = _single(session, "sku-1", "Son Gohan A #56", number="56", language="Japanese")
    second = _single(session, "sku-2", "Son Gohan B #57", number="57", language="Japanese")

    build = client.post("/ebay-export/build", data={"sku": [first, second]}, follow_redirects=False)
    assert build.status_code == 200, build.headers.get("location")
    body = build.content.decode("utf-8-sig")
    assert "set-variation-steam-siege-japanese" in body

    result = (
        "Line Number,Action,Status,ErrorCode,ErrorMessage,WarningCode,WarningMessage,ItemID,CustomLabel\r\n"
        "2,Add,Warning,,,21917236,Payment hold warning,267799999999,set-variation-steam-siege-japanese\r\n"
    ).encode()
    resp = client.post(
        "/ebay-export/results", files={"results": ("ebay-result.csv", result, "text/csv")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "1%20confirmed" in resp.headers["location"]

    for sku in (first, second):
        item = session.scalar(
            select(EbayExportItem).where(EbayExportItem.sku == sku).order_by(EbayExportItem.exported_at.desc())
        )
        assert item.status == EbayExportItemStatus.CONFIRMED
        assert item.ebay_listing_id == "267799999999"


def test_imported_ebay_warning_with_item_id_confirms_a_pending_sku(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    result = (
        "Line Number,Action,Status,ErrorCode,ErrorMessage,WarningCode,WarningMessage,ItemID,CustomLabel\r\n"
        "2,Add,Warning,,,21917236,Payment hold warning,267792969854,sku-1\r\n"
    ).encode()

    resp = client.post(
        "/ebay-export/results",
        files={"results": ("ebay-result.csv", result, "text/csv")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "1%20confirmed" in resp.headers["location"]
    item = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == "sku-1"))
    assert item.status == EbayExportItemStatus.CONFIRMED
    assert item.ebay_listing_id == "267792969854"
    assert item.confirmed_at is not None


def test_failed_ebay_result_makes_a_pending_sku_retryable(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    result = (
        "Status,ErrorCode,ErrorMessage,ItemID,CustomLabel\r\n"
        "Failure,21919188,Invalid picture URL,,sku-1\r\n"
    ).encode()

    client.post("/ebay-export/results", files={"results": ("failed.csv", result, "text/csv")})

    item = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == "sku-1"))
    assert item.status == EbayExportItemStatus.FAILED
    assert "Invalid picture URL" in item.error
    assert "value='sku-1'" in client.get("/ebay-export").text


def test_failed_variation_parent_releases_all_of_its_children(client, session):
    first = _single(session, "sku-1", "Abra #54", number="54")
    second = _single(session, "sku-2", "Kadabra #55", number="55")
    client.post("/ebay-export/build", data={"sku": [first, second]})
    result = (
        "Status,ErrorCode,ErrorMessage,ItemID,CustomLabel\r\n"
        "Failure,21920469,Prohibited active content,,set-variation-steam-siege\r\n"
    ).encode()

    response = client.post(
        "/ebay-export/results", files={"results": ("failed.csv", result, "text/csv")},
        follow_redirects=False,
    )

    items = session.scalars(select(EbayExportItem)).all()
    assert all(item.status == EbayExportItemStatus.FAILED for item in items)
    assert "2%20variation%20cards%20released%20for%20retry" in response.headers["location"]


def test_a_pre_ledger_result_file_seeds_confirmed_duplicate_prevention(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    result = (
        "Status,ErrorCode,ErrorMessage,ItemID,CustomLabel\r\n"
        "Success,,,267792969854,sku-1\r\n"
    ).encode()

    client.post("/ebay-export/results", files={"results": ("old-result.csv", result, "text/csv")})

    item = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == "sku-1"))
    assert item.kind == "imported"
    assert item.status == EbayExportItemStatus.CONFIRMED
    assert "value='sku-1'" not in client.get("/ebay-export").text


def test_pending_batch_can_be_cancelled_only_when_the_file_was_not_uploaded(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    batch = session.scalar(select(EbayExportBatch))

    resp = client.post(f"/ebay-export/batch/{batch.id}/retry", follow_redirects=False)

    assert resp.status_code == 303
    item = session.scalar(select(EbayExportItem).where(EbayExportItem.sku == "sku-1"))
    assert item.status == EbayExportItemStatus.CANCELLED
    assert "value='sku-1'" in client.get("/ebay-export").text


def test_build_reports_an_unreadable_template_instead_of_500(client, session, monkeypatch):
    _single(session, "sku-1", "Abra #54", number="54")

    def unreadable(_path):
        raise PermissionError(13, "Permission denied", "template.csv")

    monkeypatch.setattr("scripts.convert_to_ebay_template.read_ebay_header", unreadable)
    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]}, follow_redirects=False)

    assert resp.status_code == 303
    assert "Could%20not%20read%20the%20eBay%20upload%20template" in resp.headers["location"]


def test_a_selected_bulk_lot_is_exported_with_the_confirmed_condition_id(client, session):
    """Confirmed 2026-09-28 (GetItem against 3 real active category-183455
    listings, see convert_to_ebay_template.DEFAULT_LOT_CONDITION_ID) -- lots
    are selectable now, and a real ConditionID (3000, Used) goes on the row
    instead of the old REPLACE_ME placeholder."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    lot = BulkLot(id="lot-1", title="Obsidian Flames — bulk lot (30 cards)", set_id="sv3", card_count=30,
                 price=Decimal("20"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.commit()

    resp = client.post("/ebay-export/build", data={"sku": [lot.id]}, follow_redirects=False)
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "lot-1" in body
    assert "3000" in body
    assert "REPLACE_ME" not in body


def test_a_lot_already_in_the_ledger_is_not_exported_twice(client, session):
    """Lots need the same ledger duplicate-prevention gate every other
    selectable population already has (added alongside making them
    selectable, 2026-09-28) -- otherwise a lot from an earlier confirmed
    or still-pending batch could be resubmitted and create a duplicate
    listing."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    lot = BulkLot(id="lot-1", title="Obsidian Flames — bulk lot (30 cards)", set_id="sv3", card_count=30,
                 price=Decimal("20"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.add(EbayExportItem(batch_id="b1", sku="lot-1", kind="lot",
                               status=EbayExportItemStatus.CONFIRMED, exported_at=datetime.now(UTC)))
    session.commit()

    resp = client.post("/ebay-export/build", data={"sku": [lot.id]}, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_variation_candidates_group_into_one_parent_per_set(client, session):
    """A cheap single from a lot-covered set is offered as a variation
    candidate instead of a plain single -- selecting two such cards from
    the same set should produce one parent row plus two child rows."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    session.add(BulkLot(title="Obsidian Flames — bulk lot (30 cards)", set_id="sv3", card_count=30,
                        price=Decimal("20"), image_url="https://example.test/logo.png",
                        status=ShopListingStatus.ACTIVE))
    _single(session, "sku-1", "Common #4", number="4", set_id="sv3", set_name="Obsidian Flames", price="1.00")
    _single(session, "sku-2", "Common #5", number="5", set_id="sv3", set_name="Obsidian Flames", price="1.00")
    session.commit()

    resp = client.post("/ebay-export/build", data={"sku": ["sku-1", "sku-2"]})
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert body.count("Variation") == 2  # two children, each tagged Relationship=Variation
    assert "set-variation-obsidian-flames" in body


def test_ebay_export_is_reachable_from_the_nav(client):
    from automation_control.ui import ADMIN_NAV, TOOL_NAV

    hrefs = [href for _, links, *_ in ADMIN_NAV for href, _ in links]
    tools = [href for _, links, *_ in TOOL_NAV for href, _ in links]
    assert "/ebay" in hrefs
    assert "/ebay-export" in tools


def test_build_handles_well_over_a_thousand_selected_skus(client, session):
    """Real bug, 2026-09-22: FastAPI's usual `sku: list[str] = Form(...)`
    parameter injection always calls request.form() with Starlette's hard
    default max_fields=1000 (no way to raise it through the Form(...)
    marker itself), and one checkbox per card across 40 real set listings
    is nowhere near a safe margin under that -- 1,954 cards hit "Too many
    fields" on a real submit. ebay_export_build now parses the form body
    itself with a much higher max_fields instead."""
    skus = []
    for n in range(1200):
        sku = f"sku-{n}"
        _single(session, sku, f"Card {n} #{n}", number=str(n))
        skus.append(sku)
    session.commit()

    resp = client.post("/ebay-export/build", data={"sku": skus})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.content.decode("utf-8-sig")
    # sku-N is each row's CustomLabel, unique per card -- unlike title/
    # description (deliberately identical in _single, so they'd double-
    # count in the raw text regardless of grouping).
    assert "sku-0" in body
    assert "sku-1199" in body


# --- a non-English card is flagged at the row itself, not just on a group
# heading (2026-09-27) -- a lone Japanese card too few to form its own
# "(Japanese)" group falls out as an ordinary single with no group
# heading to carry that at all otherwise. ------------------------------

def test_a_japanese_single_shows_a_language_badge_on_the_page(client, session):
    _single(session, "sku-jp", "Card One #1", language="Japanese")
    session.commit()

    page = client.get("/ebay-export").text
    assert "class='xlang'" in page
    assert "Japanese" in page


def test_an_english_single_shows_no_language_badge(client, session):
    """English is the overwhelming default -- flagging it on every row
    would just be noise back on the page this whole thing set out to
    de-clutter."""
    _single(session, "sku-en", "Card One #1", language="English")
    session.commit()

    page = client.get("/ebay-export").text
    assert "class='xlang'" not in page


# --- collapsible reference sections + an inline refresh (2026-09-27) --
# Both "Bulk Master lots" (selectable since 2026-09-28) and "Already listed
# variations" (still read-only) can run to 30-40+ rows -- collapsed by
# default so they don't force scrolling past all of it to reach the
# actual submit button.

def test_the_two_reference_sections_are_wrapped_in_a_collapsible_details(client, session):
    page = client.get("/ebay-export").text
    assert page.count("<details class='xpanel-fold") == 2
    assert "Bulk Master lots" in page
    assert "Already listed variations" in page


def test_refresh_listings_button_posts_to_its_own_route(client, session):
    page = client.get("/ebay-export").text
    assert "action='/ebay-export/refresh-listings'" in page


def test_refresh_listings_route_pulls_fresh_data_and_redirects_back(client, session):
    from unittest.mock import AsyncMock, patch as mock_patch

    with mock_patch("automation_control.ebay_listings.ebay_services", return_value=("cipher", "oauth")), \
         mock_patch("automation_control.ebay_listings.valid_user_access_token", new=AsyncMock(return_value="tok")), \
         mock_patch("automation_control.ebay_listings.EbayTradingReadAdapter") as adapter_cls:
        adapter_cls.return_value.get_active_listings = AsyncMock(return_value=[])
        resp = client.post("/ebay-export/refresh-listings", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ebay-export?notice=")


def test_refresh_listings_route_surfaces_a_real_error_not_a_traceback(client, session):
    from unittest.mock import AsyncMock, patch as mock_patch

    from automation_control.adapters.ebay import EbayApiError

    with mock_patch("automation_control.ebay_listings.ebay_services", return_value=("cipher", "oauth")), \
         mock_patch("automation_control.ebay_listings.valid_user_access_token",
                    new=AsyncMock(side_effect=EbayApiError("eBay is unreachable"))):
        resp = client.post("/ebay-export/refresh-listings", follow_redirects=False)

    assert resp.status_code == 303
    assert "eBay%20is%20unreachable" in resp.headers["location"] or "eBay is unreachable" in resp.headers["location"]
