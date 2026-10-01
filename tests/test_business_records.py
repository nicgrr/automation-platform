import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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


def test_release_calendar_rows_are_capped_at_200(client, session):
    from automation_control.models import ReleaseCalendarEntry

    for i in range(205):
        session.add(ReleaseCalendarEntry(id=f"r{i}", product_name=f"Product {i}", release_date="2026-12-01"))
    session.commit()

    page = client.get("/release-calendar").text

    assert page.count("<tr>") == 201  # 1 header row + 200 capped body rows, not all 205
    assert "200 of 205 shown, capped at 200 rows." in page


# --- release calendar: editing, deleting, urgency ------------------------

def test_a_release_can_be_edited(client, session):
    from automation_control.models import ReleaseCalendarEntry

    client.post("/release-calendar", data={
        "product_name": "Delta Reign ETB", "release_date": "2026-11-06",
        "supplier_deadline": "", "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    entry_id = session.scalars(select(ReleaseCalendarEntry)).one().id

    resp = client.post(f"/release-calendar/{entry_id}/edit", data={
        "product_name": "Delta Reign ETB (corrected)", "release_date": "2026-11-13",
        "supplier_deadline": "2026-10-24", "wholesale_price": "50", "retail_price": "80", "ordered_quantity": "3",
    }, follow_redirects=False)

    assert resp.status_code == 303
    session.expire_all()
    entry = session.get(ReleaseCalendarEntry, entry_id)
    assert entry.product_name == "Delta Reign ETB (corrected)"
    assert entry.release_date == "2026-11-13"
    assert entry.ordered_quantity == 3


def test_editing_an_unknown_entry_404s(client):
    resp = client.post("/release-calendar/does-not-exist/edit",
                       data={"product_name": "X", "release_date": "", "supplier_deadline": "",
                             "wholesale_price": "", "retail_price": "", "ordered_quantity": ""})
    assert resp.status_code == 404


def test_a_release_can_be_deleted(client, session):
    from automation_control.models import ReleaseCalendarEntry

    client.post("/release-calendar", data={
        "product_name": "Cancelled Order", "release_date": "2026-12-01",
        "supplier_deadline": "", "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    entry_id = session.scalars(select(ReleaseCalendarEntry)).one().id

    resp = client.post(f"/release-calendar/{entry_id}/delete", follow_redirects=False)

    assert resp.status_code == 303
    assert session.get(ReleaseCalendarEntry, entry_id) is None


def test_deleting_an_unknown_entry_404s(client):
    resp = client.post("/release-calendar/does-not-exist/delete")
    assert resp.status_code == 404


def test_an_approaching_deadline_is_flagged_urgent_on_the_page(client):
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=2)).isoformat()
    client.post("/release-calendar", data={
        "product_name": "Urgent ETB", "release_date": "2027-01-01",
        "supplier_deadline": soon, "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    page = client.get("/release-calendar").text
    assert "order deadline soon" in page
    assert "pill bad" in page


def test_a_past_release_is_labelled_released_not_upcoming(client):
    client.post("/release-calendar", data={
        "product_name": "Old ETB", "release_date": "2020-01-01",
        "supplier_deadline": "", "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    page = client.get("/release-calendar").text
    assert "released" in page


def test_urgent_entries_sort_before_distant_ones(client):
    client.post("/release-calendar", data={
        "product_name": "Far Future ETB", "release_date": "2030-01-01",
        "supplier_deadline": "", "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=1)).isoformat()
    client.post("/release-calendar", data={
        "product_name": "Urgent ETB", "release_date": "2027-06-01",
        "supplier_deadline": soon, "wholesale_price": "", "retail_price": "", "ordered_quantity": "",
    })
    page = client.get("/release-calendar").text
    assert page.index("Urgent ETB") < page.index("Far Future ETB")


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
