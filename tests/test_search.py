import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CatalogItem, CatalogItemType, InventoryItem, TcgCard


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


def _add_card(session, item_id: str, name: str, game: str = "pokemon", number: str = "1") -> None:
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.add(TcgCard(catalog_item_id=item_id, game=game, number=number, set_code="TST"))


def test_blank_query_shows_a_prompt_not_results(client):
    response = client.get("/search")
    assert response.status_code == 200
    assert "type a card name" in response.text


def test_matching_a_card_name_shows_it_as_not_owned_by_default(client, session):
    _add_card(session, "sv1-1", "Sprigatito")
    session.commit()

    response = client.get("/search?q=sprig")

    assert response.status_code == 200
    assert "Sprigatito" in response.text
    assert "not owned" in response.text


def test_an_owned_card_shows_its_total_quantity(client, session):
    _add_card(session, "sv1-2", "Fuecoco")
    session.add(InventoryItem(card_id="sv1-2", catalog_item_id="sv1-2", quantity=2))
    session.add(InventoryItem(card_id="sv1-2", catalog_item_id="sv1-2", variant="HOLO", quantity=1))
    session.commit()

    response = client.get("/search?q=fuecoco")

    assert "own 3" in response.text


def test_a_one_piece_card_is_found_alongside_pokemon_by_the_same_search(client, session):
    _add_card(session, "sv1-3", "Kaido Fanclub", game="pokemon")
    _add_card(session, "op17-062", "Kaido", game="one_piece")
    session.commit()

    response = client.get("/search?q=kaido")

    assert "Kaido Fanclub" in response.text
    assert response.text.count("Kaido") >= 2
    assert "one_piece" in response.text


def test_no_matches_says_so_plainly(client, session):
    response = client.get("/search?q=nonexistent-card-xyz")
    assert "No catalogue items match" in response.text


def test_search_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/search?q=anything", follow_redirects=False)
    assert response.status_code in (302, 303, 401)


def test_search_page_includes_the_autocomplete_wiring(client):
    response = client.get("/search")
    assert "global-search-input" in response.text
    assert "global-search-results" in response.text
    assert "/search/suggest" in response.text


def test_suggest_returns_json_results_with_owned_counts(client, session):
    _add_card(session, "sv1-4", "Charmander")
    session.add(InventoryItem(card_id="sv1-4", catalog_item_id="sv1-4", quantity=5))
    session.commit()

    response = client.get("/search/suggest?q=char")

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["name"] == "Charmander"
    assert data["results"][0]["owned"] == 5
    assert "pokemon" in data["results"][0]["detail"]


def test_suggest_requires_a_minimum_query_length(client, session):
    _add_card(session, "sv1-5", "Pikachu")
    session.commit()

    response = client.get("/search/suggest?q=p")

    assert response.json() == {"results": []}


def test_suggest_caps_results_below_the_full_search_page(client, session):
    for i in range(15):
        _add_card(session, f"bulk-{i}", f"Bulk Common {i}", number=str(i))
    session.commit()

    response = client.get("/search/suggest?q=bulk")

    assert len(response.json()["results"]) == 8  # SUGGEST_LIMIT


def test_suggest_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/search/suggest?q=anything", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
