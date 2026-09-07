import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .models import EbayCredential, OAuthState

INVENTORY_READ_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly"
IDENTITY_READ_SCOPE = "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly"
SELLER_SCOPE = f"{INVENTORY_READ_SCOPE} {IDENTITY_READ_SCOPE}"
MARKET_SCOPE = "https://api.ebay.com/oauth/api_scope"
AUTH_URL = "https://auth.sandbox.ebay.com/oauth2/authorize"
TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
AUTH_URL_PRODUCTION = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL_PRODUCTION = "https://api.ebay.com/identity/v1/oauth2/token"
_AUTH_URLS = {"sandbox": AUTH_URL, "production": AUTH_URL_PRODUCTION}
_TOKEN_URLS = {"sandbox": TOKEN_URL, "production": TOKEN_URL_PRODUCTION}
_PRODUCTION_MARKER = re.compile(r"(?:^|[-_])PRD(?:[-_]|$)", re.IGNORECASE)
_SANDBOX_MARKER = re.compile(r"(?:^|[-_])SBX(?:[-_]|$)", re.IGNORECASE)


class OAuthError(Exception):
    pass


def valid_sandbox_runame(value: str | None) -> bool:
    """Reject callback URLs, placeholders, and obvious Production RuNames."""
    if not value or value.startswith("REPLACE_WITH_") or any(char.isspace() for char in value):
        return False
    parsed = urlsplit(value)
    return not parsed.scheme and not parsed.netloc and not _PRODUCTION_MARKER.search(value)


def valid_sandbox_client_id(value: str | None) -> bool:
    if not value or value.startswith("REPLACE_WITH_"):
        return False
    return not _PRODUCTION_MARKER.search(value)


def valid_production_runame(value: str | None) -> bool:
    """Mirror of valid_sandbox_runame for ebay_env=production -- rejects
    callback URLs, placeholders, and obvious Sandbox RuNames (the opposite
    mistake to the one valid_sandbox_runame guards against)."""
    if not value or value.startswith("REPLACE_WITH_") or any(char.isspace() for char in value):
        return False
    parsed = urlsplit(value)
    return not parsed.scheme and not parsed.netloc and not _SANDBOX_MARKER.search(value)


def valid_production_client_id(value: str | None) -> bool:
    if not value or value.startswith("REPLACE_WITH_"):
        return False
    return not _SANDBOX_MARKER.search(value)


class TokenCipher:
    def __init__(self, key: str):
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise OAuthError("invalid token-encryption configuration") from exc

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def decrypt(self, value: bytes) -> str:
        try:
            return self._fernet.decrypt(value).decode()
        except InvalidToken as exc:
            raise OAuthError("stored credential cannot be decrypted") from exc


def new_oauth_state(session: Session, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    value = secrets.token_urlsafe(32)
    session.add(OAuthState(state_hash=hashlib.sha256(value.encode()).hexdigest(), expires_at=now + timedelta(minutes=10)))
    session.commit()
    return value


def consume_oauth_state(session: Session, value: str | None, *, now: datetime | None = None) -> bool:
    if not value:
        return False
    now = now or datetime.now(UTC)
    record = session.get(OAuthState, hashlib.sha256(value.encode()).hexdigest())
    if not record or record.consumed_at is not None:
        return False
    expires = record.expires_at.replace(tzinfo=UTC) if record.expires_at.tzinfo is None else record.expires_at
    if expires <= now:
        return False
    record.consumed_at = now
    session.commit()
    return True


def authorization_url(client_id: str, runame: str, state: str, *, environment: str = "sandbox") -> str:
    validate_client_id = valid_production_client_id if environment == "production" else valid_sandbox_client_id
    validate_runame = valid_production_runame if environment == "production" else valid_sandbox_runame
    label = environment.title()
    if not validate_client_id(client_id):
        raise OAuthError(f"invalid {label} Client ID configuration")
    if not validate_runame(runame):
        raise OAuthError(f"invalid {label} RuName configuration")
    if not state:
        raise OAuthError("missing OAuth state")
    return _AUTH_URLS[environment] + "?" + urlencode({"client_id": client_id, "redirect_uri": runame, "response_type": "code", "scope": SELLER_SCOPE, "state": state}, quote_via=quote)


class EbayOAuthClient:
    def __init__(self, client_id: str, client_secret: str, *, environment: str = "sandbox", client: httpx.AsyncClient | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.environment = environment
        self.token_url = _TOKEN_URLS[environment]
        self.client = client or httpx.AsyncClient(timeout=15.0)

    def _basic(self) -> str:
        raw = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        return f"Basic {raw}"

    async def exchange_code(self, code: str, runame: str) -> dict:
        response = await self.client.post(self.token_url, data={"grant_type": "authorization_code", "code": code, "redirect_uri": runame}, headers={"Authorization": self._basic(), "Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code >= 400:
            raise OAuthError(f"eBay token exchange failed ({response.status_code})")
        return response.json()

    async def refresh(self, refresh_token: str) -> dict:
        response = await self.client.post(self.token_url, data={"grant_type": "refresh_token", "refresh_token": refresh_token, "scope": SELLER_SCOPE}, headers={"Authorization": self._basic(), "Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code >= 400:
            raise OAuthError(f"eBay token refresh failed ({response.status_code})")
        return response.json()

    async def application_token(self) -> dict:
        response = await self.client.post(self.token_url, data={"grant_type": "client_credentials", "scope": MARKET_SCOPE}, headers={"Authorization": self._basic(), "Content-Type": "application/x-www-form-urlencoded"})
        if response.status_code >= 400:
            raise OAuthError(f"eBay application-token request failed ({response.status_code})")
        return response.json()


def store_user_tokens(session: Session, cipher: TokenCipher, payload: dict, *, environment: str = "sandbox", now: datetime | None = None) -> EbayCredential:
    now = now or datetime.now(UTC)
    refresh = payload.get("refresh_token")
    if not payload.get("access_token") or not refresh:
        raise OAuthError("eBay token response was incomplete")
    record = session.get(EbayCredential, 1)
    values = dict(environment=environment, encrypted_access_token=cipher.encrypt(payload["access_token"]), encrypted_refresh_token=cipher.encrypt(refresh), access_token_expires_at=now + timedelta(seconds=int(payload.get("expires_in", 7200))), refresh_token_expires_at=now + timedelta(seconds=int(payload["refresh_token_expires_in"])) if payload.get("refresh_token_expires_in") else None, scopes=payload.get("scope", SELLER_SCOPE), updated_at=now)
    if record:
        for key, value in values.items():
            setattr(record, key, value)
    else:
        record = EbayCredential(id=1, **values)
        session.add(record)
    session.commit()
    return record


async def valid_user_access_token(session: Session, cipher: TokenCipher, oauth: EbayOAuthClient, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    record = session.get(EbayCredential, 1)
    if not record:
        raise OAuthError("eBay Sandbox is not connected")
    expiry = record.access_token_expires_at.replace(tzinfo=UTC) if record.access_token_expires_at.tzinfo is None else record.access_token_expires_at
    if expiry > now + timedelta(seconds=60):
        return cipher.decrypt(record.encrypted_access_token)
    payload = await oauth.refresh(cipher.decrypt(record.encrypted_refresh_token))
    payload.setdefault("refresh_token", cipher.decrypt(record.encrypted_refresh_token))
    updated = store_user_tokens(session, cipher, payload, now=now)
    return cipher.decrypt(updated.encrypted_access_token)
