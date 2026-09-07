import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session


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


def test_manifest_is_served_and_valid_json():
    response = TestClient(app).get("/static/manifest.json")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "EzBay"
    assert data["display"] == "standalone"
    assert len(data["icons"]) == 2


def test_icons_are_served_as_png():
    for path in ["/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/apple-touch-icon.png"]:
        response = TestClient(app).get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"] == "image/png"


def test_static_assets_do_not_require_login():
    """The manifest/icons must be reachable without a session -- iOS/Android
    fetch them before any login flow runs."""
    response = TestClient(app).get("/static/manifest.json")
    assert response.status_code == 200


def test_pages_include_pwa_tags(client):
    # Every page shares one shell (ui.py's page()) since the light-theme
    # unification, so a single quote-agnostic check covers all of them --
    # asserting a specific quote character here would just be pinning an
    # implementation detail of the shell, not the actual requirement.
    for path in ["/search", "/dashboard"]:
        response = client.get(path)
        assert "manifest.json" in response.text, path
        assert "rel='manifest'" in response.text or 'rel="manifest"' in response.text, path
        assert "apple-touch-icon" in response.text, path
        assert "apple-mobile-web-app-capable" in response.text, path
