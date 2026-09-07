from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCollectibleListing
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice, CatalogItem, CatalogItemType, CollectibleProduct,
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


def seed_series(session, series="Fruit Series", secret=True, median="421.09", low="60.60"):
    item_id = f"i-{series}-{secret}"
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.COLLECTIBLE,
                            name=f"{series} {'secret' if secret else 'blind box'}"))
    session.add(CollectibleProduct(catalog_item_id=item_id, brand="Sonny Angel",
                                   series=series, is_secret=secret))
    for source, value in (("ebay_active_median", median), ("ebay_active_low", low)):
        session.add(CardPrice(card_id=item_id, variant=None, source=source,
                              price=Decimal(value), currency="AUD"))
    session.commit()
    return item_id


def listing(**kw):
    values = dict(brand="Sonny Angel", series="Fruit Series", character="Peach",
                  variant="", is_secret=True, quantity=1, is_sealed=False,
                  asking_price=90.0, currency="AUD", unreadable_fields=[])
    values.update(kw)
    return ExtractedCollectibleListing(**values)


def wire(monkeypatch, extracted, html="<h1>Sonny Angel</h1>"):
    monkeypatch.setattr("automation_control.collectible_link.fetch_text", lambda url: html)
    monkeypatch.setattr("automation_control.collectible_link.extract_collectible_listing",
                        lambda text, api_key, **k: extracted)
    settings = __import__("automation_control.config", fromlist=["get_settings"]).get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr("automation_control.collectible_link.get_settings", lambda: settings)


def test_the_form_renders(client):
    body = client.get("/collectibles/identify").text
    assert "Paste a listing link" in body
    assert "name='url'" in body


def test_a_cheap_listing_reads_as_worth_a_look(client, session, monkeypatch):
    seed_series(session)
    wire(monkeypatch, [listing(asking_price=90.0)])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Worth a look" in body
    assert "Fruit Series" in body


def test_an_expensive_listing_reads_as_over_market(client, session, monkeypatch):
    seed_series(session)
    wire(monkeypatch, [listing(asking_price=600.0)])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Over market" in body


def test_a_listing_near_the_median_is_not_called_a_bargain(client, session, monkeypatch):
    seed_series(session)
    wire(monkeypatch, [listing(asking_price=400.0)])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Around market" in body
    assert "Worth a look" not in body


def test_an_unclear_secret_is_flagged_rather_than_assumed(client, session, monkeypatch):
    """Secret-or-not is a 7x price difference. A listing that doesn't say
    must not be silently treated as either."""
    seed_series(session)
    wire(monkeypatch, [listing(unreadable_fields=["is_secret"])])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Secret unclear" in body
    assert "worth asking before buying" in body


def test_a_multi_figure_lot_is_priced_per_figure(client, session, monkeypatch):
    seed_series(session)
    # $600 for 10 is $60 each -- well under the $421 median
    wire(monkeypatch, [listing(asking_price=600.0, quantity=10)])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Worth a look" in body
    assert "$60.00" in body


def test_a_listing_with_no_catalogue_match_says_so(client, session, monkeypatch):
    wire(monkeypatch, [listing(series="Nonexistent Series")])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "not in the catalogue" in body
    assert "No comparison" in body


def test_a_secret_listing_matches_the_secret_row_not_the_blind_box(client, session, monkeypatch):
    seed_series(session, secret=False, median="40.44", low="11.99")
    seed_series(session, secret=True, median="421.09", low="60.60")
    wire(monkeypatch, [listing(asking_price=90.0, is_secret=True)])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "Fruit Series secret" in body
    assert "$421.09" in body


def test_an_unsafe_url_is_reported_without_fetching(client, session, monkeypatch):
    from automation_control.link_fetch import UnsafeUrl

    def refuse(url):
        raise UnsafeUrl("that link points somewhere internal, so it wasn't fetched")

    monkeypatch.setattr("automation_control.collectible_link.fetch_text", refuse)
    body = client.post("/collectibles/identify", data={"url": "http://127.0.0.1:8080/"}).text
    assert "somewhere internal" in body


def test_a_page_that_is_not_a_listing_is_reported(client, session, monkeypatch):
    wire(monkeypatch, [])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/x"}).text
    assert "doesn't look like a listing" in body


def test_results_label_ebay_as_asking_prices(client, session, monkeypatch):
    seed_series(session)
    wire(monkeypatch, [listing()])
    body = client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"}).text
    assert "not completed sales" in body


def test_the_page_never_writes_to_the_catalogue(client, session, monkeypatch):
    from sqlalchemy import func, select

    before = session.scalar(select(func.count()).select_from(CatalogItem))
    wire(monkeypatch, [listing(series="Brand New Series")])
    client.post("/collectibles/identify", data={"url": "https://example.com/itm/1"})
    assert session.scalar(select(func.count()).select_from(CatalogItem)) == before
