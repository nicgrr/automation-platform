import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCard
from automation_control.database import Base, get_session
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
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


def _upload():
    return {"photo": ("card.jpg", io.BytesIO(b"fake-bytes"), "image/jpeg")}


def _seed_pikachu(session):
    session.add(CardSet(id="base1", name="Base Set"))
    session.add(CatalogCard(id="base1-58", set_id="base1", number="58", name="Pikachu"))
    session.add(CardPrice(card_id="base1-58", variant=CardVariant.NORMAL, source="test", price=12.50, currency="AUD"))
    session.commit()


def test_identify_without_api_key_returns_503(client):
    app.state.settings.anthropic_api_key = None
    resp = client.post("/quick-price/identify", files=_upload())
    assert resp.status_code == 503


def test_identify_requires_login():
    app.dependency_overrides.clear()
    resp = TestClient(app).post("/quick-price/identify", files=_upload())
    assert resp.status_code == 401


def test_matched_card_returns_its_market_value(client, session):
    _seed_pikachu(session)
    fake = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    with patch("automation_control.quick_price.extract_card_details", return_value=[fake]):
        resp = client.post("/quick-price/identify", files=_upload())

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["matched"] is True
    assert results[0]["catalog_item_id"] == "base1-58"
    assert results[0]["market_value"] == pytest.approx(12.50)


def test_unmatched_card_returns_no_price_but_still_a_row(client, session):
    fake = ExtractedCard(character="Some Unknown Card", set_name="Unknown Set", card_number="999/999", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    with patch("automation_control.quick_price.extract_card_details", return_value=[fake]):
        resp = client.post("/quick-price/identify", files=_upload())

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["matched"] is False
    assert results[0]["market_value"] is None


def test_no_cards_detected_returns_empty_results(client):
    with patch("automation_control.quick_price.extract_card_details", return_value=[]):
        resp = client.post("/quick-price/identify", files=_upload())
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_recognition_failure_returns_502_not_a_crash(client):
    with patch("automation_control.quick_price.extract_card_details", side_effect=RuntimeError("boom")):
        resp = client.post("/quick-price/identify", files=_upload())
    assert resp.status_code == 502


def test_multiple_cards_in_one_photo_each_get_their_own_result(client, session):
    _seed_pikachu(session)
    session.add(CatalogCard(id="base1-4", set_id="base1", number="4", name="Charizard"))
    session.add(CardPrice(card_id="base1-4", variant=CardVariant.NORMAL, source="test", price=250, currency="AUD"))
    session.commit()

    cards = [
        ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[]),
        ExtractedCard(character="Charizard", set_name="Base Set", card_number="4/102", rarity="Holo Rare", language="English", graded="Raw", unreadable_fields=[]),
    ]
    with patch("automation_control.quick_price.extract_card_details", return_value=cards):
        resp = client.post("/quick-price/identify", files=_upload())

    results = resp.json()["results"]
    assert len(results) == 2
    by_name = {r["character"]: r for r in results}
    assert by_name["Pikachu"]["market_value"] == pytest.approx(12.50)
    assert by_name["Charizard"]["market_value"] == pytest.approx(250.0)


def test_card_number_with_unreadable_field_falls_back_to_name_only_match(client, session):
    _seed_pikachu(session)
    fake = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="", rarity="Common", language="English", graded="Raw", unreadable_fields=["card_number"])
    with patch("automation_control.quick_price.extract_card_details", return_value=[fake]):
        resp = client.post("/quick-price/identify", files=_upload())

    results = resp.json()["results"]
    assert results[0]["matched"] is True
    assert results[0]["market_value"] == pytest.approx(12.50)
