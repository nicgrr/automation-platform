import io
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedListingItem
from automation_control.database import Base, get_session
from automation_control.models import (
    BuyThresholdConfig, CardPrice, CardVariant, CatalogItem, CatalogItemType, TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BuyThresholdConfig(label="Default", green_max_pct=Decimal("55"), yellow_max_pct=Decimal("70"), is_default=True))
        db.commit()
        yield db


@pytest.fixture
def client(session):
    original_api_key = app.state.settings.anthropic_api_key
    app.state.settings.anthropic_api_key = "test-key"
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings.anthropic_api_key = original_api_key


def _seed_priced_pokemon_card(session, catalog_item_id="pikachu-58", price=100):
    session.add(CatalogItem(id=catalog_item_id, item_type=CatalogItemType.TCG_CARD, name="Pikachu"))
    session.add(TcgCard(catalog_item_id=catalog_item_id, game="pokemon", set_code="BASE", number="58"))
    session.add(CardPrice(card_id=catalog_item_id, variant=CardVariant.NORMAL, source="test", price=price, currency="AUD"))
    session.commit()


def _seed_unpriced_one_piece_card(session, catalog_item_id="op17-062"):
    session.add(CatalogItem(id=catalog_item_id, item_type=CatalogItemType.TCG_CARD, name="Kaido"))
    session.add(TcgCard(catalog_item_id=catalog_item_id, game="one_piece", set_code="OP17", number="062"))
    session.commit()


def test_page_requires_login():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/deal-check")
    assert resp.status_code == 401


def test_extract_requires_login():
    app.dependency_overrides.clear()
    resp = TestClient(app).post("/deal-check/extract", data={"text": "hi"})
    assert resp.status_code == 401


def test_extract_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/deal-check/extract", data={"text": "hi"})
    assert resp.status_code == 503


def test_extract_with_neither_text_nor_photo_is_rejected(client):
    resp = client.post("/deal-check/extract", data={"text": ""})
    assert resp.status_code == 400


def test_matched_priced_card_gets_a_verdict(client, session):
    _seed_priced_pokemon_card(session, price=100)
    fake = ExtractedListingItem(description="Pikachu", set_code="BASE", card_number="58", condition="NM", asking_price=55.0, unreadable_fields=[])
    with patch("automation_control.deal_check.extract_listing_items", return_value=[fake]):
        resp = client.post("/deal-check/extract", data={"text": "Pikachu BASE-58 NM $55 ONO"})

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["matched"] is True
    assert results[0]["market_value"] == pytest.approx(100.0)
    assert results[0]["verdict"] == "GREEN"  # 55/100 = 55% <= green threshold


def test_matched_unpriced_card_has_no_market_value(client, session):
    _seed_unpriced_one_piece_card(session)
    fake = ExtractedListingItem(description="Kaido", set_code="OP17", card_number="062", condition="NM", asking_price=30.0, unreadable_fields=[])
    with patch("automation_control.deal_check.extract_listing_items", return_value=[fake]):
        resp = client.post("/deal-check/extract", data={"text": "Kaido OP17-062 NM $30"})

    results = resp.json()["results"]
    assert results[0]["matched"] is True
    assert results[0]["market_value"] is None
    assert results[0]["verdict"] == "UNKNOWN"


def test_unmatched_item_is_still_returned(client):
    fake = ExtractedListingItem(description="Some Rare Thing", set_code="XX99", card_number="999", condition="NM", asking_price=20.0, unreadable_fields=[])
    with patch("automation_control.deal_check.extract_listing_items", return_value=[fake]):
        resp = client.post("/deal-check/extract", data={"text": "Some Rare Thing XX99-999 $20"})

    results = resp.json()["results"]
    assert results[0]["matched"] is False
    assert results[0]["market_value"] is None


def test_missing_set_code_falls_back_to_no_match_not_an_error(client):
    fake = ExtractedListingItem(description="Promo card, no code visible", set_code="", card_number="", condition="unknown", asking_price=10.0, unreadable_fields=["set_code", "card_number"])
    with patch("automation_control.deal_check.extract_listing_items", return_value=[fake]):
        resp = client.post("/deal-check/extract", data={"text": "Promo card for $10"})

    assert resp.status_code == 200
    assert resp.json()["results"][0]["matched"] is False


def test_multiple_items_in_one_listing_each_get_their_own_result(client, session):
    _seed_priced_pokemon_card(session, price=100)
    items = [
        ExtractedListingItem(description="Pikachu", set_code="BASE", card_number="58", condition="NM", asking_price=55.0, unreadable_fields=[]),
        ExtractedListingItem(description="Unknown Card", set_code="ZZ01", card_number="1", condition="LP", asking_price=5.0, unreadable_fields=[]),
    ]
    with patch("automation_control.deal_check.extract_listing_items", return_value=items):
        resp = client.post("/deal-check/extract", data={"text": "two cards here"})

    results = resp.json()["results"]
    assert len(results) == 2


def test_recognition_failure_returns_502(client):
    from automation_control.card_recognition import RecognitionError

    with patch("automation_control.deal_check.extract_listing_items", side_effect=RecognitionError("boom")):
        resp = client.post("/deal-check/extract", data={"text": "garbage"})
    assert resp.status_code == 502


def test_photo_only_submission_is_accepted(client):
    fake = ExtractedListingItem(description="Pikachu", set_code="BASE", card_number="58", condition="NM", asking_price=55.0, unreadable_fields=[])
    with patch("automation_control.deal_check.extract_listing_items", return_value=[fake]) as mock_extract:
        resp = client.post(
            "/deal-check/extract",
            data={"text": ""},
            files={"photo": ("listing.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")},
        )
    assert resp.status_code == 200
    assert mock_extract.call_args.kwargs["photo_path"] is not None


def test_page_renders_the_form(client):
    resp = client.get("/deal-check")
    assert resp.status_code == 200
    assert "deal-check-form" in resp.text
    assert "name='text'" in resp.text
    assert "name='photo'" in resp.text
