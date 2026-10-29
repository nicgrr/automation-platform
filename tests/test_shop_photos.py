from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.config import get_settings
from automation_control.database import Base, get_session
from automation_control.models import (
    CardSet, CatalogCard, CatalogItem, CatalogItemType, InventoryItem, ShopListing, ShopListingStatus, TcgCard,
)
from storefront.app import create_app
from storefront.photos import has_own_photo, safe_source


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


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Point the media directory at a temp dir and hand back a real image."""
    from PIL import Image

    root = tmp_path / "media"
    root.mkdir()
    photo = root / "scan.jpg"
    Image.new("RGB", (750, 1050), (120, 90, 60)).save(photo)

    settings = get_settings()
    monkeypatch.setattr(settings, "scan_media_dir", str(root))
    get_settings.cache_clear()
    monkeypatch.setattr("storefront.photos.get_settings", lambda: settings)
    return root, photo


def make_listing(session, photo_path, *, status=ShopListingStatus.ACTIVE):
    item = CatalogItem(id="ci1", item_type=CatalogItemType.TCG_CARD, name="Gastrodon",
                       image_url="https://images.pokemontcg.io/x/1.png")
    tcg = TcgCard(catalog_item_id="ci1", game="pokemon", set_id="sts", number="29")
    inv = InventoryItem(id="inv1", catalog_item_id="ci1", quantity=1,
                        scan_image_path=str(photo_path) if photo_path else None)
    listing = ShopListing(id="l1", catalog_item_id="ci1", inventory_item_id="inv1",
                          title="Gastrodon #29", price=Decimal("12.00"), currency="AUD",
                          quantity=1, status=status,
                          image_url="https://images.pokemontcg.io/x/1.png")
    session.add_all([item, tcg, inv, listing])
    session.commit()
    return listing


def test_photo_route_serves_the_sellers_own_scan(client, session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    response = client.get("/photo/l1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert len(response.content) > 0


def test_photo_is_refused_for_a_listing_that_is_not_active(client, session, media_root):
    """The inventory table covers the whole collection. Without the ACTIVE
    check this route would let anyone enumerate every card you own, one id
    at a time -- listing something publicly is what grants access."""
    _, photo = media_root
    make_listing(session, photo, status=ShopListingStatus.DRAFT)
    assert client.get("/photo/l1").status_code == 404


def test_photo_is_a_404_for_an_unknown_listing(client, media_root):
    assert client.get("/photo/does-not-exist").status_code == 404


def test_a_path_outside_the_media_root_is_refused(session, media_root):
    """A database row rewritten to point at a system file must read as a
    404, not disclose the file."""
    assert safe_source("/etc/passwd") is None
    assert safe_source("../../etc/passwd") is None
    assert safe_source(None) is None


def test_a_traversal_path_inside_a_listing_is_refused(client, session, media_root):
    root, _ = media_root
    make_listing(session, f"{root}/../../../etc/passwd")
    assert client.get("/photo/l1").status_code == 404


def test_a_missing_file_is_refused_not_a_crash(client, session, media_root):
    root, _ = media_root
    make_listing(session, root / "gone.jpg")
    assert client.get("/photo/l1").status_code == 404


def test_has_own_photo_reflects_reality(session, media_root):
    _, photo = media_root
    make_listing(session, photo)
    assert has_own_photo(session, "l1") is True
    assert has_own_photo(session, "nope") is False


def test_listing_without_a_scan_falls_back_to_reference_art(client, session, media_root):
    make_listing(session, None)
    body = client.get("/shop/game-pokemon").text
    assert "images.pokemontcg.io" in body
    assert "Stock image" in body
    assert "/photo/l1" not in body


def test_a_listing_with_both_prefers_the_own_photo(client, session, media_root):
    """Reverted 2026-09-29 -- see common._art's docstring: the seller's
    own scan is back to winning over reference art now that a full
    backfill (scripts/backfill_inventory_photo_rotation.py) confirmed no
    already-committed photo is still saved wrong-orientation."""
    _, photo = media_root
    make_listing(session, photo)
    body = client.get("/shop/game-pokemon").text
    assert "/photo/l1" in body
    assert "Actual card" in body
    # the stock photo must not also be rendered -- two images would be shown
    assert "images.pokemontcg.io" not in body
    assert "Stock image" not in body


def test_item_page_prefers_the_own_photo_too(client, session, media_root):
    """The page's own social-preview meta tags may still reference the
    stock image URL for Open Graph purposes -- that's unrelated to _art()
    -- so this checks the actual rendered <img> tag, not the whole page."""
    _, photo = media_root
    make_listing(session, photo)
    body = client.get("/item/l1").text
    assert "<img src='/photo/l1?v=" in body and "large=true" in body
    assert "<img src='https://images.pokemontcg.io" not in body


def test_a_listing_with_only_a_scan_still_falls_back_to_it(client, session, media_root):
    """No reference image on file at all -- the real photo is still
    better than nothing."""
    _, photo = media_root
    item = CatalogItem(id="ci1", item_type=CatalogItemType.TCG_CARD, name="Gastrodon")
    tcg = TcgCard(catalog_item_id="ci1", game="pokemon", set_id="sts", number="29")
    inv = InventoryItem(id="inv1", catalog_item_id="ci1", quantity=1, scan_image_path=str(photo))
    listing = ShopListing(id="l1", catalog_item_id="ci1", inventory_item_id="inv1",
                          title="Gastrodon #29", price=Decimal("12.00"), currency="AUD",
                          quantity=1, status=ShopListingStatus.ACTIVE)
    session.add_all([item, tcg, inv, listing])
    session.commit()
    body = client.get("/item/l1").text
    assert "/photo/l1" in body and "Actual card" in body


def test_a_collectibles_own_photo_says_actual_item_not_actual_card(client, session, media_root):
    """Regression: a Sonny Angel figure isn't a card -- the badge was
    hardcoded to say "Actual card" regardless of item type."""
    _, photo = media_root
    item = CatalogItem(id="ci1", item_type=CatalogItemType.COLLECTIBLE, name="Skunk")
    inv = InventoryItem(id="inv1", catalog_item_id="ci1", quantity=1, scan_image_path=str(photo))
    listing = ShopListing(id="l1", catalog_item_id="ci1", inventory_item_id="inv1",
                          title="Skunk", price=Decimal("28.99"), currency="AUD",
                          quantity=1, status=ShopListingStatus.ACTIVE)
    session.add_all([item, inv, listing])
    session.commit()
    body = client.get("/item/l1").text
    assert "/photo/l1" in body
    assert "Actual item" in body
    assert "Actual card" not in body


# --- a second legitimate photo source: AI-captured collectibles ------------

@pytest.fixture
def captures_root(tmp_path, monkeypatch):
    """AI-assisted single-item captures (collectibles.py) land under
    captured_cards_dir, a genuinely different directory from the bulk
    scan pipeline's scan_media_dir -- both must be treated as real photo
    sources, not just the first one found."""
    from PIL import Image

    root = tmp_path / "captured_cards"
    root.mkdir()
    photo = root / "figure.jpg"
    Image.new("RGB", (400, 400), (10, 20, 30)).save(photo)

    settings = get_settings()
    monkeypatch.setattr(settings, "captured_cards_dir", str(root))
    monkeypatch.setattr("storefront.photos.get_settings", lambda: settings)
    return root, photo


def test_a_photo_under_captured_cards_dir_is_also_served(client, session, captures_root):
    _, photo = captures_root
    make_listing(session, photo)
    response = client.get("/photo/l1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")


def test_traversal_out_of_captured_cards_dir_is_still_refused(client, session, captures_root):
    root, _ = captures_root
    make_listing(session, f"{root}/../../../etc/passwd")
    assert client.get("/photo/l1").status_code == 404


def test_a_path_under_neither_root_is_refused(client, session, media_root, captures_root):
    _, photo = media_root
    other = photo.parent.parent / "elsewhere.jpg"
    from PIL import Image
    Image.new("RGB", (10, 10)).save(other)
    make_listing(session, other)
    assert client.get("/photo/l1").status_code == 404


# --- local cache paths in image_url must never render as <img src> ---------
#
# catalog_items.image_url and shop_listings.image_url turned out to hold a
# scan-ingest identification-cache path (scan_ingest_data/cache/<set>/<n>.png)
# for the vast majority of real TCG_CARD rows, not a hosted URL -- an
# artifact of whatever populates that field, not something this app wrote.
# Rendered directly it's a broken image; these lock in the fallback chain
# that avoids that: reject anything that isn't an absolute http(s) URL, then
# fall back to the catalog_cards reference cache (confirmed always hosted),
# then to the seller's own photo, then to nothing.

def make_card_listing(session, *, listing_image_url=None, item_image_url=None, photo_path=None):
    session.merge(CardSet(id="sts", name="Steam Siege"))
    item = CatalogItem(id="ci1", item_type=CatalogItemType.TCG_CARD, name="Gastrodon",
                       image_url=item_image_url)
    tcg = TcgCard(catalog_item_id="ci1", game="pokemon", set_id="sts", number="29")
    inv = InventoryItem(id="inv1", catalog_item_id="ci1", quantity=1,
                        scan_image_path=str(photo_path) if photo_path else None)
    listing = ShopListing(id="l1", catalog_item_id="ci1", inventory_item_id="inv1",
                          title="Gastrodon #29", price=Decimal("12.00"), currency="AUD",
                          quantity=1, status=ShopListingStatus.ACTIVE, image_url=listing_image_url)
    session.add_all([item, tcg, inv, listing])
    session.commit()
    return listing


def test_a_local_cache_path_in_image_url_is_never_rendered_as_an_img_src(client, session):
    make_card_listing(session, listing_image_url="scan_ingest_data/cache/sts/29.png",
                      item_image_url="scan_ingest_data/cache/sts/29.png")
    body = client.get("/item/l1").text
    assert "scan_ingest_data" not in body


def test_a_local_cache_path_falls_back_to_the_catalog_cards_reference_image(client, session):
    session.add(CatalogCard(id="sts-29", set_id="sts", number="29", name="Gastrodon",
                            image_url="https://images.pokemontcg.io/sts/29.png"))
    make_card_listing(session, listing_image_url="scan_ingest_data/cache/sts/29.png",
                      item_image_url="scan_ingest_data/cache/sts/29.png")
    body = client.get("/item/l1").text
    assert "images.pokemontcg.io/sts/29.png" in body
    assert "Stock image" in body


def test_a_local_cache_path_with_no_reference_card_falls_back_to_the_own_photo(client, session, media_root):
    _, photo = media_root
    make_card_listing(session, listing_image_url="scan_ingest_data/cache/sts/29.png",
                      item_image_url="scan_ingest_data/cache/sts/29.png", photo_path=photo)
    body = client.get("/item/l1").text
    assert "scan_ingest_data" not in body
    assert "/photo/l1" in body and "Actual card" in body


def test_a_real_hosted_url_is_still_used_directly_no_lookup_needed(client, session):
    make_card_listing(session, listing_image_url="https://images.pokemontcg.io/sts/29.png")
    body = client.get("/item/l1").text
    assert "images.pokemontcg.io/sts/29.png" in body


# --- professional photos ----------------------------------------------------

def _pro_pair(root):
    from PIL import Image

    front, back = root / "pro-front.jpg", root / "pro-back.jpg"
    Image.new("RGB", (1800, 2400), (10, 200, 30)).save(front)
    Image.new("RGB", (1800, 2400), (200, 10, 30)).save(back)
    return front, back


def test_professional_photo_wins_over_the_scan(client, session, media_root):
    root, scan = media_root
    make_listing(session, scan)
    front, back = _pro_pair(root)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()

    from storefront.photos import photo_count, photo_path_for_listing

    assert photo_path_for_listing(session, "l1") == front.resolve()
    assert photo_path_for_listing(session, "l1", index=1) == back.resolve()
    assert photo_count(session, "l1") == 2
    assert client.get("/photo/l1").status_code == 200
    assert client.get("/photo/l1?n=1").status_code == 200
    assert client.get("/photo/l1?n=2").status_code == 404


def test_large_photo_is_bigger_than_the_thumbnail(client, session, media_root):
    from io import BytesIO
    from PIL import Image

    root, scan = media_root
    make_listing(session, scan)
    front, back = _pro_pair(root)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()
    small = Image.open(BytesIO(client.get("/photo/l1").content)).width
    large = Image.open(BytesIO(client.get("/photo/l1?large=true").content)).width
    assert small == 420 and large == 900


def test_scan_is_still_used_when_there_is_no_professional_photo(client, session, media_root):
    root, scan = media_root
    make_listing(session, scan)
    assert has_own_photo(session, "l1")
    assert client.get("/photo/l1").status_code == 200


def test_professional_photo_outside_the_media_root_is_ignored(client, session, media_root, tmp_path):
    from PIL import Image

    root, scan = media_root
    make_listing(session, scan)
    outside = tmp_path / "outside.jpg"
    Image.new("RGB", (50, 50)).save(outside)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(outside), "/etc/passwd"]
    session.commit()
    from storefront.photos import photo_path_for_listing

    assert photo_path_for_listing(session, "l1") == scan.resolve()  # falls back to the scan


def test_item_page_shows_front_large_and_back(client, session, media_root):
    root, scan = media_root
    make_listing(session, scan)
    front, back = _pro_pair(root)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()
    body = client.get("/item/l1").text
    assert "large=true" in body and "n=1" in body and "photo-back" in body


def test_withdrawn_listing_serves_no_photo_even_with_professional_ones(client, session, media_root):
    root, scan = media_root
    make_listing(session, scan, status=ShopListingStatus.WITHDRAWN)
    front, back = _pro_pair(root)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()
    assert client.get("/photo/l1").status_code == 404


def _staged_photo(path, *, card_at=(450, 500, 1350, 1757)):
    """A busy 'card' on a smooth dark backdrop, like a photographed card."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(1)
    canvas = np.full((2400, 1800, 3), 45, dtype=np.uint8)
    x0, y0, x1, y1 = card_at
    blocks = rng.integers(0, 255, size=((y1 - y0) // 30 + 1, (x1 - x0) // 30 + 1, 3), dtype=np.uint8)
    canvas[y0:y1, x0:x1] = np.kron(blocks, np.ones((30, 30, 1), dtype=np.uint8))[: y1 - y0, : x1 - x0]
    Image.fromarray(canvas).save(path, quality=95)


def test_card_box_finds_the_card_on_a_backdrop(tmp_path):
    from PIL import Image
    from storefront.photos import card_box

    _staged_photo(tmp_path / "p.jpg")
    left, top, right, bottom = card_box(Image.open(tmp_path / "p.jpg"))
    assert abs(left - 450) < 80 and abs(right - 1350) < 80 and abs(top - 500) < 80
    assert 0.6 < (right - left) / (bottom - top) < 0.85


def test_card_box_gives_up_on_an_empty_backdrop(tmp_path):
    from PIL import Image
    from storefront.photos import card_box

    Image.new("RGB", (1800, 2400), (45, 45, 45)).save(tmp_path / "blank.jpg")
    assert card_box(Image.open(tmp_path / "blank.jpg")) is None


def test_front_is_cropped_to_the_card_but_the_back_is_not(client, session, media_root):
    from io import BytesIO
    from PIL import Image

    root, scan = media_root
    make_listing(session, scan)
    front, back = root / "f.jpg", root / "b.jpg"
    _staged_photo(front)
    Image.new("RGB", (1800, 2400), (45, 45, 45)).save(back)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()
    cropped = Image.open(BytesIO(client.get("/photo/l1?large=true").content))
    whole = Image.open(BytesIO(client.get("/photo/l1?n=1&large=true").content))
    assert cropped.width == 900 and cropped.height < 1300  # card-shaped, not the full 3:4 frame
    assert 0.6 < cropped.width / cropped.height < 0.85
    assert abs(whole.height / whole.width - 4 / 3) < 0.01  # back keeps the whole shot


def test_photo_urls_are_versioned_so_a_changed_photo_is_not_served_from_cache(client, session, media_root):
    import re

    root, scan = media_root
    make_listing(session, scan)
    before = re.search(r"/photo/l1\?v=[^'\"&]+", client.get("/browse").text).group(0)
    front, back = _pro_pair(root)
    session.get(ShopListing, "l1").ebay_photo_paths = [str(front), str(back)]
    session.commit()
    after = re.search(r"/photo/l1\?v=[^'\"&]+", client.get("/browse").text).group(0)
    assert before != after  # new photo -> new URL -> browsers refetch
    assert "max-age=604800" in client.get(after).headers["cache-control"]
    assert "max-age=3600" in client.get("/photo/l1").headers["cache-control"]  # unversioned: short
