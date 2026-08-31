from unittest.mock import patch

import httpx
import imagehash
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automation_control.adapters import pokemontcg_catalog
from automation_control.adapters.pokemontcg_catalog import CatalogLookupError
from automation_control.database import Base
from automation_control.models import CardSet, CatalogCard
from automation_control.scan_ingest import catalog

SET_PAYLOAD = {
    "id": "xy11", "name": "Steam Siege", "series": "XY", "releaseDate": "2016/08/03",
    "printedTotal": 114, "total": 116, "ptcgoCode": "STS",
}


def _card_payload(number: int) -> dict:
    return {
        "id": f"xy11-{number}", "name": f"Card {number}", "number": str(number),
        "rarity": "Common", "artist": "Someone", "supertype": "Pokémon",
        "images": {"small": f"https://images.example/{number}.png"},
    }


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _write_distinct_image(path, seed: int) -> None:
    """A deterministic noise image -- distinct per seed so two cards never
    collide in perceptual-hash space."""
    rng = np.random.default_rng(seed)
    Image.fromarray(rng.integers(0, 255, (120, 90, 3), dtype=np.uint8)).save(path)


def _fake_download(url, dest_path, client=None):
    seed = int(str(url).rsplit("/", 1)[-1].split(".")[0])
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_distinct_image(dest_path, seed)
    return dest_path


# --- cache_set ---

def test_cache_set_stores_cards_images_and_hashes(session, tmp_path):
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(n) for n in (1, 2, 3)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        result = catalog.cache_set(session, "xy11", tmp_path / "cache")

    assert result.cards_total == 3
    assert result.images_downloaded == 3
    assert result.hashed == 3
    assert result.images_failed == 0
    assert not result.from_cache
    assert result.ready

    card_set = session.get(CardSet, "xy11")
    assert card_set.name == "Steam Siege"
    assert card_set.code == "STS"
    assert card_set.cached_at is not None

    cards = catalog.get_cached_cards(session, "xy11")
    assert [c.number for c in cards] == ["1", "2", "3"]
    assert all(c.phash for c in cards)
    assert all(c.local_image_path for c in cards)
    # distinct art must produce distinct hashes, or identification later
    # would be matching against duplicates
    assert len({c.phash for c in cards}) == 3


def test_cache_set_short_circuits_when_already_cached(session, tmp_path):
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(1)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")

    # second call must not touch the API at all -- that's the whole point of
    # caching against a flaky upstream
    with patch.object(pokemontcg_catalog, "get_set", side_effect=AssertionError("should not hit API")), \
         patch.object(pokemontcg_catalog, "get_set_cards", side_effect=AssertionError("should not hit API")):
        result = catalog.cache_set(session, "xy11", tmp_path / "cache")

    assert result.from_cache
    assert result.cards_total == 1
    assert result.hashed == 1


def test_cache_set_force_refetches(session, tmp_path):
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(1)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")
        result = catalog.cache_set(session, "xy11", tmp_path / "cache", force=True)

    assert not result.from_cache
    assert result.images_downloaded == 1


def test_cache_set_tolerates_individual_image_failures(session, tmp_path):
    def flaky(url, dest_path, client=None):
        if url.endswith("2.png"):
            raise CatalogLookupError("image unavailable")
        return _fake_download(url, dest_path)

    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(n) for n in (1, 2, 3)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=flaky):
        result = catalog.cache_set(session, "xy11", tmp_path / "cache")

    # one bad image must not lose the other two, nor abort the set
    assert result.cards_total == 3
    assert result.images_failed == 1
    assert result.hashed == 2
    assert result.ready
    cards = {c.number: c for c in catalog.get_cached_cards(session, "xy11")}
    assert cards["2"].phash is None
    assert cards["1"].phash and cards["3"].phash


def test_cache_set_reports_not_ready_when_nothing_hashed(session, tmp_path):
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(1)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=CatalogLookupError("down")):
        result = catalog.cache_set(session, "xy11", tmp_path / "cache")

    assert result.hashed == 0
    assert not result.ready


def test_is_cached_and_cached_sets(session, tmp_path):
    assert not catalog.is_cached(session, "xy11")
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(1)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")

    assert catalog.is_cached(session, "xy11")
    assert [s.id for s in catalog.cached_sets(session)] == ["xy11"]


def test_cache_set_captures_bundled_pricing_at_no_extra_cost(session, tmp_path):
    """tcgplayer/cardmarket pricing rides along in the same card-list
    response Stage 2 already fetches -- confirm it actually gets kept
    rather than discarded, since scan_ingest/pricing.py depends on it."""
    payload = _card_payload(1)
    payload["tcgplayer"] = {"prices": {"normal": {"market": 0.5}}}
    payload["cardmarket"] = {"prices": {"trendPrice": 0.4}}

    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[payload]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")

    card = catalog.get_cached_cards(session, "xy11")[0]
    assert card.raw_prices["tcgplayer"]["prices"]["normal"]["market"] == 0.5
    assert card.raw_prices["cardmarket"]["prices"]["trendPrice"] == 0.4


def test_cache_set_leaves_raw_prices_none_when_absent(session, tmp_path):
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(1)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")

    assert catalog.get_cached_cards(session, "xy11")[0].raw_prices is None


def test_cached_phashes_match_the_source_image(session, tmp_path):
    """The stored hash must be usable for identification later -- i.e. it
    round-trips to the same value computed from the cached image file."""
    with patch.object(pokemontcg_catalog, "get_set", return_value=SET_PAYLOAD), \
         patch.object(pokemontcg_catalog, "get_set_cards", return_value=[_card_payload(7)]), \
         patch.object(pokemontcg_catalog, "download_image", side_effect=_fake_download):
        catalog.cache_set(session, "xy11", tmp_path / "cache")

    card = catalog.get_cached_cards(session, "xy11")[0]
    with Image.open(card.local_image_path) as image:
        recomputed = imagehash.phash(image.convert("RGB"))
    assert imagehash.hex_to_hash(card.phash) - recomputed == 0


# --- adapter retry behaviour (the upstream API returns bursts of 5xx) ---

def _response(status: int, json_body=None, text: str = "") -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=httpx.Request("GET", "https://x"))
    return httpx.Response(status, text=text, request=httpx.Request("GET", "https://x"))


def test_get_json_retries_through_server_errors():
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            return _response(500)
        return _response(200, {"data": [{"id": "xy11-1"}], "totalCount": 1})

    client = type("C", (), {"get": staticmethod(get)})()
    with patch("time.sleep"):
        cards = pokemontcg_catalog.get_set_cards("xy11", client=client)

    assert len(calls) == 3
    assert cards == [{"id": "xy11-1"}]


def test_get_json_retries_when_body_is_unparseable():
    """A 200 with an empty/HTML body happens during upstream incidents; the
    old adapter let the JSON error escape instead of retrying."""
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _response(200, text="<html>502 Bad Gateway</html>")
        return _response(200, {"data": [{"id": "xy11-1"}], "totalCount": 1})

    client = type("C", (), {"get": staticmethod(get)})()
    with patch("time.sleep"):
        cards = pokemontcg_catalog.get_set_cards("xy11", client=client)

    assert len(calls) == 2
    assert cards == [{"id": "xy11-1"}]


def test_get_json_does_not_retry_client_errors():
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return _response(404, text="not found")

    client = type("C", (), {"get": staticmethod(get)})()
    with patch("time.sleep"), pytest.raises(CatalogLookupError, match="404"):
        pokemontcg_catalog.get_set_cards("xy11", client=client)

    assert len(calls) == 1  # retrying a 404 would just waste time


def test_get_json_gives_up_after_max_attempts():
    def get(url, **kwargs):
        return _response(500)

    client = type("C", (), {"get": staticmethod(get)})()
    with patch("time.sleep"), pytest.raises(CatalogLookupError, match="after 6 attempts"):
        pokemontcg_catalog.get_set_cards("xy11", client=client)


def test_get_set_cards_paginates():
    pages = {
        1: {"data": [{"id": f"xy11-{n}"} for n in range(1, 251)], "totalCount": 260},
        2: {"data": [{"id": f"xy11-{n}"} for n in range(251, 261)], "totalCount": 260},
    }

    def get(url, params=None, **kwargs):
        return _response(200, pages[params["page"]])

    client = type("C", (), {"get": staticmethod(get)})()
    cards = pokemontcg_catalog.get_set_cards("xy11", client=client)

    assert len(cards) == 260


def test_get_set_cards_raises_when_set_has_no_cards():
    def get(url, **kwargs):
        return _response(200, {"data": [], "totalCount": 0})

    client = type("C", (), {"get": staticmethod(get)})()
    with pytest.raises(CatalogLookupError, match="no cards found"):
        pokemontcg_catalog.get_set_cards("nope", client=client)
