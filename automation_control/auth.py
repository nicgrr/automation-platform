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


# In-process sliding-window limiter for /login -- no new infra needed for
# a single-process, single-operator app (see ARCHITECTURE.md's flagged
# gap: "no rate limiting on the login endpoint"). Keyed by client IP,
# counting only *failed* attempts, so a legitimate double-click or a
# password typo doesn't self-lock-out the one real user, but repeated
# wrong-password attempts from the same source do get slowed down.
# Process-local by design: a restart clears it, and that's fine -- the
# threat this defends against (a script trying many passwords in a
# burst) resets naturally on a restart anyway, and this app has no
# multi-worker deployment to keep in sync across.
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5

_login_failures: dict[str, list[float]] = {}


def login_rate_limited(client_key: str, *, now: float | None = None) -> bool:
    """True if `client_key` (normally the client IP) has already made
    LOGIN_RATE_LIMIT_MAX_ATTEMPTS failed attempts within the last
    LOGIN_RATE_LIMIT_WINDOW_SECONDS, and should be refused before even
    checking the submitted password."""
    now = now if now is not None else time.time()
    attempts = _login_failures.get(client_key, [])
    recent = [t for t in attempts if now - t < LOGIN_RATE_LIMIT_WINDOW_SECONDS]
    _login_failures[client_key] = recent
    return len(recent) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS


def record_login_failure(client_key: str, *, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    _login_failures.setdefault(client_key, []).append(now)


def clear_login_failures(client_key: str) -> None:
    _login_failures.pop(client_key, None)


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


def csrf_token_for(username: str, issued: int, signing_key: str) -> str:
    """Deterministic per-session CSRF token, signed the same way the
    session cookie itself is -- no separate server-side token store
    needed, just the same (username, issued) pair `create_session`
    already embeds. Double-submit-cookie pattern: this value is also set
    as a second, non-httponly cookie (rbay_csrf, see api.py) so the
    shared client-side JS (ui.py's CSRF_SCRIPT) can read it and attach it
    to every form post and fetch()-based POST automatically -- a
    cross-site attacker can forge a request that carries the (httponly)
    session cookie, but has no way to read this one to put the matching
    value in the request body, since browsers never let one origin read
    another's cookies."""
    return hmac.new(signing_key.encode(), f"csrf|{username}|{issued}".encode(), hashlib.sha256).hexdigest()


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


# Real state changes only -- GET/HEAD/OPTIONS never need a CSRF check
# (and requiring one would break plain links / bookmarklet-style GETs).
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Only these content types can plausibly come from a real <form> or
# FormData post -- a JSON API body (e.g. /approvals, /jobs) can't be
# triggered by a plain cross-site <form> without a CORS preflight this
# app already refuses, so those routes are intentionally left out of
# this check rather than forced to carry a token they have no page to
# read it from.
_CSRF_CHECKED_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def require_dashboard_user(request: Request) -> str:
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

    Also the one choke point for CSRF verification (added 2026-09-29):
    since essentially every mutating route already depends on this
    function for auth, checking the double-submit token here covers all
    of them without touching each route or template individually -- see
    csrf_token_for's docstring for the actual mechanism. `await
    request.form()` here doesn't cost the route its own Form(...)
    parameters: Starlette caches the parsed body, so this and the route's
    own parsing read the same cached result rather than the stream twice.
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

    if request.method in _CSRF_PROTECTED_METHODS:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(_CSRF_CHECKED_CONTENT_TYPES):
            form = await request.form()
            submitted = form.get("_csrf")
            expected = csrf_token_for(username, issued, settings.session_signing_key)
            if not submitted or not hmac.compare_digest(str(submitted), expected):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing or invalid CSRF token")
    return username
