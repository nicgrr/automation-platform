import base64
import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request, status

# 8 hours meant a workday of steady use ended in a logout mid-task, with no
# way back in short of typing the password again. This app is Tailscale-only
# and single-user (see ARCHITECTURE.md), so a long-lived, quietly-renewing
# session costs nothing a public-facing login couldn't accept -- the actual
# perimeter is the tailnet, not this cookie.
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days of no activity, then out
# Below this age a session is left untouched -- reissuing the cookie on
# every single request would mean every request carries a Set-Cookie
# header for no benefit, since the session isn't close to expiring anyway.
SESSION_RENEW_THRESHOLD_SECONDS = 24 * 3600


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


def verify_session(value: str, signing_key: str, *, max_age: int = SESSION_MAX_AGE_SECONDS, now: int | None = None) -> str | None:
    username, _issued = _verify_session_with_issued(value, signing_key, max_age=max_age, now=now)
    return username


def _verify_session_with_issued(value: str, signing_key: str, *, max_age: int = SESSION_MAX_AGE_SECONDS,
                                now: int | None = None) -> tuple[str | None, int]:
    try:
        payload, signature = value.rsplit(".", 1)
        expected = hmac.new(signing_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None, 0
        padded = payload + "=" * (-len(payload) % 4)
        username, issued_text = base64.urlsafe_b64decode(padded).decode().rsplit("|", 1)
        issued = int(issued_text)
        current = now or int(time.time())
        if issued > current + 60 or current - issued > max_age:
            return None, 0
        return username, issued
    except (ValueError, UnicodeDecodeError):
        return None, 0


def require_dashboard_user(request: Request) -> str:
    """Sliding session: a request past SESSION_RENEW_THRESHOLD_SECONDS since
    login marks itself in `request.state` for renewal, and
    `renew_session_middleware` (api.py) writes the actual Set-Cookie header
    onto whatever the route ends up returning.

    That indirection is required, not incidental. Every route in this app
    returns its own Response object directly (`return HTMLResponse(...)`,
    `return RedirectResponse(...)`), and FastAPI only merges a dependency's
    injected `Response` headers into the final response when the route
    *doesn't* return one of its own -- when it does, that object wins and
    whatever was set on the dependency's Response is silently dropped. A
    first version of this set the cookie here directly and it never once
    reached the browser; the failure is invisible without literally
    inspecting Set-Cookie headers, which is what caught it.
    """
    settings = request.app.state.settings
    cookie = request.cookies.get("rbay_session")
    if not cookie or not settings.session_signing_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    username, issued = _verify_session_with_issued(cookie, settings.session_signing_key)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    if time.time() - issued > SESSION_RENEW_THRESHOLD_SECONDS:
        request.state.renew_session_for = username
    return username
