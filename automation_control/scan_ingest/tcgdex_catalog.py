"""Scan-ingest reference catalogue for Japanese-print Pokemon cards, from
TCGdex (api.tcgdex.net) -- a free, keyless, community-run multi-language
Pokemon TCG database. pokemontcg.io (catalog.py) is English-only, so a
Japanese-print card never matches anything cached there even though it's
visually the same card family, hence its own game key ("pokemon_japan")
and its own reference pool -- see games.py.

Same CatalogCard/CatalogItem/TcgCard shape catalog.cache_set produces for
English Pokemon, so detection, identification, and /inventory need no
third code path. Card ids are namespaced "jp-<tcgdex id, lowercased>"
(e.g. "jp-sv7-001") because TCGdex's own set ids collide with
pokemontcg.io's for the same era (both call Neo Genesis "neo1") -- without
the prefix a Japanese Stellar Miracle #1 and an unrelated English set's
card 1 could fight over the same primary key.

No pricing: nothing here bills or queries a price source. pokemonpricetracker
and the pokemontcg.io snapshot both key off an English card name/printing,
so silently reusing either for a Japanese card would either match nothing
(wasting a billed pokemonpricetracker credit) or -- worse -- match some
unrelated English reprint by the same character name. See session.py's
game-price routing: pokemon_japan always commits with price unknown.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..models import CardSet, CatalogCard, CatalogItem, CatalogItemType, TcgCard
from .catalog import CacheResult, _compute_phashes

API_BASE = "https://api.tcgdex.net/v2/ja"
ASSETS_BASE = "https://assets.tcgdex.net/ja"
HEADERS = {"User-Agent": "ezbay-scan-catalog/1.0"}
GAME_KEY = "pokemon_japan"
SET_ID_PREFIX = "jp-"

IMAGE_WORKERS = 8


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def set_id_for(tcgdex_set_id: str) -> str:
    return SET_ID_PREFIX + _slug(tcgdex_set_id)


def _get(client: httpx.Client, path: str) -> dict | list:
    last_error: Exception | None = None
    for _ in range(3):
        try:
            response = client.get(f"{API_BASE}{path}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"TCGdex {path} failed: {last_error}")


def fetch_sets(client: httpx.Client) -> list[dict]:
    """Every Japanese-print set, oldest first (release date may be missing
    on very old sets, which sort last rather than crash the sort)."""
    sets = _get(client, "/sets")
    return sorted(sets, key=lambda s: s.get("id", ""))


def _download(client: httpx.Client, url: str, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            response = client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if response.status_code == 200 and response.content:
            destination.write_bytes(response.content)
            return True
        if 400 <= response.status_code < 500 and response.status_code != 429:
            return False
    return False


def _fetch_and_hash(client: httpx.Client, url: str, destination: Path, force: bool) -> tuple[bool, str | None, str | None]:
    downloaded = False
    if force or not destination.exists():
        if not _download(client, url, destination):
            return False, None, None
        downloaded = True
    phash, art = _compute_phashes(destination)
    return downloaded, phash, art


def cache_set(
    session: Session, tcgdex_set_id: str, cache_dir: Path, client: httpx.Client, force: bool = False,
) -> CacheResult:
    """Cache one TCGdex set as a CardSet with hashed reference cards. Safe
    to re-run: existing rows are updated in place, and images already on
    disk are re-hashed rather than re-downloaded unless `force` is set.
    """
    payload = _get(client, f"/sets/{tcgdex_set_id}")
    set_id = set_id_for(payload["id"])
    cards = payload.get("cards") or []

    card_set = session.get(CardSet, set_id) or CardSet(id=set_id)
    session.add(card_set)
    card_set.name = payload.get("name") or set_id
    card_set.code = payload.get("id")
    card_set.series = "Pokémon (Japanese)"
    card_set.game = GAME_KEY
    card_set.release_date = (payload.get("releaseDate") or "").replace("-", "/") or None
    counts = payload.get("cardCount") or {}
    card_set.total = counts.get("total") or len(cards)
    card_set.printed_total = counts.get("official")

    jobs: list[tuple[CatalogCard, Path]] = []
    for entry in cards:
        tcgdex_card_id = entry.get("id")
        number = entry.get("localId")
        if not tcgdex_card_id or not number:
            continue
        card_id = SET_ID_PREFIX + _slug(tcgdex_card_id)
        name = entry.get("name") or ""

        card = session.get(CatalogCard, card_id) or CatalogCard(id=card_id, set_id=set_id)
        session.add(card)
        card.set_id = set_id
        card.number = str(number)
        card.name = name
        image_base = entry.get("image")
        card.image_url = f"{image_base}/high.webp" if image_base else None

        if session.get(CatalogItem, card_id) is None:
            session.add(CatalogItem(id=card_id, item_type=CatalogItemType.TCG_CARD, name=name, image_url=card.image_url))
        if session.get(TcgCard, card_id) is None:
            session.add(TcgCard(
                catalog_item_id=card_id, game=GAME_KEY, set_id=set_id, set_code=payload.get("id"),
                number=str(number), character=name, language="Japanese", release_date=card_set.release_date,
            ))

        if image_base:
            jobs.append((card, cache_dir / set_id / f"{card.number}.webp"))
        else:
            card.local_image_path = card.phash = card.art_phash = None

    downloaded = failed = hashed = 0
    with ThreadPoolExecutor(IMAGE_WORKERS) as pool:
        # low.webp: small enough for pHash, a fraction of high.webp's bandwidth.
        results = pool.map(lambda job: _fetch_and_hash(client, job[0].image_url.replace("/high.webp", "/low.webp"), job[1], force), jobs)
        for (card, path), (was_downloaded, phash, art) in zip(jobs, results):
            downloaded += was_downloaded
            if phash is None:
                failed += 1
                continue
            card.local_image_path = str(path)
            card.phash, card.art_phash = phash, art
            hashed += 1

    card_set.cached_at = datetime.now(UTC)
    session.commit()
    return CacheResult(
        set_id=set_id, set_name=card_set.name, cards_total=len(cards),
        images_downloaded=downloaded, images_failed=failed, hashed=hashed, from_cache=False,
    )
