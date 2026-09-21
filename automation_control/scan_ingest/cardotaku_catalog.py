"""Scan-ingest reference catalogues sourced from cardotaku.com's public
Shopify storefront -- for games with no other free source.

Both games here are the same story: no official card-list API, and
tcgplayer_catalog.py's source (tcgcsv.com) doesn't carry either of them
(Japan-only games never sold through the US TCGplayer market). Super
Dragon Ball Heroes's one fan-run card database (sdbh.dbz.space) sits
behind a Cloudflare bot-challenge with no API -- not something a
background service can scrape reliably, so it wasn't used. cardotaku.com
is a real retailer selling singles with the physical card's own printed
code in the listing title (e.g. "SUPER DRAGON BALL HEROES BM1-005 C"),
and Shopify exposes every storefront's catalogue at
`/collections/<handle>/products.json` with no auth or bot-check -- the
same convention scripts/backfill_one_piece_images_from_tcgplayer.py's
tcgcsv.com source follows for its own free public mirror.

This is a single seller's listing, not an official database: coverage is
whatever they've stocked, and pricing (see pricing.CardotakuSnapshotPriceSource)
is their one asking price, not a market aggregate. Treat both as rough,
same caution as the pokemontcg.io snapshot for English Pokemon.

A card's set isn't given directly -- it's inferred from the code's prefix
("BM1-005" -> set "BM1", "SDV11-PUR1" -> set "SDV11"), which is how each
game's own numbering already scopes a card to its set. A code repeated
across several listings (a foil/"golden"/"Parallel" print of the same
physical card, or a reprint) is deduped the same way tcgplayer_catalog.py
dedupes TCGplayer parallels: the plain listing (no extra descriptor in
the title) claims the shared id, everything else gets its own
"<prefix>p<product_id>" id so pHash can still tell them apart.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..models import CardSet, CatalogCard, CatalogItem, CatalogItemType, TcgCard
from .catalog import CacheResult, _compute_phashes

STORE_BASE = "https://cardotaku.com/en-us"
HEADERS = {"User-Agent": "ezbay-scan-catalog/1.0"}
PAGE_SIZE = 250
IMAGE_WORKERS = 8

# A short all-caps/digits token right after the code is a rarity marker
# (C, R, SR, UR, CP, SEC, HCP, GDM, PUR, ...); a longer/lowercase remainder
# ("without golden", "Parallel") is a parallel descriptor, not a rarity.
_RARITY_RE = re.compile(r"^[A-Z]{1,4}\d{0,2}$")


@dataclass(frozen=True)
class CardotakuGame:
    key: str               # matches games.GAMES key / TcgCard.game / CardSet.game
    collection_handle: str  # cardotaku.com/en-us/collections/<this>
    title_words: str        # the fixed franchise text every listing title starts with
    set_id_prefix: str      # CardSet.id / CatalogCard.id prefix, keeps ids unambiguous
    series_label: str       # CardSet.series


# "SUPER DRAGON BALL HEROES BM1-005 C" -> code "BM1-005", rest "C". The
# prefix after the franchise name varies release to release (BM, UM, UGM,
# SH, PCS, PJS, SDV1..SDV12, ...) so only the fixed franchise words are
# anchored per game -- see _title_re().
GAMES: dict[str, CardotakuGame] = {g.key: g for g in (
    CardotakuGame("super_dragon_ball_heroes", "super-dragon-ball-heroes", "SUPER DRAGON BALL HEROES", "sdbh-", "Super Dragon Ball Heroes"),
    # cardotaku splits Super Divers into one collection per mission (SDV1..
    # SDV12) plus a promo one; "-sdv-card-list" is their own combined
    # listing across all of them (1,148 products as of 2026-09-22), so one
    # fetch covers the whole game the same way the SDBH collection does.
    CardotakuGame("dragon_ball_super_divers", "dragon-ball-super-divers-sdv-card-list", "DRAGON BALL SUPER DIVERS", "dbsd-", "Dragon Ball Super Divers"),
)}


def _slug(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def _title_re(title_words: str) -> re.Pattern:
    escaped_words = r"\s+".join(re.escape(word) for word in title_words.split())
    return re.compile(rf"^{escaped_words}\s+([A-Z0-9]+-[A-Z0-9]+)\b\s*(.*)$", re.IGNORECASE)


def _parse_title(title: str, title_words: str) -> tuple[str, str | None, str | None] | None:
    """(code, rarity, parallel_descriptor), or None if this listing isn't
    a single card (a boxed set, sleeves, a manga volume, etc -- these have
    no code-shaped token right after the franchise name)."""
    match = _title_re(title_words).match(title.strip())
    if not match:
        return None
    code, rest = match.group(1).upper(), match.group(2).strip()
    if not rest:
        return code, None, None
    first_word = rest.split(" ", 1)[0]
    if _RARITY_RE.match(first_word):
        return code, first_word, (rest[len(first_word):].strip() or None)
    return code, None, rest


def fetch_products(client: httpx.Client, game: CardotakuGame) -> list[dict]:
    """Every product in this game's cardotaku collection, across every page."""
    products: list[dict] = []
    page = 1
    while True:
        response = client.get(f"{STORE_BASE}/collections/{game.collection_handle}/products.json",
                               params={"limit": PAGE_SIZE, "page": page})
        response.raise_for_status()
        batch = response.json().get("products") or []
        if not batch:
            break
        products.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return products


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


def cache_catalog(
    session: Session, game: CardotakuGame, cache_dir: Path, client: httpx.Client,
    products: list[dict] | None = None, force: bool = False,
) -> list[CacheResult]:
    """Cache one game's whole cardotaku catalogue as one CardSet per code
    prefix. Unlike the other importers, sets aren't fetched separately --
    each game's own numbering already scopes a code to its set, so sets
    are created on the fly as prefixes are first seen. Safe to re-run:
    existing rows update in place, images already on disk aren't
    re-downloaded unless `force` is set.
    """
    products = fetch_products(client, game) if products is None else products

    by_code: dict[str, dict] = {}
    skipped_non_single = 0
    for product in products:
        parsed = _parse_title(product.get("title") or "", game.title_words)
        if parsed is None:
            skipped_non_single += 1
            continue
        code, rarity, descriptor = parsed
        entry = {"product": product, "code": code, "rarity": rarity, "descriptor": descriptor}
        existing = by_code.get(code)
        # Prefer the plain listing (no parallel descriptor) as the card
        # its shared code claims -- same "plain print wins the id" rule
        # tcgplayer_catalog.py uses for its own parallels.
        if existing is None or (descriptor is None and existing["descriptor"] is not None):
            by_code[code] = entry

    card_sets: dict[str, CardSet] = {}
    jobs: list[tuple[CatalogCard, Path]] = []
    for entry in by_code.values():
        product, code, rarity = entry["product"], entry["code"], entry["rarity"]
        prefix = code.split("-", 1)[0]
        set_id = game.set_id_prefix + _slug(prefix)

        card_set = card_sets.get(set_id) or session.get(CardSet, set_id) or CardSet(id=set_id)
        session.add(card_set)
        card_sets[set_id] = card_set
        card_set.name = f"{game.title_words.title()} {prefix}"
        card_set.code = prefix
        card_set.series = game.series_label
        card_set.game = game.key

        card_id = game.set_id_prefix + _slug(code)
        card = session.get(CatalogCard, card_id) or CatalogCard(id=card_id, set_id=set_id)
        session.add(card)
        card.set_id = set_id
        card.number = code
        card.name = product.get("title") or code
        card.rarity = rarity
        images = product.get("images") or []
        card.image_url = images[0]["src"] if images else None
        price = (product.get("variants") or [{}])[0].get("price")
        card.raw_prices = {"cardotaku": {"price": price}} if price else None

        if session.get(CatalogItem, card_id) is None:
            session.add(CatalogItem(id=card_id, item_type=CatalogItemType.TCG_CARD, name=card.name, image_url=card.image_url))
        if session.get(TcgCard, card_id) is None:
            session.add(TcgCard(catalog_item_id=card_id, game=game.key, set_id=set_id, set_code=prefix, number=code, rarity=rarity, language="Japanese"))

        if card.image_url:
            jobs.append((card, cache_dir / set_id / f"{_slug(code)}.jpg"))
        else:
            card.local_image_path = card.phash = card.art_phash = None

    downloaded = failed = hashed = 0
    with ThreadPoolExecutor(IMAGE_WORKERS) as pool:
        results = pool.map(lambda job: _fetch_and_hash(client, job[0].image_url, job[1], force), jobs)
        for (card, path), (was_downloaded, phash, art) in zip(jobs, results):
            downloaded += was_downloaded
            if phash is None:
                failed += 1
                continue
            card.local_image_path = str(path)
            card.phash, card.art_phash = phash, art
            hashed += 1

    now = datetime.now(UTC)
    for card_set in card_sets.values():
        card_set.cached_at = now
    session.commit()

    return _summarize(session, card_sets, downloaded, failed, hashed, skipped_non_single)


def _summarize(session: Session, card_sets: dict[str, CardSet], downloaded: int, failed: int, hashed: int, skipped: int) -> list[CacheResult]:
    """One CacheResult per set (for a per-set progress printout), plus a
    summary line folded into the first result -- there's no single top-level
    'set' to report import totals against, unlike the group/set-scoped
    importers this mirrors."""
    results = []
    for set_id, card_set in sorted(card_sets.items()):
        total = session.query(CatalogCard).filter(CatalogCard.set_id == set_id).count()
        results.append(CacheResult(
            set_id=set_id, set_name=card_set.name, cards_total=total,
            images_downloaded=0, images_failed=0, hashed=0, from_cache=False,
        ))
    if results:
        results[0] = CacheResult(
            set_id=results[0].set_id, set_name=f"{len(card_sets)} sets, {skipped} non-card listing(s) skipped",
            cards_total=results[0].cards_total, images_downloaded=downloaded, images_failed=failed,
            hashed=hashed, from_cache=False,
        )
    return results
