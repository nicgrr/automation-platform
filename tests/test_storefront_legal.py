import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from storefront.app import create_app


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_privacy_page_renders(client):
    body = client.get("/privacy").text
    assert "Privacy policy" in body
    assert "Stripe" in body


def test_terms_page_renders(client):
    body = client.get("/terms").text
    assert "Terms of sale" in body
    assert "Australian Consumer Law" in body


def test_both_pages_flag_themselves_as_a_draft(client):
    """Placeholder business facts must never look finished -- a shopper
    or the operator should never mistake [BUSINESS_ABN] for a real one."""
    for path in ["/privacy", "/terms"]:
        body = client.get(path).text
        assert "Draft for review" in body
        assert "[BUSINESS_ABN]" in body or "[CONTACT_EMAIL]" in body


def test_footer_links_to_both_pages(client):
    body = client.get("/").text
    assert "href='/privacy'" in body
    assert "href='/terms'" in body


def test_legal_pages_use_the_normal_shop_chrome(client):
    """Not a bare content page -- same header/footer/nav as everywhere
    else, so it doesn't look like a broken link."""
    body = client.get("/privacy").text
    assert "class='topbar'" in body
    assert "NL Vault — trading cards" in body
