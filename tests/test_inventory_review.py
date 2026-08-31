from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control import inventory_review
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.models import CardPrice, CardSet, CardVariant, CatalogCard, InventoryItem

# The inventory is organised set-first: /inventory is a grid of set tiles and
# individual cards live at /inventory/set/{id}. Card-level assertions
# therefore target the set page; set-level ones target the landing page.
SET_PAGE = "/inventory/set/xy11"


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture(autouse=True)
def fixed_fx(monkeypatch):
    """Pin the AUD rate so value assertions are deterministic and the suite
    never depends on a live FX call. 2.0 makes conversions obvious by eye."""
    monkeypatch.setattr(inventory_review.fx, "rates", lambda force=False: {"USD": 2.0, "EUR": 4.0, "AUD": 1.0})
    monkeypatch.setattr(inventory_review.fx, "is_live", lambda: True)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(session, *, card_id="xy11-31", number="31", name="Dewott", set_id="xy11", set_name="Steam Siege",
          variant=CardVariant.NORMAL, quantity=1, price: Decimal | None = Decimal("2.50"), currency="USD",
          printed_total=114):
    if session.get(CardSet, set_id) is None:
        session.add(CardSet(id=set_id, name=set_name, printed_total=printed_total))
    card = session.get(CatalogCard, card_id)
    if card is None:
        card = CatalogCard(id=card_id, set_id=set_id, number=number, name=name)
        session.add(card)
    item = InventoryItem(card_id=card_id, variant=variant, condition="Near Mint", quantity=quantity)
    session.add(item)
    if price is not None:
        session.add(CardPrice(card_id=card_id, variant=variant, source="test", price=price, currency=currency))
    session.commit()
    return item


def test_inventory_requires_auth():
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/inventory")
    assert resp.status_code in (401, 403, 307, 302)


# --- set grid --------------------------------------------------------------

def test_inventory_lists_sets_you_own_cards_from(client, session):
    _seed(session, name="Dewott")
    resp = client.get("/inventory")
    assert resp.status_code == 200
    assert "Steam Siege" in resp.text
    assert "/inventory/set/xy11" in resp.text


def test_set_grid_loads_no_card_images(client, session):
    """The landing page's whole point is being fast: it must not reference a
    single per-card scan, however many cards are owned."""
    for n in range(1, 6):
        _seed(session, card_id=f"xy11-{n}", number=str(n), name=f"Card {n}")
    resp = client.get("/inventory")
    assert "/inventory/thumb/" not in resp.text
    assert "/inventory/image/" not in resp.text


def test_set_grid_shows_progress_against_the_printed_total(client, session):
    _seed(session, card_id="xy11-1", number="1", name="One", printed_total=114)
    _seed(session, card_id="xy11-2", number="2", name="Two")
    resp = client.get("/inventory")
    assert "Progress: 2/114" in resp.text


def test_inventory_empty_state(client):
    resp = client.get("/inventory")
    assert resp.status_code == 200
    assert "Nothing scanned yet" in resp.text


def test_set_grid_totals_are_converted_to_aud(client, session):
    """Values are stored in source currency and converted for display; with
    USD pinned at 2.0, a $2.50 USD card reads as $5.00 AUD."""
    _seed(session, quantity=1, price=Decimal("2.50"), currency="USD")
    resp = client.get("/inventory")
    assert "$5.00" in resp.text
    assert "AUD" in resp.text


def test_set_grid_sums_mixed_currencies_into_one_aud_figure(client, session):
    _seed(session, card_id="xy11-31", number="31", name="Dewott", quantity=2, price=Decimal("1.00"), currency="USD")
    _seed(session, card_id="xy11-29", number="29", name="Gastrodon", quantity=1, price=Decimal("3.00"), currency="EUR")
    resp = client.get("/inventory")
    # (2 x $1 USD x 2.0) + (1 x EUR3 x 4.0) = $16.00 AUD
    assert "$16.00" in resp.text


def test_set_grid_separates_sets(client, session):
    _seed(session, card_id="xy11-31", set_id="xy11", set_name="Steam Siege", name="Dewott")
    _seed(session, card_id="xy12-1", number="1", set_id="xy12", set_name="Evolutions", name="Charizard")
    resp = client.get("/inventory")
    assert "Steam Siege" in resp.text and "Evolutions" in resp.text
    assert "/inventory/set/xy11" in resp.text and "/inventory/set/xy12" in resp.text


# --- one set's cards -------------------------------------------------------

def test_set_page_lists_that_set_s_cards_only(client, session):
    _seed(session, card_id="xy11-31", set_id="xy11", name="Dewott")
    _seed(session, card_id="xy12-1", number="1", set_id="xy12", set_name="Evolutions", name="Charizard")
    resp = client.get(SET_PAGE)
    assert "Dewott" in resp.text
    assert "Charizard" not in resp.text


def test_set_page_404s_for_an_unknown_set(client):
    assert client.get("/inventory/set/nope").status_code == 404


def test_set_page_shows_quantity_and_aud_price(client, session):
    _seed(session, quantity=3, price=Decimal("2.50"), currency="USD")
    resp = client.get(SET_PAGE)
    assert "x3" in resp.text        # quantity badge
    assert "$5.00" in resp.text     # unit price in AUD (2.50 x 2.0)
    assert "$15.00" in resp.text    # set total = 3 copies


def test_set_page_handles_missing_price_gracefully(client, session):
    _seed(session, price=None)
    resp = client.get(SET_PAGE)
    assert resp.status_code == 200
    assert "Dewott" in resp.text


def test_set_page_filters_by_variant(client, session):
    _seed(session, card_id="xy11-31", variant=CardVariant.NORMAL, name="Dewott Normal")
    _seed(session, card_id="xy11-32", number="32", variant=CardVariant.HOLO, name="Samurott Holo")
    resp = client.get(SET_PAGE, params={"variant": "holo"})
    assert "Samurott Holo" in resp.text
    assert "Dewott Normal" not in resp.text


def test_set_page_rejects_unknown_variant(client, session):
    _seed(session)
    assert client.get(SET_PAGE, params={"variant": "not-a-real-variant"}).status_code == 400


def test_set_page_sort_by_value_puts_highest_first(client, session):
    _seed(session, card_id="xy11-1", number="1", name="Cheap", price=Decimal("1.00"))
    _seed(session, card_id="xy11-2", number="2", name="Expensive", price=Decimal("50.00"))
    resp = client.get(SET_PAGE, params={"sort": "value_desc"})
    assert resp.text.index("Expensive") < resp.text.index("Cheap")


def test_set_page_sort_by_quantity_puts_most_copies_first(client, session):
    _seed(session, card_id="xy11-1", number="1", name="Single", quantity=1)
    _seed(session, card_id="xy11-2", number="2", name="Bulk", quantity=20)
    resp = client.get(SET_PAGE, params={"sort": "quantity_desc"})
    assert resp.text.index("Bulk") < resp.text.index("Single")


def test_set_page_default_sort_is_numeric_not_lexicographic(client, session):
    """Card '10' must sort after '2', not before it as a plain string sort
    would (lexicographic '10' < '2')."""
    _seed(session, card_id="xy11-2", number="2", name="Two")
    _seed(session, card_id="xy11-10", number="10", name="Ten")
    resp = client.get(SET_PAGE)
    assert resp.text.index("Two") < resp.text.index("Ten")


def test_set_page_uses_the_most_recent_price_observation(client, session):
    """Price history accumulates in CardPrice; the page must show the latest
    observation, not an arbitrary or the first one."""
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
    session.add(InventoryItem(card_id="xy11-31", variant=CardVariant.NORMAL, condition="Near Mint", quantity=1))
    old = datetime.now(UTC) - timedelta(days=5)
    session.add(CardPrice(card_id="xy11-31", variant=CardVariant.NORMAL, source="test", price=Decimal("1.00"), currency="USD", fetched_at=old))
    session.add(CardPrice(card_id="xy11-31", variant=CardVariant.NORMAL, source="test", price=Decimal("9.00"), currency="USD", fetched_at=datetime.now(UTC)))
    session.commit()

    resp = client.get(SET_PAGE)
    assert "$18.00" in resp.text   # 9.00 USD x 2.0
    assert "$2.00" not in resp.text  # not the stale 1.00 USD observation


def test_set_page_price_lookup_is_scoped_to_variant(client, session):
    """The same card's normal and holo prints have different prices; a
    variant mix-up here would show the wrong estimate."""
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
    session.add(InventoryItem(card_id="xy11-31", variant=CardVariant.NORMAL, condition="Near Mint", quantity=1))
    session.add(InventoryItem(card_id="xy11-31", variant=CardVariant.HOLO, condition="Near Mint", quantity=1))
    session.add(CardPrice(card_id="xy11-31", variant=CardVariant.NORMAL, source="test", price=Decimal("0.17"), currency="USD"))
    session.add(CardPrice(card_id="xy11-31", variant=CardVariant.HOLO, source="test", price=Decimal("15.00"), currency="USD"))
    session.commit()

    resp = client.get(SET_PAGE)
    assert "$0.34" in resp.text    # normal, 0.17 x 2.0
    assert "$30.00" in resp.text   # holo,   15.00 x 2.0


# --- images ----------------------------------------------------------------

def test_inventory_image_404s_when_item_has_no_image(client, session):
    item = _seed(session)
    assert client.get(f"/inventory/image/{item.id}").status_code == 404


def test_inventory_image_404s_for_unknown_item(client):
    assert client.get("/inventory/image/does-not-exist").status_code == 404


def _seed_with_image(session, tmp_path, name="card.jpg"):
    from PIL import Image

    image_path = tmp_path / name
    Image.new("RGB", (750, 1050), (200, 30, 30)).save(image_path, "JPEG")
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
    item = InventoryItem(card_id="xy11-31", variant=CardVariant.NORMAL, condition="Near Mint",
                         quantity=1, scan_image_path=str(image_path))
    session.add(item)
    session.commit()
    return item, image_path


def test_inventory_image_serves_the_full_file(client, session, tmp_path):
    item, image_path = _seed_with_image(session, tmp_path)
    resp = client.get(f"/inventory/image/{item.id}")
    assert resp.status_code == 200
    assert resp.content == image_path.read_bytes()


def test_thumb_is_generated_smaller_than_the_original(client, session, tmp_path, monkeypatch):
    """The reason this endpoint exists: serving 750x1050 scans as grid
    thumbnails made the page load dominated by images."""
    from PIL import Image

    monkeypatch.setattr(
        inventory_review, "get_settings",
        lambda: type("S", (), {"scan_media_dir": str(tmp_path / "media")})(),
    )
    item, image_path = _seed_with_image(session, tmp_path)

    resp = client.get(f"/inventory/thumb/{item.id}")
    assert resp.status_code == 200
    assert len(resp.content) < image_path.stat().st_size

    import io
    assert Image.open(io.BytesIO(resp.content)).width == inventory_review.THUMB_WIDTH


def test_thumb_falls_back_to_the_original_when_resizing_fails(client, session, tmp_path, monkeypatch):
    """A broken thumbnail must never take the page down -- the card still
    has to render, just without the size win."""
    monkeypatch.setattr(
        inventory_review, "get_settings",
        lambda: type("S", (), {"scan_media_dir": str(tmp_path / "media")})(),
    )
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
    broken = tmp_path / "not-an-image.jpg"
    broken.write_bytes(b"definitely not a jpeg")
    item = InventoryItem(card_id="xy11-31", variant=CardVariant.NORMAL, condition="Near Mint",
                         quantity=1, scan_image_path=str(broken))
    session.add(item)
    session.commit()

    resp = client.get(f"/inventory/thumb/{item.id}")
    assert resp.status_code == 200
    assert resp.content == b"definitely not a jpeg"


def test_thumb_404s_for_unknown_item(client):
    assert client.get("/inventory/thumb/does-not-exist").status_code == 404
