"""The settings page: what we're connected to, and what we must never show.

The secrecy tests are the important ones. This page reports on every
credential the app holds, so the rule that it renders none of them needs
to be enforced by something other than care.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.config import Settings
from automation_control.database import Base, get_session
from automation_control.ebay_oauth import TokenCipher, store_user_tokens

SECRET = "sk-ant-super-secret-value-9876543210"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    original = app.state.settings
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "nic"
    yield TestClient(app)
    app.dependency_overrides.clear()
    app.state.settings = original


def _settings(**overrides) -> Settings:
    base = dict(ebay_env="production", ebay_client_id="cid", ebay_client_secret="cs",
                ebay_runame="rn", ebay_token_encryption_key="k",
                app_public_base_url="https://ezbay.example",
                ebay_payment_policy_id="p", ebay_fulfillment_policy_id="f",
                ebay_return_policy_id="r", ebay_merchant_location_key="loc")
    base.update(overrides)
    return Settings(**base)


def _connect(session, environment: str, key="k"):
    from cryptography.fernet import Fernet

    cipher = TokenCipher(Fernet.generate_key().decode())
    store_user_tokens(session, cipher,
                      {"access_token": "a", "refresh_token": "r", "expires_in": 7200},
                      environment=environment)


def test_the_page_renders(client):
    app.state.settings = _settings()
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Settings" in resp.text


def test_release_identity_is_visible(client, monkeypatch):
    import automation_control.settings_page as mod

    monkeypatch.setattr(mod, "application_revision", lambda: ("abc123def456", True))
    monkeypatch.setattr(mod, "database_revision", lambda session: None)
    app.state.settings = _settings()

    page = client.get("/settings").text

    assert "Application revision" in page
    assert "abc123def456 + local changes" in page
    assert "Database revision" in page
    assert "not baselined" in page


def test_no_credential_value_is_ever_rendered(client):
    """Not even masked: a truncated key still leaks its length and prefix,
    and nothing this page answers needs the value."""
    app.state.settings = _settings(anthropic_api_key=SECRET, apify_token=SECRET,
                                   pokemonpricetracker_api_key=SECRET,
                                   ebay_client_secret=SECRET, stripe_secret_key=SECRET)

    page = client.get("/settings").text

    assert SECRET not in page
    for fragment in (SECRET[:8], SECRET[-8:]):
        assert fragment not in page
    # ...while still saying they are present.
    assert page.count("configured") >= 4


def test_a_missing_credential_says_what_stops_working(client):
    app.state.settings = _settings(anthropic_api_key=None)
    page = client.get("/settings").text
    assert "cannot identify anything" in page


def test_market_discovery_integrations_show_configuration_without_secrets(client):
    app.state.settings = _settings(
        brave_search_api_key=SECRET,
        brave_search_storage_permitted=True,
        market_alert_imap_host="imap.example.test",
        market_alert_imap_username="alerts@example.test",
        market_alert_imap_password=SECRET,
    )

    page = client.get("/settings").text

    assert "Brave public-web discovery" in page
    assert "configured with storage permission" in page
    assert "Marketplace alert mailbox" in page
    assert SECRET not in page


def test_an_environment_mismatch_is_called_out_with_a_reconnect(client, session):
    """The failure this page exists for: a sandbox token against production
    fails as an ordinary auth error, so nothing else tells you which eBay
    you are attached to."""
    app.state.settings = _settings(ebay_env="production")
    _connect(session, "sandbox")

    page = client.get("/settings").text

    assert "connected to sandbox" in page
    assert "configured for production" in page
    assert "Reconnect to production" in page
    assert "/auth/ebay/start" in page


def test_a_matching_connection_is_reported_healthy(client, session):
    app.state.settings = _settings(ebay_env="production")
    _connect(session, "production")

    page = client.get("/settings").text

    assert "connected to production" in page
    assert "configured for production" not in page  # no mismatch warning
    assert "Reconnect eBay" in page


def test_never_connected_offers_connect_not_reconnect(client):
    app.state.settings = _settings()
    page = client.get("/settings").text
    assert "not connected" in page
    assert "Connect eBay" in page


def test_missing_listing_policies_are_named(client, session):
    """"Connected" and "able to publish" are different questions with
    different fixes."""
    app.state.settings = _settings(ebay_return_policy_id=None, ebay_merchant_location_key=None)
    _connect(session, "production")

    page = client.get("/settings").text

    assert "missing returns policy, merchant location" in page
    assert "publishing stays blocked" in page


def test_settings_is_reachable_from_the_nav(client):
    from automation_control.ui import ADMIN_NAV

    hrefs = [href for _, links, _ in ADMIN_NAV for href, _ in links]
    assert "/settings" in hrefs
