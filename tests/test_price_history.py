from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CardPrice, CardVariant, CatalogItem, CatalogItemType, TcgCard


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


def test_no_observations_says_so_plainly(client, session):
    session.add(CatalogItem(id="sv1-1", item_type=CatalogItemType.TCG_CARD, name="Sprigatito"))
    session.add(TcgCard(catalog_item_id="sv1-1", game="pokemon", number="1"))
    session.commit()

    response = client.get("/prices/sv1-1")
    assert response.status_code == 200
    assert "No price observations recorded" in response.text


def test_price_stats_computed_correctly_in_aud(client, session):
    session.add(CatalogItem(id="sv1-2", item_type=CatalogItemType.TCG_CARD, name="Fuecoco"))
    session.add(TcgCard(catalog_item_id="sv1-2", game="pokemon", number="2"))
    now = datetime.now(UTC)
    session.add(CardPrice(id="p1", card_id="sv1-2", variant=CardVariant.NORMAL, source="test", price=Decimal("10"), currency="AUD", fetched_at=now - timedelta(days=20)))
    session.add(CardPrice(id="p2", card_id="sv1-2", variant=CardVariant.NORMAL, source="test", price=Decimal("15"), currency="AUD", fetched_at=now - timedelta(days=3)))
    session.add(CardPrice(id="p3", card_id="sv1-2", variant=CardVariant.NORMAL, source="test", price=Decimal("20"), currency="AUD", fetched_at=now))
    session.commit()

    response = client.get("/prices/sv1-2")

    assert response.status_code == 200
    assert "$20.00" in response.text   # current (most recent) and high
    assert "$10.00" in response.text   # low
    assert "+100.0%" in response.text  # change from 10 -> 20


def test_unknown_card_returns_404(client):
    response = client.get("/prices/does-not-exist")
    assert response.status_code == 404


def test_price_history_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/prices/anything", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
