"""The eBay File Exchange export: preview every candidate listing read
live from the database, let a listing be deselected without touching its
own status, then build the real upload CSV from exactly what's ticked.
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.models import BulkLot, CardSet, CatalogItem, CatalogItemType, ShopListing, ShopListingStatus, TcgCard

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
            set_name: str = "Steam Siege", price: str = "5.00") -> str:
    if session.get(CardSet, set_id) is None:
        session.add(CardSet(id=set_id, name=set_name))
    item = CatalogItem(id=f"item-{sku}", name=title, item_type=CatalogItemType.TCG_CARD,
                       image_url="https://example.test/item.jpg")
    session.add(item)
    session.add(TcgCard(catalog_item_id=item.id, game="pokemon", set_id=set_id, number=number))
    listing = ShopListing(id=sku, catalog_item_id=item.id, title=title, price=Decimal(price),
                          status=ShopListingStatus.ACTIVE, quantity=1)
    session.add(listing)
    session.commit()
    return sku


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


def test_build_streams_a_real_file_exchange_shaped_csv(client, session):
    _single(session, "sku-1", "Abra #54", number="54")
    resp = client.post("/ebay-export/build", data={"sku": ["sku-1"]})
    assert resp.status_code == 200
    assert "attachment; filename=ebay_bulk_listing_ready.csv" in resp.headers["content-disposition"]
    body = resp.content.decode("utf-8-sig")
    assert body.startswith("Info,Version")
    rows = body.split("\r\n")
    assert rows[1] == ",".join(_TEMPLATE_HEADER)


def test_a_bulk_lot_sku_is_never_exported_even_if_submitted(client, session):
    """Bulk Master lots have no verified eBay condition ID for their
    category (see the module docstring) -- the page never gives them a
    checkbox, and submitting one's id by hand still must not produce a row,
    since that row would carry the REPLACE_ME placeholder condition ID."""
    session.add(CardSet(id="sv3", name="Obsidian Flames"))
    lot = BulkLot(id="lot-1", title="Obsidian Flames — bulk lot (30 cards)", set_id="sv3", card_count=30,
                 price=Decimal("20"), image_url="https://example.test/logo.png", status=ShopListingStatus.ACTIVE)
    session.add(lot)
    session.commit()

    resp = client.post("/ebay-export/build", data={"sku": [lot.id]}, follow_redirects=False)
    # Nothing survives selection (the lot was never a candidate row), so
    # this is the same "nothing exportable" outcome as selecting nothing.
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
    from automation_control.ui import ADMIN_NAV

    hrefs = [href for _, links, *_ in ADMIN_NAV for href, _ in links]
    assert "/ebay-export" in hrefs


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
