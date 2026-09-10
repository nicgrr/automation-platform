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
from automation_control.models import (
    CardSet, CardVariant, CatalogCard, CatalogItem, CatalogItemType, InventoryItem, PhotoAuditDismissal, ShopListing,
)
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
def client(session, tmp_path, monkeypatch):
    from automation_control import inventory_audit_review
    from automation_control.config import Settings

    # Without this, the review page's cache file (see inventory_audit_review
    # .py's _cache_path) resolves to the real scan_media_dir and every test
    # run would read/write the live production audit cache.
    monkeypatch.setattr(inventory_audit_review, "get_settings", lambda: Settings(scan_media_dir=str(tmp_path)))

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

    session.add(CatalogCard(id="card-a", set_id="set1", number="1", name="Card A", phash=red_whole, art_phash=red_art,
                             image_url="https://cards.example/card-a.jpg"))
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
    assert mismatches[0]["reference_image_url"] == "https://cards.example/card-a.jpg"


def test_find_mismatches_omits_reference_image_when_the_catalog_has_no_real_url(session, tmp_path):
    """catalog_cards.image_url turned out to hold a local scan-ingest cache
    path for most rows (see storefront/common.py::_is_web_url) -- offering
    it as a stock-photo replacement would just swap one broken image for
    another, so it must be filtered the same way the storefront does."""
    red_path = tmp_path / "card_a_reference.jpg"
    blue_path = tmp_path / "photo.jpg"
    _pattern_image(red_path, seed=1)
    _pattern_image(blue_path, seed=2)
    red_whole, red_art = _phashes(red_path)

    session.add(CatalogCard(id="card-a", set_id="set1", number="1", name="Card A", phash=red_whole, art_phash=red_art,
                             image_url="scan_ingest_data/cache/card-a.jpg"))
    session.add(InventoryItem(id="inv1", card_id="card-a", catalog_item_id="card-a",
                               variant=CardVariant.NORMAL, quantity=1, scan_image_path=str(blue_path)))
    session.commit()

    mismatches = find_mismatches(session)
    assert len(mismatches) == 1
    assert mismatches[0]["reference_image_url"] is None


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


def test_review_page_offers_stock_photo_replacement_when_a_reference_image_exists(client, mismatched_item):
    response = client.get("/inventory-audit")
    assert "/inventory-audit/use-stock-photo" in response.text
    assert "Replace with stock photo" in response.text


def test_use_stock_photo_clears_the_local_photo_without_touching_the_item(client, session, mismatched_item):
    response = client.post("/inventory-audit/use-stock-photo", data={"item_id": "inv1"}, follow_redirects=False)
    assert response.status_code == 303

    session.expire_all()
    item = session.get(InventoryItem, "inv1")
    assert item is not None
    assert item.scan_image_path is None

    response = client.get("/inventory-audit")
    assert "Nothing flagged right now" in response.text


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


def test_second_page_load_uses_the_cache_instead_of_recomputing(client, session, mismatched_item, monkeypatch):
    from automation_control import inventory_audit_review

    calls = []
    real = inventory_audit_review.find_mismatches
    monkeypatch.setattr(inventory_audit_review, "find_mismatches",
                         lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    client.get("/inventory-audit")
    client.get("/inventory-audit")
    assert len(calls) == 1


def test_rescan_forces_a_fresh_computation(client, session, mismatched_item, monkeypatch):
    from automation_control import inventory_audit_review

    calls = []
    real = inventory_audit_review.find_mismatches
    monkeypatch.setattr(inventory_audit_review, "find_mismatches",
                         lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    client.get("/inventory-audit")
    response = client.post("/inventory-audit/rescan", follow_redirects=False)
    assert response.status_code == 303
    assert len(calls) == 2


def test_deleting_the_item_hides_it_even_from_a_stale_cache(client, session, mismatched_item):
    """The cache can be minutes old; a delete or stock-photo swap must take
    effect immediately rather than waiting for the next ~30s rescan."""
    client.get("/inventory-audit")  # populate the cache
    client.post("/inventory-audit/delete", data={"item_id": "inv1"})

    response = client.get("/inventory-audit")
    assert "Nothing flagged right now" in response.text


def test_refile_moves_the_item_to_the_correct_card_with_no_existing_holding(client, session, mismatched_item):
    """The bug this guards: the photo audit could tell you a photo was
    wrong but never that the *identification* was wrong -- see
    scripts/audit_inventory_photos.py's own docstring for a real prior
    case (a Joltik committed as a Gengar). Delete-or-keep is wrong for
    that case since the card is still genuinely owned."""
    session.add(CardSet(id="set1", name="Set One", code="S1"))
    session.commit()

    response = client.post("/inventory-audit/refile",
                            data={"item_id": "inv1", "set_code": "S1", "number": "2"},
                            follow_redirects=False)
    assert response.status_code == 303

    session.expire_all()
    item = session.get(InventoryItem, "inv1")
    assert item is not None
    assert item.card_id == "card-b"
    assert item.catalog_item_id == "card-b"

    assert "Nothing flagged right now" in client.get("/inventory-audit").text


def test_refile_merges_into_an_existing_holding_of_the_correct_card(client, session, mismatched_item, tmp_path):
    session.add(CardSet(id="set1", name="Set One", code="S1"))
    existing_photo = tmp_path / "existing.jpg"
    _pattern_image(existing_photo, seed=3)
    session.add(InventoryItem(id="inv-existing", card_id="card-b", catalog_item_id="card-b",
                               variant=CardVariant.NORMAL, quantity=2, scan_image_path=str(existing_photo)))
    session.commit()

    client.post("/inventory-audit/refile", data={"item_id": "inv1", "set_code": "S1", "number": "2"})

    session.expire_all()
    assert session.get(InventoryItem, "inv1") is None
    assert session.get(InventoryItem, "inv-existing").quantity == 3


def test_refile_drops_any_listing_built_for_the_wrong_card(client, session, mismatched_item):
    session.add(CardSet(id="set1", name="Set One", code="S1"))
    session.add(CatalogItem(id="card-a", item_type=CatalogItemType.TCG_CARD, name="Card A"))
    session.add(ShopListing(id="listing1", catalog_item_id="card-a", inventory_item_id="inv1",
                             title="Card A", price="10.00"))
    session.commit()

    client.post("/inventory-audit/refile", data={"item_id": "inv1", "set_code": "S1", "number": "2"})

    assert session.get(ShopListing, "listing1") is None


def test_refile_rejects_an_unknown_set_code(client, session, mismatched_item):
    response = client.post("/inventory-audit/refile", data={"item_id": "inv1", "set_code": "NOPE", "number": "2"})
    assert response.status_code == 400
    session.expire_all()
    assert session.get(InventoryItem, "inv1").card_id == "card-a"


def test_refile_rejects_an_unknown_number(client, session, mismatched_item):
    session.add(CardSet(id="set1", name="Set One", code="S1"))
    session.commit()
    response = client.post("/inventory-audit/refile", data={"item_id": "inv1", "set_code": "S1", "number": "999"})
    assert response.status_code == 400


def test_refile_tolerates_a_zero_padded_number_from_the_photo(client, session, mismatched_item):
    """The photo often shows a zero-padded number ("002") while the
    stored catalogue number is bare ("2")."""
    session.add(CardSet(id="set1", name="Set One", code="S1"))
    session.commit()

    response = client.post("/inventory-audit/refile",
                            data={"item_id": "inv1", "set_code": "s1", "number": "002"},
                            follow_redirects=False)
    assert response.status_code == 303
    session.expire_all()
    assert session.get(InventoryItem, "inv1").card_id == "card-b"


def test_refile_form_is_present_on_the_review_page(client, mismatched_item):
    response = client.get("/inventory-audit")
    assert "/inventory-audit/refile" in response.text
    assert "Re-file to this card" in response.text


def test_review_requires_login():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.get("/inventory-audit", follow_redirects=False)
    assert response.status_code in (302, 303, 401)
