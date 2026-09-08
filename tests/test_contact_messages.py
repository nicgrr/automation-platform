import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import ContactMessage


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


def make_message(session, *, name="A Buyer", is_read=False):
    message = ContactMessage(name=name, email="buyer@example.test", message="Hi there", is_read=is_read)
    session.add(message)
    session.commit()
    return message


def test_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/contact-messages")
    assert resp.status_code in (401, 403, 307, 302)


def test_shows_unread_by_default(client, session):
    make_message(session, name="Unread One", is_read=False)
    make_message(session, name="Already Read", is_read=True)
    body = client.get("/contact-messages").text
    assert "Unread One" in body
    assert "Already Read" not in body


def test_all_filter_shows_everything(client, session):
    make_message(session, name="Unread One", is_read=False)
    make_message(session, name="Already Read", is_read=True)
    body = client.get("/contact-messages?show=all").text
    assert "Unread One" in body and "Already Read" in body


def test_marking_read_updates_the_record(client, session):
    message = make_message(session)
    resp = client.post(f"/contact-messages/{message.id}/read", follow_redirects=False)
    assert resp.status_code == 303
    session.refresh(message)
    assert message.is_read is True
    assert message.read_at is not None


def test_unknown_message_id_404s(client):
    assert client.post("/contact-messages/does-not-exist/read").status_code == 404


def test_message_body_is_shown(client, session):
    session.add(ContactMessage(name="Buyer", email="b@example.test", message="Do you ship to NZ?"))
    session.commit()
    body = client.get("/contact-messages?show=all").text
    assert "Do you ship to NZ?" in body
