import io
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import imagehash
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from automation_control import review_queue
from automation_control.api import app
from automation_control.auth import require_dashboard_user
from automation_control.card_recognition import ExtractedCardDetailsScreenshot
from automation_control.database import Base, get_session
from automation_control.models import (
    CardPrice,
    CardSet,
    CardVariant,
    CatalogCard,
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    ScanSession,
    TcgCard,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def media(tmp_path, monkeypatch):
    """Point the app's media dir at a temp tree so the real queue is never
    touched by a test run."""
    root = tmp_path / "media"
    (root / "needs_review").mkdir(parents=True)
    settings = type("S", (), {"scan_media_dir": str(root), "scan_phash_max_distance": 12})()
    monkeypatch.setattr(review_queue, "get_settings", lambda: settings)
    return root


@pytest.fixture
def client(session, media):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_dashboard_user] = lambda: "testuser"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _art(seed: int, size=(245, 342)) -> Image.Image:
    import numpy as np

    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))


def _queue(media: Path, name: str, seed: int = 1) -> Path:
    path = media / "needs_review" / name
    _art(seed).save(path)
    return path


def test_listing_title_code_parser():
    assert review_queue._codes_from_listing_title(
        "Son Goku SH1-29 2016 Super Dragon Ball Heroes Card Holo Japanese Rare BANDAI"
    ) == ["SH1-29"]


def test_listing_title_autofill_matches_cached_card(client, session):
    session.add(CardSet(id="sdbh-sh1", name="Super Dragon Ball Heroes SH1", game="super_dragon_ball_heroes"))
    session.add(CatalogCard(id="sdbh-sh1-29", set_id="sdbh-sh1", number="SH1-29", name="SUPER DRAGON BALL HEROES SH1-29", rarity="UR"))
    session.add(CatalogItem(id="sdbh-sh1-29", item_type=CatalogItemType.TCG_CARD, name="SUPER DRAGON BALL HEROES SH1-29"))
    session.add(TcgCard(
        catalog_item_id="sdbh-sh1-29", game="super_dragon_ball_heroes", set_id="sdbh-sh1",
        set_code="SH1", number="SH1-29", character="Son Goku", rarity="UR", language="Japanese",
    ))
    session.commit()

    response = client.post(
        "/review/parse-listing-title",
        data={"title": "Son Goku SH1-29 2016 Super Dragon Ball Heroes Card Holo Japanese Rare BANDAI"},
    )
    assert response.status_code == 200
    assert response.json()["catalog_item_id"] == "sdbh-sh1-29"
    assert response.json()["name"] == "Son Goku"
    assert response.json()["language"] == "Japanese"
    assert response.json()["apply_fields"] == ["name"]

    seller_first = client.post(
        "/review/parse-listing-title",
        data={"title": "JAPAN BANDAI DRAGONBALL HEROES Card SH1-29 Super Saiyan God SS SON GOKU"},
    )
    assert seller_first.status_code == 200
    assert seller_first.json()["name"] == "SUPER DRAGON BALL HEROES SH1-29"
    assert seller_first.json()["apply_fields"] == []


def _cache_card(session, tmp_path, *, set_id="xy11", set_name="Steam Siege", number="31", name="Dewott", seed=1,
                printed_total=114, rarity=None, card_id=None):
    if session.get(CardSet, set_id) is None:
        # cached_at matters: only sets marked cached are matched against.
        session.add(CardSet(id=set_id, name=set_name, printed_total=printed_total,
                            cached_at=datetime.now(UTC)))
    card_id = card_id or f"{set_id}-{number}"
    ref = tmp_path / f"{card_id}.png"
    _art(seed).save(ref)
    # Both hashes, matching what catalog._compute_phashes stores.
    from automation_control.scan_ingest.identify import art_phash as _art_phash
    with Image.open(ref) as image:
        rgb = image.convert("RGB")
        phash = str(imagehash.phash(rgb))
        art = str(_art_phash(rgb))
    session.add(CatalogCard(id=card_id, set_id=set_id, number=number, name=name,
                            local_image_path=str(ref), phash=phash, art_phash=art, rarity=rarity))
    session.add(CatalogItem(id=card_id, item_type=CatalogItemType.TCG_CARD, name=name))
    session.commit()
    return card_id


# --- the page --------------------------------------------------------------

def test_review_requires_auth(media):
    app.dependency_overrides.clear()
    assert TestClient(app).get("/review").status_code in (401, 403, 307, 302)


def test_empty_queue_says_so(client):
    resp = client.get("/review")
    assert resp.status_code == 200
    assert "Queue is empty" in resp.text


def test_queue_lists_each_crop_with_its_image(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=1)
    resp = client.get("/review")
    assert "sheet-a-card1-unidentified.jpg" in resp.text
    assert "/review/image/sheet-a-card1-unidentified.jpg" in resp.text


def test_queue_shows_the_matcher_s_suggestion(client, media, session, tmp_path):
    """A crop identical to a cached card's art should be suggested as that
    card, so the operator's usual action is a single click."""
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.get("/review")
    assert "Dewott" in resp.text
    assert "name='number' value='31'" in resp.text


def test_exact_bandai_code_is_suggested_even_when_foil_art_hash_is_outside_limit(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path, set_id="op-1", set_name="One Piece",
        number="OP09-046", name="Crocodile", seed=1,
    )
    session.get(CardSet, "op-1").game = "one_piece"
    session.add(TcgCard(
        catalog_item_id=card_id, game="one_piece", set_id="op-1",
        number="OP09-046", character="Crocodile", language="English",
    ))
    session.commit()
    # Deliberately unrelated pixels: the exact printed code retained in the
    # filename must beat the pHash cutoff, while still leaving base/parallel
    # choice to the artwork ranking if more than one shares that code.
    _queue(media, "sheet-a-card1-OP09-046-Crocodile.jpg", seed=99)

    resp = client.get("/review")

    assert "Crocodile" in resp.text
    assert "name='number' value='OP09-046'" in resp.text


def test_queue_shows_name_set_rarity_and_cost_for_the_top_match(client, media, session, tmp_path):
    """The matched record is a real editor, prefilled from the catalogue."""
    _cache_card(session, tmp_path, set_id="xy11", number="31", name="Dewott", seed=7, rarity="Uncommon")
    session.add(CardPrice(catalog_item_id="xy11-31", source="ebay_active_low", price="4.20", currency="AUD"))
    session.commit()
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")
    assert "name='set_id' required" in resp.text
    assert "name='name' value='Dewott'" in resp.text
    assert "name='number' value='31'" in resp.text
    assert "name='rarity' value='Uncommon'" in resp.text
    assert "name='price' type='number' step='0.01' min='0.01' value='4.20'" in resp.text
    assert "name='language' value='English'" in resp.text
    assert "name='condition'" in resp.text
    assert "Confirm" in resp.text


def test_queue_shows_ebay_listing_prices_and_bulk_checkboxes(client, media, session, tmp_path):
    _cache_card(session, tmp_path, set_id="xy11", number="31", name="Dewott", seed=7)
    session.add_all([
        CardPrice(catalog_item_id="xy11-31", source="ebay_active_low", price="4.20", currency="AUD"),
        CardPrice(catalog_item_id="xy11-31", source="ebay_active_median", price="9.50", currency="AUD"),
    ])
    session.commit()
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")
    assert "eBay AU active listings: low <b>$4.20</b>" in resp.text
    assert "median <b>$9.50</b>" in resp.text
    assert "class='bulk-check'" in resp.text
    assert "id='bulk-save'" in resp.text


def test_queue_shows_an_empty_price_field_with_no_price_on_file(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")
    assert "name='price' type='number' step='0.01' min='0.01' value=''" in resp.text


def test_queue_offers_a_details_screenshot_that_is_read_then_discarded(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    page = client.get("/review").text

    assert "Add a details screenshot" in page
    assert "PriceCharting or similar product page" in page
    assert "reads it locally" in page
    assert "configured recognition service" in page
    assert "then the screenshot is discarded" in page
    assert "Detect current card &mdash; local/free first" in page
    assert "Ask Claude for a second opinion" in page
    assert "/review/detect-crop" in page
    assert "/review/claude-suggest" in page
    assert "class='details-shot-file'" in page
    assert "/review/details-screenshot" in page


def test_detect_current_scan_uses_printed_total_and_name_without_api_credits(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path, set_id="swsh2", set_name="Rebel Clash", printed_total=192,
        number="15", name="Trevenant", rarity="Rare", card_id="swsh2-15", seed=14,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    ocr = "Trevenant\nEvolves from Phantump\nSeed Bomb\n015/192\n"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: ocr)
        resp = client.post("/review/detect-crop", data={"crop": crop.name})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["catalog_item_id"] == card_id
    assert payload["set_id"] == "swsh2"
    assert payload["set_name"] == "Rebel Clash"
    assert payload["name"] == "Trevenant"
    assert payload["number"] == "15"
    assert payload["rarity"] == "Rare"
    assert payload["price"] == ""
    assert "locally" in payload["warnings"][0]


def test_detect_current_scan_falls_back_to_ai_card_text_reader(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path, set_id="dbs-24359", set_name="Premium Anniversary Box 2025",
        number="EX25-18", name="Sealing in a Jar", rarity="Extra",
        card_id="dragon-ball-super-ex25-18", seed=14,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = "test-key"
    extracted = review_queue.ExtractedCard(
        character="Sealing in a Jar", set_name="Premium Anniversary Box 2025",
        card_number="EX25-18", rarity="Extra", language="English", graded="Raw",
        unreadable_fields=[],
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue, "_extract_scan_crop_with_local_ocr", lambda *_args: None)
        mp.setattr(review_queue, "extract_card_details", lambda *_args, **_kwargs: [extracted])
        resp = client.post("/review/detect-crop", data={"crop": crop.name})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == card_id
    assert payload["number"] == "EX25-18"
    assert payload["source_site"] == "AI reading of current scan"
    assert "verify the printed code" in payload["warnings"][-1]


def test_ask_claude_returns_a_review_only_candidate_without_committing(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path, set_id="dbs-24359", set_name="Premium Anniversary Box 2025",
        number="EX25-18", name="Sealing in a Jar", rarity="Extra",
        card_id="dragon-ball-super-ex25-18", seed=14,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = "test-key"
    extracted = review_queue.ExtractedCard(
        character="Sealing in a Jar", set_name="Premium Anniversary Box 2025",
        card_number="EX25-18", rarity="Extra", language="English", graded="Raw",
        unreadable_fields=[],
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue, "extract_card_details", lambda *_args, **_kwargs: [extracted])
        response = client.post("/review/claude-suggest", data={"crop": crop.name})

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == card_id
    assert payload["source_site"] == "Claude vision suggestion"
    assert "Claude read" in payload["reasoning"]
    assert "nothing has been saved" in payload["warnings"][-1]
    assert crop.exists()
    assert session.query(InventoryItem).count() == 0


def test_ask_claude_requires_a_configured_key(client, media):
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = None

    response = client.post("/review/claude-suggest", data={"crop": crop.name})

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_detect_current_scan_falls_back_to_lens_when_ai_is_unavailable(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path, set_id="dbs-24359", set_name="Premium Anniversary Box 2025",
        number="EX25-18", name="Sealing in a Jar", rarity="Extra",
        card_id="dragon-ball-super-ex25-18", seed=14,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    settings = review_queue.get_settings()
    settings.anthropic_api_key = "out-of-credit-key"
    settings.apify_token = "lens-key"

    def unavailable(*_args, **_kwargs):
        raise review_queue.RecognitionError("recognition provider request failed")

    async def lens(*_args, **_kwargs):
        return [{"title": "Sealing in a Jar EX25-18 Dragon Ball Super", "link": "https://example.test/card"}]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue, "_extract_scan_crop_with_local_ocr", lambda *_args: None)
        mp.setattr(review_queue, "extract_card_details", unavailable)
        mp.setattr(review_queue, "search_image", lens)
        resp = client.post("/review/detect-crop", data={"crop": crop.name})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == card_id
    assert payload["source_site"] == "Google Lens reverse search"
    assert "Google Lens was used" in payload["warnings"][-1]


def test_detect_current_scan_uses_reference_art_to_select_a_special_stamped_reprint(
    client, media, session, tmp_path,
):
    _cache_card(
        session, tmp_path, set_id="swsh2", set_name="Rebel Clash", printed_total=192,
        number="15", name="Trevenant", rarity="Rare", card_id="swsh2-15", seed=14,
    )
    special_id = _cache_card(
        session, tmp_path, set_id="pkm-3179", set_name="Trick or Trade BOOster Bundle",
        printed_total=None, number="015/192", name="Trevenant", rarity="Promo",
        card_id="pokemon-ttbb-015-192", seed=21,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=21)
    ocr = "Trevenant\nEvolves from Phantump\nSeed Bomb\n015/192\n"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: ocr)
        resp = client.post("/review/detect-crop", data={"crop": crop.name})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["catalog_item_id"] == special_id
    assert payload["set_id"] == "pkm-3179"
    assert payload["set_name"] == "Trick or Trade BOOster Bundle"
    assert payload["number"] == "015/192"
    assert payload["rarity"] == "Promo"


def test_detect_current_scan_uses_strong_phash_for_card_without_printed_total(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path,
        set_id="db-carddass-hondan-12",
        set_name="Dragon Ball Carddass Hondan Part 12",
        printed_total=None,
        number="480",
        name="Krillin",
        rarity="Normal",
        card_id="db-carddass-hondan-12-480",
        seed=12,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=12)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: "Krillin No.480")
        resp = client.post("/review/detect-crop", data={"crop": crop.name})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["catalog_item_id"] == card_id
    assert payload["set_id"] == "db-carddass-hondan-12"
    assert payload["number"] == "480"
    assert payload["source_site"] == "local scan reference image"


def test_details_screenshot_prefills_the_exact_catalogue_card_and_ungraded_aud_price(
    client, media, session, tmp_path,
):
    base_id = _cache_card(
        session, tmp_path, set_id="dbs-2755", set_name="Vicious Rejuvenation",
        number="BT12-136", name="SS3 Gogeta, Marvelous Might", rarity="Super Rare",
        card_id="dragon-ball-super-bt12-136", seed=11,
    )
    _cache_card(
        session, tmp_path, set_id="dbs-2755", set_name="Vicious Rejuvenation",
        number="BT12-136", name="SS3 Gogeta, Marvelous Might (SPR)", rarity="Special Rare",
        card_id="tcgp-229452", seed=12,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = "test-key"
    extracted = ExtractedCardDetailsScreenshot(
        card_name="SS3 Gogeta, Marvelous Might",
        set_name="Dragon Ball Super Vicious Rejuvenation",
        card_number="BT12-136",
        rarity="",
        language="",
        ungraded_price="1.06",
        currency="AUD",
        source_site="pricecharting.com",
        unreadable_fields=["rarity", "language"],
    )
    upload = io.BytesIO()
    _art(123, size=(300, 600)).save(upload, format="JPEG")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue, "extract_card_details_screenshot", lambda *_args, **_kwargs: extracted)
        resp = client.post(
            "/review/details-screenshot",
            data={"crop": crop.name},
            files={"screenshot": ("details.jpg", upload.getvalue(), "image/jpeg")},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == base_id
    assert payload["set_id"] == "dbs-2755"
    assert payload["name"] == "SS3 Gogeta, Marvelous Might"
    assert payload["number"] == "BT12-136"
    assert payload["rarity"] == "Super Rare"
    assert payload["price"] == "1.06"
    assert payload["currency"] == "AUD"
    assert payload["apply_fields"] == ["price"]


def test_details_screenshot_uses_local_ocr_without_spending_external_credits(
    client, media, session, tmp_path,
):
    base_id = _cache_card(
        session, tmp_path, set_id="dbs-2755", set_name="Vicious Rejuvenation",
        number="BT12-136", name="SS3 Gogeta, Marvelous Might", rarity="Super Rare",
        card_id="dragon-ball-super-bt12-136", seed=11,
    )
    _cache_card(
        session, tmp_path, set_id="dbs-2755", set_name="Vicious Rejuvenation",
        number="BT12-136", name="SS3 Gogeta, Marvelous Might (SPR)", rarity="Special Rare",
        card_id="tcgp-229452", seed=12,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = None
    ocr = """pricecharting.com
Trading Cards > Dragon Ball Cards > Dragon Ball Super Vicious Rejuvenation
SS3 Gogeta, Marvelous Might BT12-136 Dragon Ball Super Vicious Rejuvenation
Vicious Rejuvenation $3.00  SS3 Gogeta, Marvelous Might $9.99
Ungraded Grade 7 Grade 8
A1.06 - -
Grade 9 Grade 9.5 PSA 10 A54.91
"""
    upload = io.BytesIO()
    _art(123, size=(300, 600)).save(upload, format="JPEG")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: ocr)
        mp.setattr(
            review_queue, "extract_card_details_screenshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("external provider must not be called")),
        )
        resp = client.post(
            "/review/details-screenshot",
            data={"crop": crop.name},
            files={"screenshot": ("details.jpg", upload.getvalue(), "image/jpeg")},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["catalog_item_id"] == base_id
    assert payload["price"] == "1.06"
    assert payload["source_site"] == "pricecharting.com"


def test_ebay_carddass_listing_matches_no_number_and_misspelled_name_without_credits(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path,
        set_id="db-carddass-hondan-12",
        set_name="Dragon Ball Carddass Hondan Part 12",
        printed_total=None,
        number="480",
        name="Krillin",
        rarity="Normal",
        card_id="db-carddass-hondan-12-480",
        seed=12,
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = None
    # Real listing title: it spells the character "Kririn", uses No.480,
    # and omits the formal Hondan Part 12 set title.
    ocr = """ebay.com
Kririn Dragon Ball Z CARDDASS TCG Card BANDAI Japan Vintage F/S No.480
$10.00
Condition Used
"""
    upload = io.BytesIO()
    _art(123, size=(300, 600)).save(upload, format="JPEG")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: ocr)
        mp.setattr(
            review_queue, "extract_card_details_screenshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("external provider must not be called")),
        )
        resp = client.post(
            "/review/details-screenshot",
            data={"crop": crop.name},
            files={"screenshot": ("details.jpg", upload.getvalue(), "image/jpeg")},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == card_id
    assert payload["set_id"] == "db-carddass-hondan-12"
    assert payload["name"] == "Krillin"
    assert payload["number"] == "480"
    assert payload["rarity"] == "Normal"
    # An active USD asking price is not silently copied into an AUD market
    # value field.
    assert payload["price"] == ""
    assert payload["source_site"] == "ebay.com"


def test_details_screenshot_falls_back_to_exact_queued_crop_without_credits(
    client, media, session, tmp_path,
):
    card_id = _cache_card(
        session, tmp_path,
        set_id="dbha10-unofficial",
        set_name="Dragon Ball Heroes Inspired Art Cards DBHA10 (Unofficial)",
        printed_total=None,
        number="DBHA10-08",
        name="Turles",
        rarity="Unofficial Art Card",
        card_id="dbha10-unofficial-08",
        seed=22,
    )
    crop = _queue(media, "sheet-a-card2-unidentified.jpg", seed=22)
    review_queue.get_settings().anthropic_api_key = None
    ocr = """ebay.com
Dragon Ball Heroes Art Cards, Anniversary Edition HR Turles
US $16.50 approx. AU $23.19
Condition Ungraded - Near mint or better
"""
    upload = io.BytesIO()
    _art(123, size=(300, 600)).save(upload, format="JPEG")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(review_queue.pytesseract, "image_to_string", lambda *_args, **_kwargs: ocr)
        mp.setattr(
            review_queue, "extract_card_details_screenshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("external provider must not be called")),
        )
        resp = client.post(
            "/review/details-screenshot",
            data={"crop": crop.name},
            files={"screenshot": ("details.jpg", upload.getvalue(), "image/jpeg")},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["matched"] is True
    assert payload["catalog_item_id"] == card_id
    assert payload["set_id"] == "dbha10-unofficial"
    assert payload["number"] == "DBHA10-08"
    assert payload["price"] == ""
    assert "queued scan" in payload["warnings"][0]


def test_details_screenshot_does_not_put_a_foreign_price_in_the_aud_field(session):
    extracted = ExtractedCardDetailsScreenshot(
        card_name="Pikachu", set_name="Base Set", card_number="58", rarity="Common",
        language="English", ungraded_price="12.00", currency="USD",
        source_site="example.test", unreadable_fields=[],
    )

    payload = review_queue._screenshot_details_payload(session, extracted)

    assert payload["price"] == ""
    assert "USD" in payload["warnings"][0]


def test_details_screenshot_accepts_pricecharting_s_australian_price_marker():
    assert review_queue._screenshot_price("A 1.06", "A") == ("1.06", None)


def test_provider_credit_error_is_explained_instead_of_hidden():
    assert "credits are exhausted" in review_queue._provider_failure_message(
        RuntimeError("Your credit balance is too low to access the Anthropic API")
    )


def test_details_screenshot_rejects_a_non_image(client, media):
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=90)
    review_queue.get_settings().anthropic_api_key = "test-key"

    resp = client.post(
        "/review/details-screenshot",
        data={"crop": crop.name},
        files={"screenshot": ("details.jpg", b"not an image", "image/jpeg")},
    )

    assert resp.status_code == 400
    assert "not a readable image" in resp.json()["detail"]


def test_queue_says_so_when_nothing_matches(client, media, session, tmp_path):
    _cache_card(session, tmp_path, seed=3)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=999)
    resp = client.get("/review")
    assert "No confident match" in resp.text


def test_queue_paginates(client, media, session, tmp_path):
    """One card per page, so N queued crops means N pages."""
    _cache_card(session, tmp_path)
    count = review_queue.PAGE_SIZE * 3
    for n in range(count):
        _queue(media, f"sheet-a-card{n}-unidentified.jpg", seed=n + 20)

    first = client.get("/review")
    assert "Page 1 of 3" in first.text
    assert client.get("/review?page=3").status_code == 200


def test_a_batch_of_cards_is_shown_per_page(client, media, session, tmp_path):
    """A responsive grid, not one card taking the whole screen -- collapses
    to a single column on a narrow phone via CSS (no server-side branching
    needed), several side by side on anything wider."""
    _cache_card(session, tmp_path)
    for n in range(4):
        _queue(media, f"sheet-a-card{n}-unidentified.jpg", seed=n + 40)

    resp = client.get("/review")

    assert resp.text.count("class='review-card'") == min(4, review_queue.PAGE_SIZE)


def test_cards_are_numbered_within_their_page(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    for n in range(3):
        _queue(media, f"sheet-a-card{n}-unidentified.jpg", seed=n + 40)

    resp = client.get("/review")

    for position in range(1, 4):
        assert f"#{position}</div>" in resp.text


# --- decisions -------------------------------------------------------------

def test_accept_commits_the_card_and_clears_the_crop(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                               "set_id": "xy11", "number": "31"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert not crop.exists()
    item = session.scalars(select(InventoryItem)).one()
    assert item.card_id == "xy11-31"


def test_accept_records_a_scan_session(client, media, session, tmp_path):
    """Committing through the web must leave the same trail as the CLI, so
    the card is attributable to a session rather than appearing from nowhere."""
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31"}, follow_redirects=False)

    scan_session = session.scalars(select(ScanSession)).one()
    assert scan_session.set_id == "xy11"
    assert session.scalars(select(InventoryItem)).one().session_id == scan_session.id


def test_accept_with_a_corrected_number_commits_that_card(client, media, session, tmp_path):
    """The suggestion is only a suggestion -- a typed number wins."""
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    _cache_card(session, tmp_path, number="32", name="Samurott", seed=8)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "32"}, follow_redirects=False)

    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-32"


def test_accept_with_a_rarity_override_updates_the_catalogue_card(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7, rarity="Common")
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31", "rarity": "Uncommon"},
               follow_redirects=False)

    assert session.get(CatalogCard, "xy11-31").rarity == "Uncommon"


def test_accept_with_a_cost_override_adds_a_manual_price(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31", "cost": "9.99"},
               follow_redirects=False)

    price = session.scalars(select(CardPrice).where(CardPrice.catalog_item_id == "xy11-31")).one()
    assert price.source == "manual"
    assert Decimal(str(price.price)) == Decimal("9.99")


def test_accept_leaving_cost_unchanged_does_not_add_a_redundant_manual_row(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7)
    session.add(CardPrice(catalog_item_id="xy11-31", source="manual", price="9.99", currency="AUD"))
    session.commit()
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31", "cost": "9.99"},
               follow_redirects=False)

    assert session.scalars(select(CardPrice).where(CardPrice.catalog_item_id == "xy11-31")).all().__len__() == 1


def test_accept_with_no_rarity_or_cost_fields_leaves_the_catalogue_untouched(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7, rarity="Common")
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                        "set_id": "xy11", "number": "31"}, follow_redirects=False)

    assert session.get(CatalogCard, "xy11-31").rarity == "Common"
    assert session.scalars(select(CardPrice).where(CardPrice.catalog_item_id == "xy11-31")).all() == []


def test_accept_rejects_a_number_not_in_the_set(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "accept",
                                               "set_id": "xy11", "number": "999"}, follow_redirects=False)

    assert resp.status_code == 400
    assert crop.exists()  # nothing lost on a bad request
    assert session.scalars(select(InventoryItem)).all() == []


def test_discard_removes_the_crop_without_committing(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "action": "discard"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert not crop.exists()
    assert session.scalars(select(InventoryItem)).all() == []


def test_a_decision_from_a_stale_tab_is_an_idempotent_redirect(client):
    resp = client.post(
        "/review/decide",
        data={"crop": "sheet-already-done-card1-unidentified.jpg", "action": "discard"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/review?notice=already-processed"


def test_unknown_action_is_rejected(client, media, session, tmp_path):
    _cache_card(session, tmp_path)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.post("/review/decide", data={"crop": crop.name, "action": "launch"}, follow_redirects=False)
    assert resp.status_code == 400
    assert crop.exists()


# --- path safety -----------------------------------------------------------

def test_crop_name_cannot_escape_the_review_directory(client, media, session, tmp_path):
    """The crop name arrives from a form field, so a traversal attempt must
    not reach a file outside the queue -- deleting or serving arbitrary
    files would otherwise be one POST away."""
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"not yours")

    for name in ("../secret.jpg", "../../secret.jpg", "/etc/hostname"):
        assert client.post("/review/decide", data={"crop": name, "action": "discard"},
                           follow_redirects=False).status_code == 404
        assert client.get(f"/review/image/{name}").status_code in (404, 307, 404)

    assert secret.exists()


def test_image_route_serves_a_queued_crop(client, media, session, tmp_path):
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)
    resp = client.get(f"/review/image/{crop.name}")
    assert resp.status_code == 200
    assert resp.content == crop.read_bytes()


def test_queue_offers_several_candidates_not_one(client, media, session, tmp_path):
    """Across a large catalogue an unrelated card can hash closer than the
    right one (measured: a Gothorita at distance 6 beating the correct
    Flabebe at 8). A single confident-looking wrong answer is worse than a
    short list, so alternatives are offered.

    Three cards are given the *same* art so they all land inside the
    distance limit. Synthesising art that is merely similar is unreliable --
    pHash is chaotic on synthetic images, and even small edits jump 20+ --
    and what's under test here is candidate selection, not hashing.
    """
    for number in ("31", "32", "33"):
        _cache_card(session, tmp_path, number=number, name=f"Card{number}", seed=7)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")

    assert resp.text.count("data-card-id=") == 3
    for number in ("31", "32", "33"):
        assert f"Card{number}" in resp.text


def test_picking_a_candidate_commits_that_card(client, media, session, tmp_path):
    """A pick carries its own set, so choosing a card from a set other than
    the top match takes one click rather than a retype."""
    _cache_card(session, tmp_path, set_id="xy11", number="31", name="Dewott", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "pick": "xy11:31"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-31"
    assert not crop.exists()


def test_exact_pick_does_not_confuse_an_alt_art_with_the_base_card(client, media, session, tmp_path):
    """Set+number is not an identity: alternate arts and parallels often
    reuse both. New review buttons carry the catalogue id as well."""
    _cache_card(
        session, tmp_path, set_id="xy11", number="31", name="Dewott",
        seed=7, card_id="xy11-31-base",
    )
    _cache_card(
        session, tmp_path, set_id="xy11", number="31", name="Dewott (Alternate Art)",
        seed=8, card_id="xy11-31-alt",
    )
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=8)

    resp = client.post(
        "/review/decide",
        data={"crop": crop.name, "pick": "xy11|xy11-31-alt"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-31-alt"


def test_ambiguous_typed_number_requires_an_exact_card_choice(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Base", seed=7, card_id="xy11-base")
    _cache_card(session, tmp_path, number="31", name="Alt", seed=8, card_id="xy11-alt")
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post(
        "/review/decide",
        data={"crop": crop.name, "action": "accept", "set_id": "xy11", "number": "31"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "more than one card" in resp.json()["detail"]
    assert crop.exists()


def test_a_pick_for_an_uncached_set_is_rejected(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide", data={"crop": crop.name, "pick": "nope:1"},
                       follow_redirects=False)

    assert resp.status_code == 400
    assert crop.exists()


def test_review_edits_sync_the_scan_and_crm_catalogues(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", name="Old name", seed=7, rarity="Common")
    session.add(TcgCard(
        catalog_item_id="xy11-31", game="pokemon", set_id="xy11", number="31",
        character="Old name", rarity="Common", language="English",
    ))
    session.commit()
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post(
        "/review/decide",
        data={
            "crop": crop.name,
            "action": "accept",
            "set_id": "xy11",
            "number": "31",
            "selected_card_id": "xy11-31",
            "details_card_id": "xy11-31",
            "review_form_version": "2",
            "dirty_fields": "name,rarity,language,price",
            "name": "Dewott",
            "rarity": "Uncommon",
            "language": "Japanese",
            "price": "12.50",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert session.get(CatalogCard, "xy11-31").name == "Dewott"
    assert session.get(CatalogCard, "xy11-31").rarity == "Uncommon"
    assert session.get(CatalogItem, "xy11-31").name == "Dewott"
    assert session.get(TcgCard, "xy11-31").rarity == "Uncommon"
    assert session.get(TcgCard, "xy11-31").language == "Japanese"
    price = session.scalars(select(CardPrice).where(CardPrice.catalog_item_id == "xy11-31")).one()
    assert Decimal(str(price.price)) == Decimal("12.50")


def test_review_can_record_condition(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post(
        "/review/decide",
        data={
            "crop": crop.name, "action": "accept", "set_id": "xy11",
            "number": "31", "condition": "Lightly Played",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().condition == "Lightly Played"


# --- typing the number as printed ------------------------------------------

def test_number_typed_as_printed_identifies_the_set(client, media, session, tmp_path):
    """The suggestions can all be wrong, and then the typed number would
    otherwise land in whichever set the top match happened to be in. The
    printed total names the set, so '31/114' is unambiguous."""
    _cache_card(session, tmp_path, set_id="xy11", number="31", name="Dewott", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "action": "accept", "set_id": "", "number": "31/114"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-31"


def test_printed_total_overrides_a_wrong_suggested_set(client, media, session, tmp_path):
    """The exact reported failure: every suggestion was from the wrong set,
    so typing the number alone produced 'no card #166 in ex14'."""
    _cache_card(session, tmp_path, set_id="xy11", number="166", name="Eevee", seed=7, printed_total=236)
    _cache_card(session, tmp_path, set_id="other", number="1", name="Decoy", seed=9, printed_total=99)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "action": "accept",
                             "set_id": "other", "number": "166/236"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().card_id == "xy11-166"


def test_an_uncached_printed_total_says_so(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "action": "accept", "number": "5/999"},
                       follow_redirects=False)

    assert resp.status_code == 400
    assert "999 printed cards" in resp.json()["detail"]
    assert crop.exists()


def test_a_malformed_printed_total_is_reported_clearly(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "action": "accept", "number": "31/abc"},
                       follow_redirects=False)

    assert resp.status_code == 400
    assert "166/236" in resp.json()["detail"]
    assert crop.exists()


# --- the artwork signal (reverse holos) ------------------------------------

def _holo_like(base_seed: int, size=(245, 342)) -> Image.Image:
    """A card whose artwork window matches `base_seed` but whose surround is
    scrambled -- the shape a reverse holo takes against flat reference art,
    where foil covers everything except the artwork."""
    import numpy as np

    from automation_control.scan_ingest.identify import ART_REGION

    base = np.array(_art(base_seed, size))
    noise = np.array(_art(base_seed + 5000, size))
    w, h = size
    x0, y0, x1, y1 = ART_REGION
    out = noise.copy()
    out[int(h * y0):int(h * y1), int(w * x0):int(w * x1)] = \
        base[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
    return Image.fromarray(out)


def test_a_card_only_the_artwork_matches_is_still_offered(client, media, session, tmp_path):
    """Reverse holos barely resemble their reference anywhere but the
    artwork window. Measured on real scans: a Cosmic Eclipse Pancham sat at
    whole-card distance 14 and rank 3, behind an unrelated Wobbuffet, but
    at artwork distance 6 and rank 1."""
    _cache_card(session, tmp_path, set_id="xy11", number="31", name="Holo Target", seed=3300)
    crop = media / "needs_review" / "sheet-a-card1-unidentified.jpg"
    _holo_like(3300).save(crop)

    resp = client.get("/review")

    assert "Holo Target" in resp.text, "artwork-only match was not offered"


def test_art_hash_is_confined_to_the_artwork_window(tmp_path):
    """Changing only the surround must not change the artwork hash, which
    is the whole reason it survives foil."""
    from automation_control.scan_ingest.identify import art_phash

    plain = _art(11)
    holo = _holo_like(11)
    assert art_phash(plain) == art_phash(holo)
    assert imagehash.phash(plain) != imagehash.phash(holo)


# --- rarity and printing ---------------------------------------------------

def test_candidates_show_rarity(client, media, session, tmp_path):
    """'Common' vs 'Rare Holo' is often the quickest way to tell two
    similar-looking candidates apart."""
    _cache_card(session, tmp_path, number="31", name="Dewott", seed=7, rarity="Rare Holo")
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")

    assert "Rare Holo" in resp.text


def test_printing_can_be_chosen(client, media, session, tmp_path):
    _cache_card(session, tmp_path, number="31", seed=7)
    _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.get("/review")

    assert "name='variant'" in resp.text
    assert "Reverse Holo" in resp.text


def test_accepting_records_the_chosen_printing(client, media, session, tmp_path):
    """Foil isn't reliably detectable from a flatbed scan -- it mostly reads
    as glare -- so it has to be stated, and it matters: a reverse holo is a
    different holding at a different price."""
    _cache_card(session, tmp_path, set_id="xy11", number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "pick": "xy11:31", "variant": "reverse_holo"},
                       follow_redirects=False)

    assert resp.status_code == 303
    assert session.scalars(select(InventoryItem)).one().variant is CardVariant.REVERSE_HOLO


def test_printing_defaults_to_normal_when_not_sent(client, media, session, tmp_path):
    _cache_card(session, tmp_path, set_id="xy11", number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    client.post("/review/decide", data={"crop": crop.name, "pick": "xy11:31"},
                follow_redirects=False)

    assert session.scalars(select(InventoryItem)).one().variant is CardVariant.NORMAL


def test_an_unknown_printing_is_rejected(client, media, session, tmp_path):
    _cache_card(session, tmp_path, set_id="xy11", number="31", seed=7)
    crop = _queue(media, "sheet-a-card1-unidentified.jpg", seed=7)

    resp = client.post("/review/decide",
                       data={"crop": crop.name, "pick": "xy11:31", "variant": "sparkly"},
                       follow_redirects=False)

    assert resp.status_code == 400
    assert crop.exists()
