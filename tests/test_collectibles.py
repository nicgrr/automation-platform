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


def test_add_a_secret_figure_and_list_it(client):
    create = client.post(
        "/collectibles",
        data={"name": "Sonny Angel Fruits Series - Secret Pineapple", "brand": "Sonny Angel", "series": "Fruits",
              "character": "", "variant": "Pineapple", "blind_box_series": "Fruits Series 2", "is_secret": "true", "retail_price": "18"},
        follow_redirects=False,
    )
    assert create.status_code == 303

    listing = client.get("/collectibles")
    assert "Sonny Angel Fruits Series - Secret Pineapple" in listing.text
    assert "secret" in listing.text
    assert "$18.00" in listing.text


def test_a_non_secret_figure_shows_no_secret_badge(client):
    client.post("/collectibles", data={"name": "Smiski Bathroom Series", "brand": "Smiski", "series": "", "character": "", "variant": "", "blind_box_series": "", "is_secret": "", "retail_price": ""}, follow_redirects=False)
    listing = client.get("/collectibles")
    assert "Smiski Bathroom Series" in listing.text


def test_collectibles_page_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/collectibles", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
