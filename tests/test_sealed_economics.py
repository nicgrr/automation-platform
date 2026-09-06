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


def _create_case(client) -> str:
    response = client.post(
        "/sealed-products",
        data={"name": "One Piece OP18 Case", "brand": "Bandai", "game": "one_piece", "product_type": "case",
              "units_per_display": "24", "displays_per_case": "12", "rrp": "1200"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_add_a_sealed_product_shows_up_in_the_list(client):
    _create_case(client)
    listing = client.get("/sealed-products")
    assert "One Piece OP18 Case" in listing.text
    assert "24 / display" in listing.text
    assert "12 / case" in listing.text


def test_scenario_comparison_matches_hand_calculation(client):
    """12 boxes/case x 24 packs/box = 288 packs/case."""
    url = _create_case(client)
    response = client.get(url, params={
        "case_cost": "900", "case_resale_value": "1100",
        "price_per_box": "100", "price_per_pack": "5", "avg_singles_value_per_pack": "6",
    })
    assert response.status_code == 200
    assert "288 pack(s) per case" in response.text
    assert "$200.00" in response.text    # whole case: 1100 - 900
    assert "$300.00" in response.text    # boxes: 12*100 - 900
    assert "$540.00" in response.text    # packs: 288*5 - 900
    assert "$828.00" in response.text    # singles: 288*6 - 900


def test_no_inputs_shows_the_form_without_a_comparison(client):
    url = _create_case(client)
    response = client.get(url)
    assert response.status_code == 200
    assert "Scenario comparison" not in response.text


def test_sealed_economics_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/sealed-products", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
