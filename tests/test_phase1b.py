from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.orm import Session

from automation_control.adapters.ebay import EbayApiError, EbaySandboxReadAdapter
from automation_control.api import ebay_callback
from automation_control.audit import redact
from automation_control.auth import create_session, hash_password, require_dashboard_user, verify_password, verify_session
from automation_control.config import Settings
from automation_control.database import get_session
from automation_control.ebay_oauth import AUTH_URL, IDENTITY_READ_SCOPE, INVENTORY_READ_SCOPE, SELLER_SCOPE, EbayOAuthClient, OAuthError, TokenCipher, authorization_url, consume_oauth_state, new_oauth_state, store_user_tokens, valid_sandbox_runame, valid_user_access_token


def test_oauth_state_validation_is_single_use_and_rejects_missing(session):
    state = new_oauth_state(session)
    assert consume_oauth_state(session, state)
    assert not consume_oauth_state(session, state)
    assert not consume_oauth_state(session, None)


def test_expired_oauth_state_is_rejected(session):
    now = datetime.now(UTC)
    state = new_oauth_state(session, now=now - timedelta(minutes=11))
    assert not consume_oauth_state(session, state, now=now)


async def test_callback_failure_and_unauthorized_dashboard(session):
    assert not consume_oauth_state(session, None)
    class Request:
        cookies = {}
        class app:
            state = type("State", (), {"settings": Settings()})()
    try:
        require_dashboard_user(Request())
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "token" not in str(exc.detail).lower()
    else:
        raise AssertionError("dashboard access was not rejected")
    try:
        await ebay_callback(Request(), state=None, code=None, error=None, session=session)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "code" not in str(exc.detail).lower()
    else:
        raise AssertionError("invalid callback was not rejected")


def test_dashboard_password_and_session_validation():
    encoded = hash_password("correct horse")
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong", encoded)
    cookie = create_session("operator", "signing-key", now=1000)
    assert verify_session(cookie, "signing-key", now=1001) == "operator"
    assert verify_session(cookie, "wrong-key", now=1001) is None


def test_sandbox_authorization_request_uses_runame_and_read_only_scopes():
    target = authorization_url("example-SBX-client", "example-SBX-runame", "opaque-state")
    parsed = urlsplit(target)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTH_URL
    assert query == {
        "client_id": ["example-SBX-client"],
        "redirect_uri": ["example-SBX-runame"],
        "response_type": ["code"],
        "scope": [f"{INVENTORY_READ_SCOPE} {IDENTITY_READ_SCOPE}"],
        "state": ["opaque-state"],
    }


def test_sandbox_authorization_request_encodes_scope_spaces_as_percent20():
    target = authorization_url("example-SBX-client", "example-SBX-runame", "opaque-state")
    scope_segment = target.split("scope=")[1].split("&")[0]
    assert "%20" in scope_segment
    assert "+" not in scope_segment


def test_sandbox_runame_rejects_callback_url_and_production_marker():
    assert valid_sandbox_runame("example-SBX-runame")
    assert not valid_sandbox_runame("https://example.test/auth/ebay/callback")
    assert not valid_sandbox_runame("example-PRD-runame")
    for invalid in ("https://example.test/callback", "example-PRD-runame"):
        try:
            authorization_url("example-SBX-client", invalid, "opaque-state")
        except OAuthError:
            pass
        else:
            raise AssertionError("invalid RuName was accepted")


def test_token_and_secret_redaction():
    redacted = redact({"access_token": "a", "refresh_token": "b", "client_secret": "c", "authorization_code": "d", "nested": {"password": "e"}})
    assert redacted == {"access_token": "[REDACTED]", "refresh_token": "[REDACTED]", "client_secret": "[REDACTED]", "authorization_code": "[REDACTED]", "nested": {"password": "[REDACTED]"}}


async def test_expired_token_is_refreshed_and_stored(session):
    cipher = TokenCipher(Fernet.generate_key().decode())
    store_user_tokens(session, cipher, {"access_token": "old", "refresh_token": "refresh", "expires_in": -1, "scope": SELLER_SCOPE})

    class FakeOAuth:
        called = False
        async def refresh(self, refresh_token):
            self.called = True
            assert refresh_token == "refresh"
            return {"access_token": "new", "expires_in": 7200, "scope": SELLER_SCOPE}

    oauth = FakeOAuth()
    assert await valid_user_access_token(session, cipher, oauth) == "new"
    assert oauth.called


async def test_unexpired_token_does_not_refresh(session):
    cipher = TokenCipher(Fernet.generate_key().decode())
    store_user_tokens(session, cipher, {"access_token": "current", "refresh_token": "refresh", "expires_in": 7200})
    class NoRefresh:
        async def refresh(self, refresh_token):
            raise AssertionError("refresh should not run")
    assert await valid_user_access_token(session, cipher, NoRefresh()) == "current"


async def test_ebay_api_failure_is_sanitized():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(500, json={"error": "upstream leaked detail"})
    adapter = EbaySandboxReadAdapter("sensitive-token", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        await adapter.get_listings()
    except EbayApiError as exc:
        assert "sensitive-token" not in str(exc)
        assert "upstream leaked detail" not in str(exc)
    else:
        raise AssertionError("expected API failure")


def test_read_only_policy_enforcement():
    adapter = EbaySandboxReadAdapter("token")
    assert "sell.inventory.readonly" in SELLER_SCOPE
    assert "commerce.identity.readonly" in SELLER_SCOPE
    for method in ("create_listing", "update_listing", "update_price", "update_inventory", "create_offer", "get_orders"):
        assert not hasattr(adapter, method)
