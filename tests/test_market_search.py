from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.adapters.ebay import EbaySandboxReadAdapter
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


@pytest.mark.asyncio
async def test_ebay_adapter_sends_official_item_location_filter():
    adapter = EbaySandboxReadAdapter("token", environment="production")
    adapter._get = AsyncMock(return_value={"itemSummaries": []})

    await adapter.search_market("Sonny Angel", marketplace="EBAY_AU", item_location_country="JP")

    assert adapter._get.await_args.kwargs["params"]["filter"] == "itemLocationCountry:JP"
    assert adapter._get.await_args.kwargs["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_AU"


def test_blank_query_shows_a_prompt_not_a_search(client):
    resp = client.get("/market-search")
    assert resp.status_code == 200
    assert "Search a card, figure, or set name" in resp.text


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
    session.add(CardPrice(catalog_item_id="base1-4", variant=CardVariant.NORMAL, source="test", price=250, currency="AUD"))
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


def test_location_selector_defaults_to_australia_and_offers_japan(client):
    resp = client.get("/market-search")
    assert "<option value='AU' selected>Australia</option>" in resp.text
    assert "<option value='JP'>Japan</option>" in resp.text
    assert "International (US)" not in resp.text


def test_choosing_japan_is_passed_as_an_item_location_filter(client, configured_settings):
    with patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(return_value={"access_token": "app-token"})), \
         patch("automation_control.market_search.EbaySandboxReadAdapter.search_market", new=AsyncMock(return_value={"itemSummaries": []})) as mocked:
        resp = client.get("/market-search?q=charizard&country=JP")

    assert resp.status_code == 200
    assert "<option value='JP' selected>Japan</option>" in resp.text
    mocked.assert_awaited_once_with("charizard", marketplace="EBAY_AU", item_location_country="JP")


def test_an_unknown_country_falls_back_to_the_default(client):
    resp = client.get("/market-search?country=US")
    assert "<option value='AU' selected>Australia</option>" in resp.text


def test_json_market_search_is_also_restricted_to_requested_allowed_country(client, configured_settings):
    with patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(return_value={"access_token": "app-token"})), \
         patch("automation_control.api.EbaySandboxReadAdapter.search_market", new=AsyncMock(return_value={
             "itemSummaries": [
                 {"itemId": "jp-1", "title": "Japan item", "price": {"value": "1000", "currency": "JPY"}, "itemLocation": {"country": "JP"}},
                 {"itemId": "us-1", "title": "Unexpected item", "price": {"value": "10", "currency": "USD"}, "itemLocation": {"country": "US"}},
             ]
         })) as mocked:
        resp = client.get("/api/ebay/market-search?q=Sonny+Angel&country=JP")

    assert resp.status_code == 200
    assert resp.json()["country"] == "JP"
    assert [item["ebay_item_id"] for item in resp.json()["items"]] == ["jp-1"]
    mocked.assert_awaited_once_with("Sonny Angel", marketplace="EBAY_AU", item_location_country="JP")


def test_json_market_search_rejects_country_outside_australia_and_japan(client):
    resp = client.get("/api/ebay/market-search?q=Sonny+Angel&country=US")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Seller location must be Australia or Japan"


def test_pasting_a_link_uses_its_page_title_as_the_search(client, configured_settings):
    html = "<html><head><meta property='og:title' content='Sonny Angel Animal Series 1 Monkey'></head></html>"
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.text = html

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)), \
         patch("automation_control.ebay_oauth.EbayOAuthClient.application_token", new=AsyncMock(return_value={"access_token": "app-token"})), \
         patch("automation_control.market_search.EbaySandboxReadAdapter.search_market", new=AsyncMock(return_value={"itemSummaries": []})) as mocked:
        resp = client.get("/market-search?url=https://example.test/itm/123")

    assert resp.status_code == 200
    assert mocked.await_args.args[0] == "Sonny Angel Animal Series 1 Monkey"


def test_a_link_with_no_readable_title_shows_a_clear_message(client, configured_settings):
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.text = "<html><head></head><body>no title here</body></html>"

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        resp = client.get("/market-search?url=https://example.test/itm/123")

    assert resp.status_code == 200
    assert "Couldn't read a title" in resp.text


@pytest.fixture
def apify_token():
    original = app.state.settings.apify_token
    yield
    app.state.settings.apify_token = original


def test_screenshot_search_is_a_clear_503_when_apify_is_not_configured(client, apify_token):
    app.state.settings.apify_token = None
    resp = client.post("/market-search/from-photo", files={"photo": ("shot.jpg", b"fake-bytes", "image/jpeg")})
    assert resp.status_code == 200
    assert "isn't configured" in resp.text


def test_screenshot_search_shows_lens_results_as_pickable_links_not_an_auto_search(client, apify_token):
    app.state.settings.apify_token = "fake-token"
    fake_items = [{"title": "Sonny Angel Animal Series 1 Monkey", "link": "https://example.test/a"}]

    with patch("automation_control.market_search.search_image", new=AsyncMock(return_value=fake_items)):
        resp = client.post("/market-search/from-photo", files={"photo": ("shot.jpg", b"fake-bytes", "image/jpeg")})

    assert resp.status_code == 200
    assert "Sonny Angel Animal Series 1 Monkey" in resp.text
    assert "href='/market-search?q=Sonny%20Angel%20Animal%20Series%201%20Monkey&country=AU'" in resp.text


def test_screenshot_search_failure_shows_the_real_error(client, apify_token):
    app.state.settings.apify_token = "fake-token"
    from automation_control.reverse_image_search import ReverseImageSearchError

    with patch("automation_control.market_search.search_image", new=AsyncMock(side_effect=ReverseImageSearchError("Apify run failed (402): insufficient balance"))):
        resp = client.post("/market-search/from-photo", files={"photo": ("shot.jpg", b"fake-bytes", "image/jpeg")})

    assert resp.status_code == 200
    assert "402" in resp.text
