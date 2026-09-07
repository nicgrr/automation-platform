from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def configured_settings():
    settings = app.state.settings
    original = {
        "ebay_client_id": settings.ebay_client_id, "ebay_client_secret": settings.ebay_client_secret,
        "ebay_runame": settings.ebay_runame, "ebay_token_encryption_key": settings.ebay_token_encryption_key,
        "app_public_base_url": settings.app_public_base_url,
    }
    settings.ebay_client_id = "example-SBX-client"
    settings.ebay_client_secret = "shh"
    settings.ebay_runame = "example-SBX-runame"
    settings.ebay_token_encryption_key = Fernet.generate_key().decode()
    settings.app_public_base_url = "https://example.test"
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_market_search_page_requires_login():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/market-search")
    assert resp.status_code == 401


def test_blank_query_shows_a_prompt_not_a_search(client):
    resp = client.get("/market-search")
    assert resp.status_code == 200
    assert "Search a card or set name" in resp.text


def test_unconfigured_ebay_shows_a_clear_message(client):
    resp = client.get("/market-search?q=charizard")
    assert resp.status_code == 200
    assert "isn't configured" in resp.text


def test_configured_search_shows_results_and_sandbox_note(client, configured_settings):
    with patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(return_value={"access_token": "app-token"})), \
         patch("automation_control.market_search.EbaySandboxReadAdapter.search_market", new=AsyncMock(return_value={
             "itemSummaries": [{"itemId": "1", "title": "Charizard ex 199", "price": {"value": "12.50", "currency": "AUD"}, "itemLocation": {"country": "AU"}}]
         })):
        resp = client.get("/market-search?q=charizard")

    assert resp.status_code == 200
    assert "Charizard ex 199" in resp.text
    assert "$12.50" in resp.text
    assert "Sandbox mode" in resp.text


def test_search_shows_your_own_catalog_price_alongside_ebay_results(client, session, configured_settings):
    session.add(CardSet(id="base1", name="Base Set"))
    session.add(CatalogCard(id="base1-4", set_id="base1", number="4", name="Charizard"))
    session.add(CardPrice(card_id="base1-4", variant=CardVariant.NORMAL, source="test", price=250, currency="AUD"))
    session.commit()

    with patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(return_value={"access_token": "app-token"})), \
         patch("automation_control.market_search.EbaySandboxReadAdapter.search_market", new=AsyncMock(return_value={"itemSummaries": []})):
        resp = client.get("/market-search?q=Charizard")

    assert "Charizard" in resp.text
    assert "$250.00" in resp.text


def test_search_failure_is_handled_gracefully(client, configured_settings):
    from automation_control.ebay_oauth import OAuthError

    with patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(side_effect=OAuthError("token request failed"))):
        resp = client.get("/market-search?q=charizard")
    assert resp.status_code == 200
    assert "failed" in resp.text.lower()
