import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control.database import Base, get_session
from automation_control.models import ContactMessage, SocialLink
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


def _submit(client, **overrides):
    data = {"name": "A Buyer", "email": "buyer@example.test", "message": "Is this still available?",
            "website": ""}
    data.update(overrides)
    return client.post("/contact", data=data, follow_redirects=False)


# --- the form and submission -------------------------------------------------

def test_contact_form_renders(client):
    body = client.get("/contact").text
    assert "Contact us" in body
    assert "name='message'" in body


def test_a_real_submission_is_stored(client, session):
    resp = _submit(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contact/sent"

    message = session.scalars(select(ContactMessage)).one()
    assert message.name == "A Buyer"
    assert message.email == "buyer@example.test"
    assert message.is_read is False


def test_the_honeypot_silently_drops_a_bot_submission(client, session):
    """A filled honeypot means a bot. It must look identical to success
    (same redirect) so a scraper learns nothing about being caught, but
    nothing gets stored."""
    resp = _submit(client, website="http://spam.example")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contact/sent"
    assert session.scalars(select(ContactMessage)).all() == []


def test_a_very_long_message_is_truncated_not_rejected(client, session):
    _submit(client, message="x" * 10_000)
    message = session.scalars(select(ContactMessage)).one()
    assert len(message.message) == 4000


def test_the_sent_page_renders(client):
    body = client.get("/contact/sent").text
    assert "Message sent" in body


# --- social links in the footer ----------------------------------------------

def test_a_platform_with_no_url_does_not_render(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram", url=None, is_active=True))
    session.commit()
    body = client.get("/").text
    assert "class='social-link'" not in body


def test_a_configured_platform_renders_in_the_footer(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=True))
    session.commit()
    body = client.get("/").text
    assert "https://instagram.com/nlvault" in body
    assert "target='_blank'" in body
    assert 'rel=\'noopener noreferrer\'' in body


def test_an_inactive_platform_is_hidden_even_with_a_url(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=False))
    session.commit()
    assert "instagram.com/nlvault" not in client.get("/").text


def test_social_links_appear_in_a_defined_order(client, session):
    session.add(SocialLink(platform="whatnot", label="Whatnot", url="https://whatnot.com/x",
                           is_active=True, sort_order=1))
    session.add(SocialLink(platform="instagram", label="Instagram", url="https://instagram.com/x",
                           is_active=True, sort_order=0))
    session.commit()
    body = client.get("/").text
    assert body.index("instagram.com") < body.index("whatnot.com")


def test_footer_links_to_contact_on_every_page(client):
    assert "href='/contact'" in client.get("/").text
    assert "href='/contact'" in client.get("/shipping").text


# --- home page icon row -----------------------------------------------------

def test_home_shows_no_icon_row_with_no_social_links(client, session):
    assert "class='social-icons'" not in client.get("/").text


def test_home_shows_an_icon_row_once_a_link_is_configured(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=True))
    session.commit()
    body = client.get("/").text
    assert "class='social-icons'" in body
    assert "https://instagram.com/nlvault" in body
    assert "aria-label='Instagram'" in body


def test_home_icon_row_has_no_visible_text_label(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=True))
    session.commit()
    body = client.get("/").text
    icon_block = body.split("class='social-icons'")[1].split("</div>")[0]
    assert "<span>Instagram</span>" not in icon_block


def test_home_icon_row_and_footer_row_both_render_independently(client, session):
    session.add(SocialLink(platform="instagram", label="Instagram",
                           url="https://instagram.com/nlvault", is_active=True))
    session.commit()
    body = client.get("/").text
    assert body.count("instagram.com/nlvault") == 2  # once in the hero, once in the footer
