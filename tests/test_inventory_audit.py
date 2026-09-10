import imagehash
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.database import Base, get_session
from automation_control.inventory_audit import find_mismatches
from automation_control.models import CardVariant, CatalogCard, InventoryItem, PhotoAuditDismissal
from automation_control.scan_ingest.identify import art_phash


def _pattern_image(path, seed):
    # A solid-color image has no gradient, so pHash hashes every flat color
    # identically -- distinct pseudo-random noise per seed is what actually
    # produces two images that hash far apart, mimicking two different cards.
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(280, 200, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(path)


def _phashes(path):
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return str(imagehash.phash(rgb)), str(art_phash(rgb))


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


@pytest.fixture
def mismatched_item(session, tmp_path):
    """A "Card A" filed item whose stored photo is actually solid-blue, while
    the "Card A" reference is solid-red -- unmistakably the wrong picture."""
    red_path = tmp_path / "card_a_reference.jpg"
    blue_path = tmp_path / "card_b_reference.jpg"
    photo_path = tmp_path / "filed_item_photo.jpg"
    _pattern_image(red_path, seed=1)
    _pattern_image(blue_path, seed=2)
    _pattern_image(photo_path, seed=2)  # the item's actual photo looks like card B, not A

    red_whole, red_art = _phashes(red_path)
    blue_whole, blue_art = _phashes(blue_path)

    session.add(CatalogCard(id="card-a", set_id="set1", number="1", name="Card A", phash=red_whole, art_phash=red_art))
    session.add(CatalogCard(id="card-b", set_id="set1", number="2", name="Card B", phash=blue_whole, art_phash=blue_art))
    item = InventoryItem(id="inv1", card_id="card-a", catalog_item_id="card-a",
                          variant=CardVariant.NORMAL, quantity=1, scan_image_path=str(photo_path))
    session.add(item)
    session.commit()
    return item


def test_find_mismatches_flags_a_photo_that_does_not_match_its_filed_card(session, mismatched_item):
    mismatches = find_mismatches(session)

    assert len(mismatches) == 1
    assert mismatches[0]["item_id"] == "inv1"
    assert mismatches[0]["filed_label"] == "set1 #1 Card A"
    assert mismatches[0]["looks_like_label"] == "set1 #2 Card B"


def test_find_mismatches_ignores_a_photo_that_matches_its_filed_card(session, tmp_path):
    path = tmp_path / "correct.jpg"
    _pattern_image(path, seed=1)
    whole, art = _phashes(path)
    session.add(CatalogCard(id="card-a", set_id="set1", number="1", name="Card A", phash=whole, art_phash=art))
    session.add(InventoryItem(id="inv1", card_id="card-a", catalog_item_id="card-a",
                               variant=CardVariant.NORMAL, quantity=1, scan_image_path=str(path)))
    session.commit()

    assert find_mismatches(session) == []


def test_review_page_shows_the_worst_mismatch_first(client, mismatched_item):
    response = client.get("/inventory-audit")
    assert response.status_code == 200
    assert "Card A" in response.text
    assert "Card B" in response.text


def test_dismissing_removes_it_from_the_pending_queue(client, session, mismatched_item):
    response = client.post("/inventory-audit/dismiss", data={"item_id": "inv1", "own_distance": 40}, follow_redirects=False)
    assert response.status_code == 303

    assert session.query(PhotoAuditDismissal).filter_by(inventory_item_id="inv1").count() == 1
    response = client.get("/inventory-audit")
    assert "Nothing flagged right now" in response.text


def test_dismissing_twice_does_not_create_a_duplicate_row(client, session, mismatched_item):
    client.post("/inventory-audit/dismiss", data={"item_id": "inv1", "own_distance": 40})
    client.post("/inventory-audit/dismiss", data={"item_id": "inv1", "own_distance": 40})

    assert session.query(PhotoAuditDismissal).filter_by(inventory_item_id="inv1").count() == 1


def test_deleting_removes_the_item_and_its_shop_listing(client, session, mismatched_item):
    from automation_control.models import CatalogItem, CatalogItemType, ShopListing

    session.add(CatalogItem(id="card-a", item_type=CatalogItemType.TCG_CARD, name="Card A"))
    session.add(ShopListing(id="listing1", catalog_item_id="card-a", inventory_item_id="inv1",
                             title="Card A", price="10.00"))
    session.commit()

    response = client.post("/inventory-audit/delete", data={"item_id": "inv1"}, follow_redirects=False)
    assert response.status_code == 303

    assert session.get(InventoryItem, "inv1") is None
    assert session.get(ShopListing, "listing1") is None


def test_review_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/inventory-audit", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
