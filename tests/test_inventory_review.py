from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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


# --- sorting and the whole-set view ----------------------------------------

def _seed_set(session, set_id, name, release_date, printed_total=100):
    session.add(CardSet(id=set_id, name=name, release_date=release_date, printed_total=printed_total))
    card = CatalogCard(id=f"{set_id}-1", set_id=set_id, number="1", name=f"{name} Card")
    session.add(card)
    session.add(InventoryItem(card_id=card.id, variant=CardVariant.NORMAL, condition="Near Mint", quantity=1))
    session.commit()


def test_sets_default_to_newest_release_first(client, session):
    _seed_set(session, "old", "Old Set", "2016/08/03")
    _seed_set(session, "new", "New Set", "2023/03/31")
    resp = client.get("/inventory")
    assert resp.text.index("New Set") < resp.text.index("Old Set")


def test_sets_can_be_sorted_oldest_first(client, session):
    _seed_set(session, "old", "Old Set", "2016/08/03")
    _seed_set(session, "new", "New Set", "2023/03/31")
    resp = client.get("/inventory", params={"sort": "released_asc"})
    assert resp.text.index("Old Set") < resp.text.index("New Set")


def test_sets_can_be_sorted_by_name(client, session):
    _seed_set(session, "zzz", "Zebra Set", "2016/08/03")
    _seed_set(session, "aaa", "Alpha Set", "2023/03/31")
    resp = client.get("/inventory", params={"sort": "name"})
    assert resp.text.index("Alpha Set") < resp.text.index("Zebra Set")


def test_a_set_with_no_release_date_sorts_last_not_first(client, session):
    """An unknown date must not masquerade as the newest release."""
    _seed_set(session, "dated", "Dated Set", "2020/01/01")
    _seed_set(session, "undated", "Undated Set", None)
    resp = client.get("/inventory")
    assert resp.text.index("Dated Set") < resp.text.index("Undated Set")


def test_unknown_sort_falls_back_rather_than_erroring(client, session):
    _seed_set(session, "a", "A Set", "2020/01/01")
    assert client.get("/inventory", params={"sort": "bogus"}).status_code == 200


def test_set_page_lists_unowned_cards_too(client, session):
    """Progress is 'x of y' -- you can only see that against the whole set,
    so unowned cards appear dimmed with Qty: 0 rather than being hidden."""
    _seed(session, card_id="xy11-1", number="1", name="Owned")
    session.add(CatalogCard(id="xy11-2", set_id="xy11", number="2", name="NotOwned"))
    session.commit()

    resp = client.get(SET_PAGE)
    assert "Owned" in resp.text and "NotOwned" in resp.text
    assert "Qty: 0" in resp.text
    assert "unowned" in resp.text


def test_set_page_owned_only_hides_the_rest(client, session):
    _seed(session, card_id="xy11-1", number="1", name="Owned")
    session.add(CatalogCard(id="xy11-2", set_id="xy11", number="2", name="NotOwned"))
    session.commit()

    resp = client.get(SET_PAGE, params={"owned_only": "1"})
    assert "Owned" in resp.text
    assert "NotOwned" not in resp.text


def test_set_page_splits_a_card_into_its_printings(client, session):
    """The same card in normal and reverse holo are different things to own
    and are priced differently, so each gets its own tile."""
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(
        id="xy11-1", set_id="xy11", number="1", name="Caterpie",
        raw_prices={"tcgplayer": {"prices": {"normal": {"market": 0.25},
                                             "reverseHolofoil": {"market": 0.54}}}},
    ))
    session.commit()

    resp = client.get(SET_PAGE)
    assert resp.text.count("Caterpie") == 2
    assert "Normal" in resp.text and "Reverse Holo" in resp.text


def test_set_page_shows_rarity_and_number(client, session):
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-1", set_id="xy11", number="1", name="Caterpie", rarity="Common"))
    session.commit()
    resp = client.get(SET_PAGE)
    assert "Common" in resp.text
    assert "1/114" in resp.text


def test_set_page_uses_uniform_reference_art_not_the_scan(client, session, tmp_path):
    """Tiles are all the same size because they all come from the catalog's
    245x342 reference art, not from scans of varying crop."""
    _seed(session, card_id="xy11-1", number="1", name="Owned")
    card = session.get(CatalogCard, "xy11-1")
    card.local_image_path = str(tmp_path / "ref.png")
    session.commit()

    resp = client.get(SET_PAGE)
    assert "/inventory/card-thumb/xy11-1" in resp.text


def test_card_thumb_is_resized_to_the_shared_width(client, session, tmp_path, monkeypatch):
    from PIL import Image
    import io

    monkeypatch.setattr(
        inventory_review, "get_settings",
        lambda: type("S", (), {"scan_media_dir": str(tmp_path / "media")})(),
    )
    ref = tmp_path / "ref.png"
    Image.new("RGB", (245, 342), (10, 120, 200)).save(ref)
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-1", set_id="xy11", number="1", name="Caterpie", local_image_path=str(ref)))
    session.commit()

    resp = client.get("/inventory/card-thumb/xy11-1")
    assert resp.status_code == 200
    assert Image.open(io.BytesIO(resp.content)).width == inventory_review.THUMB_WIDTH


def test_card_thumb_404s_without_reference_art(client, session):
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114))
    session.add(CatalogCard(id="xy11-1", set_id="xy11", number="1", name="Caterpie"))
    session.commit()
    assert client.get("/inventory/card-thumb/xy11-1").status_code == 404


def test_grid_children_can_shrink_so_columns_stay_even(client, session, tmp_path):
    """A nowrap card name sets its grid column's min-content width unless the
    child can shrink, which made columns wildly uneven and pushed card images
    outside their tiles. min-width:0 is what lets the ellipsis do its job."""
    _seed(session, card_id="xy11-1", number="1",
          name="A Very Long Card Name That Would Otherwise Widen Its Column")
    resp = client.get(SET_PAGE)

    assert resp.status_code == 200
    style = resp.text[resp.text.index("<style>"):]
    assert ".card-tile>*{min-width:0" in style.replace(" ", "")
    assert "min-width:0" in style.replace(" ", "")


def test_set_grid_children_can_shrink_too(client, session, tmp_path):
    _seed(session, set_id="xy11", set_name="A Set With A Very Long Name Indeed")
    resp = client.get("/inventory")
    style = resp.text[resp.text.index("<style>"):].replace(" ", "")
    assert "a.set-tile>*{min-width:0" in style


# --- searching and editing from the set page -------------------------------

def test_set_page_offers_a_card_search(client, session, tmp_path):
    """The whole set is already on the page, so filtering it should be a
    keystroke rather than a request."""
    _seed(session, card_id="xy11-31", number="31", name="Dewott")
    resp = client.get(SET_PAGE)
    assert "id='card-search'" in resp.text
    assert "data-find=" in resp.text
    assert "dewott" in resp.text.lower()


def test_owned_card_offers_quantity_controls(client, session, tmp_path):
    _seed(session, card_id="xy11-31", number="31", name="Dewott", quantity=2)
    resp = client.get(SET_PAGE)
    assert "value='dec'" in resp.text
    assert "value='remove'" in resp.text


def test_unowned_card_offers_add_only(client, session, tmp_path):
    _seed(session, card_id="xy11-1", number="1", name="Owned")
    session.add(CatalogCard(id="xy11-2", set_id="xy11", number="2", name="NotOwned"))
    session.commit()

    resp = client.get(SET_PAGE)

    assert "+ Add" in resp.text
    # a card you don't own has nothing to remove
    assert resp.text.count("value='remove'") == 1


def test_add_increments_an_existing_holding(client, session, tmp_path):
    item = _seed(session, card_id="xy11-31", number="31", quantity=1)
    resp = client.post("/inventory/adjust",
                       data={"card_id": "xy11-31", "variant": "normal", "action": "add",
                             "back": SET_PAGE}, follow_redirects=False)
    assert resp.status_code == 303
    session.expire_all()
    assert session.get(InventoryItem, item.id).quantity == 2


def test_add_creates_a_holding_for_a_card_never_scanned(client, session, tmp_path):
    """You can own a card the scanner never saw."""
    session.add(CardSet(id="xy11", name="Steam Siege", printed_total=114, cached_at=datetime.now(UTC)))
    session.add(CatalogCard(id="xy11-31", set_id="xy11", number="31", name="Dewott"))
    session.commit()

    client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                           "action": "add", "back": SET_PAGE}, follow_redirects=False)

    item = session.scalars(select(InventoryItem)).one()
    assert item.card_id == "xy11-31" and item.quantity == 1


def test_decrement_removes_the_row_at_zero(client, session, tmp_path):
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                           "action": "dec", "back": SET_PAGE}, follow_redirects=False)
    assert session.scalars(select(InventoryItem)).all() == []


def test_remove_clears_the_whole_holding(client, session, tmp_path):
    _seed(session, card_id="xy11-31", number="31", quantity=5)
    client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                           "action": "remove", "back": SET_PAGE}, follow_redirects=False)
    assert session.scalars(select(InventoryItem)).all() == []


def test_every_change_is_audited(client, session, tmp_path):
    """Inventory is the record of what you own; a silent edit to it would
    be indistinguishable from a bug."""
    from automation_control.models import AuditEvent

    _seed(session, card_id="xy11-31", number="31", quantity=1)
    client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                           "action": "add", "back": SET_PAGE}, follow_redirects=False)

    event = session.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.adjust")).one()
    assert event.details["change"] == "add"
    assert event.details["quantity"] == 2


def test_adjust_refuses_when_several_conditions_are_held(client, session, tmp_path):
    """Which condition to change isn't ours to guess, and guessing would
    silently edit the wrong holding."""
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    session.add(InventoryItem(card_id="xy11-31", variant=CardVariant.NORMAL,
                              condition="Played", quantity=1))
    session.commit()

    resp = client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                                  "action": "add", "back": SET_PAGE},
                       follow_redirects=False)

    assert resp.status_code == 400
    assert "2 conditions" in resp.json()["detail"]


def test_adjust_will_not_redirect_off_site(client, session, tmp_path):
    """`back` arrives from a form field; an open redirect isn't worth the
    convenience of remembering the view."""
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    resp = client.post("/inventory/adjust",
                       data={"card_id": "xy11-31", "variant": "normal", "action": "add",
                             "back": "https://example.com/phish"}, follow_redirects=False)
    assert resp.headers["location"] == "/inventory"


def test_adjust_rejects_unknown_card_and_action(client, session, tmp_path):
    _seed(session, card_id="xy11-31", number="31", quantity=1)
    assert client.post("/inventory/adjust", data={"card_id": "nope", "variant": "normal",
                                                  "action": "add"}, follow_redirects=False).status_code == 404
    assert client.post("/inventory/adjust", data={"card_id": "xy11-31", "variant": "normal",
                                                  "action": "explode"}, follow_redirects=False).status_code == 400
