import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import GoalKind, SupplierStatus


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


def test_add_and_list_a_supplier(client):
    create = client.post(
        "/suppliers",
        data={"name": "Big Wholesale Co", "contact": "sales@bigwholesale.com", "website": "", "categories": "TCG",
              "account_status": SupplierStatus.ACTIVE.value, "wholesale_discount_pct": "15"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    listing = client.get("/suppliers")
    assert "Big Wholesale Co" in listing.text
    assert "15.00%" in listing.text


def test_a_goal_marks_itself_achieved_once_progress_reaches_target(client):
    create = client.post("/goals", data={"label": "First $1,000 sales month", "kind": GoalKind.MILESTONE.value, "target_value": "1000"}, follow_redirects=False)
    assert create.status_code == 303

    board = client.get("/goals")
    assert "First $1,000 sales month" in board.text
    assert "achieved" not in board.text.lower().split("goal-card")[1]

    import re
    goal_id = re.search(r"/goals/([\w-]+)/progress", board.text).group(1)
    client.post(f"/goals/{goal_id}/progress", data={"current_value": "1000"}, follow_redirects=False)

    board2 = client.get("/goals")
    assert "achieved" in board2.text


def test_add_a_release_calendar_entry(client):
    create = client.post(
        "/release-calendar",
        data={"product_name": "One Piece OP18 Booster Box", "release_date": "2026-11-01", "supplier_deadline": "2026-10-01",
              "wholesale_price": "80", "retail_price": "130", "ordered_quantity": "5"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    listing = client.get("/release-calendar")
    assert "One Piece OP18 Booster Box" in listing.text
    assert "2026-11-01" in listing.text


def test_business_record_pages_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    for url in ["/suppliers", "/goals", "/release-calendar"]:
        response = client.get(url, follow_redirects=False)
        assert response.status_code in (302, 303, 401), url


def _create_supplier(client, name="Big Wholesale Co"):
    resp = client.post(
        "/suppliers",
        data={"name": name, "contact": "", "website": "", "categories": "TCG",
              "account_status": SupplierStatus.ACTIVE.value, "wholesale_discount_pct": ""},
        follow_redirects=False,
    )
    listing = client.get("/suppliers")
    import re
    return re.search(rf"/suppliers/([\w-]+)'>{name}", listing.text).group(1)


def test_supplier_detail_page_links_from_the_list(client):
    supplier_id = _create_supplier(client)
    detail = client.get(f"/suppliers/{supplier_id}")
    assert detail.status_code == 200
    assert "Big Wholesale Co" in detail.text
    assert "No products on file" in detail.text


def test_supplier_detail_404_for_unknown_supplier(client):
    resp = client.get("/suppliers/does-not-exist")
    assert resp.status_code == 404


def test_add_a_supplier_product_and_see_it_on_the_detail_page(client, session):
    from automation_control.models import SupplierProduct

    supplier_id = _create_supplier(client)
    create = client.post(
        f"/suppliers/{supplier_id}/products",
        data={"description": "Sonny Angel Mini Figures - Fruits Series", "cost": "12.50", "min_order_qty": "24"},
        follow_redirects=False,
    )
    assert create.status_code == 303
    assert create.headers["location"] == f"/suppliers/{supplier_id}"

    product = session.query(SupplierProduct).one()
    assert product.supplier_id == supplier_id
    assert product.cost == 12.5
    assert product.min_order_qty == 24

    detail = client.get(f"/suppliers/{supplier_id}")
    assert "Sonny Angel Mini Figures - Fruits Series" in detail.text
    assert "$12.50" in detail.text
    assert "24" in detail.text


def test_add_supplier_product_404_for_unknown_supplier(client):
    resp = client.post("/suppliers/does-not-exist/products", data={"description": "x", "cost": "1"})
    assert resp.status_code == 404
