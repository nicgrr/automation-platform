import base64
import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request, status


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, expected_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.urlsafe_b64decode(salt_text), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, base64.urlsafe_b64decode(expected_text))
    except (ValueError, TypeError):
        return False


def create_session(username: str, signing_key: str, *, now: int | None = None) -> str:
    issued = now or int(time.time())
    payload = base64.urlsafe_b64encode(f"{username}|{issued}".encode()).decode().rstrip("=")
    signature = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session(value: str, signing_key: str, *, max_age: int = 8 * 3600, now: int | None = None) -> str | None:
    try:
        payload, signature = value.rsplit(".", 1)
        expected = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        username, issued_text = base64.urlsafe_b64decode(padded).decode().rsplit("|", 1)
        issued = int(issued_text)
        current = now or int(time.time())
        if issued > current + 60 or current - issued > max_age:
            return None
        return username
    except (ValueError, UnicodeDecodeError):
        return None


def require_dashboard_user(request: Request) -> str:
    settings = request.app.state.settings
    cookie = request.cookies.get("rbay_session")
    if not cookie or not settings.session_signing_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    username = verify_session(cookie, settings.session_signing_key)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    return username
