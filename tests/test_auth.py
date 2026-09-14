"""Sessions: the signed cookie itself, and the sliding renewal on top of it.

No test file existed for auth.py -- every other test overrode
require_dashboard_user rather than exercising it, so this logic (including
the original 8-hour cutoff) had never actually been run by the suite.
"""

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from automation_control.api import app
from automation_control.auth import (
    SESSION_MAX_AGE_SECONDS,
    SESSION_RENEW_THRESHOLD_SECONDS,
    create_session,
    require_dashboard_user,
    verify_session,
)

KEY = "test-signing-key"


class _FakeRequest:
    """Just enough of a Request for require_dashboard_user: cookies and a
    state bag it can write the renewal flag onto."""
    def __init__(self, cookie: str | None, settings):
        self.cookies = {"rbay_session": cookie} if cookie else {}
        app_state = type("AppState", (), {"settings": settings})()
        self.app = type("App", (), {"state": app_state})()
        self.state = type("State", (), {})()


def _settings(key=KEY):
    return type("S", (), {"session_signing_key": key})()


# --- the signed cookie itself ------------------------------------------

def test_a_session_survives_the_old_eight_hour_cutoff():
    """The whole point: an 8-hour session used to force a re-login mid-day.
    A token issued 8 hours and one minute ago must still verify."""
    issued = int(time.time()) - 8 * 3600 - 60
    token = create_session("nic", KEY, now=issued)
    assert verify_session(token, KEY, now=issued + 8 * 3600 + 120) == "nic"


def test_a_session_expires_at_thirty_days():
    issued = int(time.time())
    token = create_session("nic", KEY, now=issued)
    assert verify_session(token, KEY, now=issued + SESSION_MAX_AGE_SECONDS - 60) == "nic"
    assert verify_session(token, KEY, now=issued + SESSION_MAX_AGE_SECONDS + 60) is None


def test_a_tampered_token_is_rejected():
    token = create_session("nic", KEY)
    tampered = token[:-4] + "0000"
    assert verify_session(tampered, KEY) is None


def test_a_token_signed_with_a_different_key_is_rejected():
    token = create_session("nic", KEY)
    assert verify_session(token, "a-different-key") is None


def test_garbage_input_does_not_raise():
    for junk in ("", "no-dot-here", "a.b.c", "!!!.###"):
        assert verify_session(junk, KEY) is None


# --- sliding renewal -----------------------------------------------------

def test_a_fresh_session_is_not_flagged_for_renewal():
    request = _FakeRequest(create_session("nic", KEY, now=int(time.time()) - 60), _settings())
    require_dashboard_user(request)
    assert getattr(request.state, "renew_session_for", None) is None


def test_a_session_past_the_renew_threshold_is_flagged():
    issued = int(time.time()) - SESSION_RENEW_THRESHOLD_SECONDS - 100
    request = _FakeRequest(create_session("nic", KEY, now=issued), _settings())
    username = require_dashboard_user(request)
    assert username == "nic"
    assert request.state.renew_session_for == "nic"


def test_an_expired_session_401s_rather_than_renewing():
    issued = int(time.time()) - SESSION_MAX_AGE_SECONDS - 100
    request = _FakeRequest(create_session("nic", KEY, now=issued), _settings())
    with pytest.raises(HTTPException) as exc:
        require_dashboard_user(request)
    assert exc.value.status_code == 401
    assert getattr(request.state, "renew_session_for", None) is None


def test_no_cookie_401s():
    request = _FakeRequest(None, _settings())
    with pytest.raises(HTTPException) as exc:
        require_dashboard_user(request)
    assert exc.value.status_code == 401


# --- the middleware that actually writes the cookie -----------------------
# Every route in this app returns its own Response object (HTMLResponse,
# RedirectResponse), so a dependency's injected Response never reaches the
# browser -- FastAPI only merges those headers when the route doesn't
# return its own. The middleware is what makes renewal real; these tests
# go through the whole app rather than calling require_dashboard_user in
# isolation, specifically to catch that class of failure again.

@pytest.fixture
def client():
    app.state.settings.session_signing_key = KEY
    return TestClient(app)


def _set_cookie(client, age_seconds: int) -> str:
    token = create_session("nic", KEY, now=int(time.time()) - age_seconds)
    client.cookies.set("rbay_session", token)
    return token


def test_a_young_session_gets_no_new_cookie_on_an_html_route(client):
    _set_cookie(client, 3600)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "set-cookie" not in {h.lower() for h in resp.headers}


def test_a_stale_session_is_renewed_on_an_html_route(client):
    """HTMLResponse is the shape that broke the first version of this."""
    _set_cookie(client, SESSION_RENEW_THRESHOLD_SECONDS + 100)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "set-cookie" in {h.lower() for h in resp.headers}
    assert resp.headers["set-cookie"].startswith("rbay_session=")


def test_a_stale_session_is_renewed_on_a_redirect_route(client):
    """RedirectResponse is a different Response subclass -- the middleware
    must not be accidentally coupled to one."""
    _set_cookie(client, SESSION_RENEW_THRESHOLD_SECONDS + 100)
    resp = client.get("/collectibles", follow_redirects=False)
    assert "set-cookie" in {h.lower() for h in resp.headers}


def test_an_expired_session_gets_401_and_no_cookie(client):
    _set_cookie(client, SESSION_MAX_AGE_SECONDS + 100)
    resp = client.get("/dashboard")
    assert resp.status_code == 401
    assert "set-cookie" not in {h.lower() for h in resp.headers}


def test_the_renewed_cookie_is_itself_valid_for_a_fresh_window(client):
    """Renewal must actually extend the session, not just touch the cookie
    without changing what it verifies to."""
    _set_cookie(client, SESSION_RENEW_THRESHOLD_SECONDS + 100)
    resp = client.get("/dashboard")
    new_cookie = resp.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    # Would have failed under the old session's clock; must not fail under the new one.
    assert verify_session(new_cookie, KEY, now=int(time.time()) + SESSION_MAX_AGE_SECONDS - 60) == "nic"
