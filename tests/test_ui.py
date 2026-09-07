import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.ui import STYLE, page, pill


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_every_pill_kind_has_its_own_color():
    # Regression: buying.py's traffic light has returned "warn" for a
    # YELLOW verdict since Phase 2, but ui.py's STYLE only ever defined
    # .ok/.bad/.neutral -- a YELLOW verdict silently rendered with no
    # color at all. Locking in all four now.
    for kind in ["ok", "bad", "warn", "neutral"]:
        assert f".pill.{kind} {{" in STYLE, kind


def test_page_includes_topbar_by_default(client):
    response = client.get("/collectibles")
    assert "<header class='topbar'>" in response.text
    assert "global-search-input" in response.text
    assert "global-search-results" in response.text
    # regression: the search box used to have no min-width override, so on
    # a narrow viewport the nav links squeezed it down to a few px wide
    # (flex items don't shrink below their content size by default).
    assert "min-width: 0" in response.text
    assert "@media (max-width: 680px)" in response.text


def test_page_can_omit_the_topbar(client):
    app.dependency_overrides.clear()
    response = TestClient(app).get("/login")
    assert "<header class='topbar'>" not in response.text
    assert "global-search-input" not in response.text


def test_only_one_search_box_per_page(client):
    # The dedicated /search page used to render its own big search input
    # with the same id as the shared topbar's, which is an invalid
    # duplicate id and broke getElementById-based wiring.
    response = client.get("/search?q=test")
    assert response.text.count("id='global-search-input'") == 1
    assert "value='test'" in response.text


def test_brand_header_shows_page_title_not_the_ezbay_wordmark():
    from automation_control.ui import brand_header

    html = brand_header("Collectibles")
    assert "<h1 class='page-title'>Collectibles</h1>" in html
    assert "&larr; Dashboard" in html


def test_brand_header_can_hide_the_back_link():
    from automation_control.ui import brand_header

    html = brand_header("Private Control Plane", show_back=False)
    assert "&larr;" not in html


def test_pill_helper_renders_the_given_kind():
    assert pill("YELLOW", "warn") == "<span class='pill warn'>YELLOW</span>"


def test_page_shell_has_no_leftover_dark_theme_literals():
    rendered = page("Title", "<p>body</p>")
    for literal in ["#05070d", "#0d1220", "#0a0f1c", "#5eeaff", "#a78bfa"]:
        assert literal not in rendered, literal
