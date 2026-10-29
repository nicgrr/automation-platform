"""Return-to-where-I-was redirects after a form post (navigation.py)."""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from automation_control.navigation import (
    FLOW_COOKIE,
    finish_flow,
    redirect_back,
    remember_flow_origin,
    safe_return_path,
)


@pytest.mark.parametrize("value", [
    "/inventory/cards?sort=name&q=pikachu",
    "/shop-listings?status=active",
    "/",
])
def test_same_site_paths_are_accepted(value):
    assert safe_return_path(value) == value


@pytest.mark.parametrize("value", [
    None, "", "https://example.com/phish", "//example.com/phish", "/\\example.com",
    "javascript:alert(1)", "inventory", "/inventory\r\nSet-Cookie: x=1", "/a\\b",
    "/login", "/login/", "/logout", "/" + "a" * 2001,
])
def test_anything_that_could_leave_the_site_is_refused(value):
    assert safe_return_path(value) is None


app = FastAPI()


@app.post("/things/{thing_id}/edit")
def edit_thing(thing_id: str, request: Request):
    return redirect_back(request, "/things")


client = TestClient(app)


def _post(params: dict) -> str:
    resp = client.post("/things/7/edit", params=params, follow_redirects=False)
    assert resp.status_code == 303
    return resp.headers["location"]


def test_returns_to_the_page_the_form_was_on_with_a_saved_toast():
    assert _post({"_return_to": "/things?status=active&q=gohan"}) == "/things?status=active&q=gohan&_saved=Saved"


def test_an_edit_page_that_posts_to_itself_returns_to_the_page_before_it():
    location = _post({"_return_to": "/things/7/edit", "_return_prev": "/things?status=active"})
    assert location == "/things?status=active&_saved=Saved"


def test_an_edit_page_with_no_history_falls_back_to_the_default():
    assert _post({"_return_to": "/things/7/edit"}) == "/things?_saved=Saved"


def test_an_unsafe_return_falls_back_to_the_default():
    assert _post({"_return_to": "https://example.com/phish"}) == "/things?_saved=Saved"


def test_an_old_toast_is_not_carried_into_the_next_redirect():
    assert _post({"_return_to": "/things?_saved=Old"}) == "/things?_saved=Saved"


def test_a_post_without_return_parameters_keeps_the_old_redirect_exactly():
    assert _post({}) == "/things"


flow_app = FastAPI()


@flow_app.post("/upload")
def upload(request: Request):
    return remember_flow_origin(request, RedirectResponse("/review/1", status_code=303))


@flow_app.post("/review/last")
def review_last(request: Request):
    return finish_flow(request, "/review")


def test_finishing_a_queue_returns_to_where_the_upload_started():
    flow = TestClient(flow_app)
    flow.post("/upload", params={"_return_to": "/inventory/cards?q=gohan"}, follow_redirects=False)
    resp = flow.post("/review/last", follow_redirects=False)
    assert resp.headers["location"] == "/inventory/cards?q=gohan&_saved=All+reviewed"
    # one-shot: the next queue run without an upload keeps the old behaviour
    assert flow.post("/review/last", follow_redirects=False).headers["location"] == "/review"


def test_a_queue_with_no_upload_behind_it_keeps_its_old_ending():
    assert TestClient(flow_app).post("/review/last", follow_redirects=False).headers["location"] == "/review"


def test_an_upload_from_an_unsafe_page_records_no_origin():
    flow = TestClient(flow_app)
    resp = flow.post("/upload", params={"_return_to": "https://example.com"}, follow_redirects=False)
    assert FLOW_COOKIE not in resp.cookies
