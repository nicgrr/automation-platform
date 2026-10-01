"""Scan-ingest reference catalogues from TCGplayer.

Pokemon's ordinary sets come from pokemontcg.io. For One Piece, the Dragon
Ball games, and Pokemon special reprint sets which that source omits, the
source is tcgcsv.com, the same free, keyless, daily mirror of
TCGplayer's catalogue that scripts/backfill_one_piece_images_from_tcgplayer.py
already uses. One TCGplayer "group" becomes one CardSet, and every product
carrying a card Number becomes a CatalogCard with a reference image and
pHash -- the same shape catalog.cache_set produces for Pokemon, so
detection, identification, commit, and /inventory need no second code path.

Unlike pokemontcg.io, TCGplayer lists every printing as its own product
(an alternate-art or gold-stamped parallel shares its base card's Number
but is a separate product with its own price). Each gets its own
CatalogCard, so pHash can tell them apart and each prices on its own.

Card ids follow the convention already in the DB, so these rows join up
with catalogue items that exist already rather than duplicating them:
  * the plain print of a number is `_slug(game, set_code, number)` --
    the id scripts/import_one_piece_from_optcgapi.py and
    scripts/import_collector_export.py already use for One Piece and
    Dragon Ball items
  * every other printing (parallel, alt art, a numberless card) is
    "tcgp-<productId>", which cannot collide with anything
A plain print reprinted in a later group (promo reprints, release-event
packs) keeps the id its original set claimed. Groups are processed oldest
first so the original set gets there first. The reprint gets no row of its
own: it is the same physical card, and a second reference with identical
art would only tie with the first on every pHash lookup.
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
from .games import GAME_LABELS

API_BASE = "https://tcgcsv.com/tcgplayer"
HEADERS = {"User-Agent": "ezbay-scan-catalog/1.0"}


@dataclass(frozen=True)
class TcgplayerGame:
    key: str             # matches games.Game.key / TcgCard.game / CardSet.game
    category_id: int     # TCGplayer categoryId
    set_prefix: str      # CardSet.id prefix, keeps ids short and unambiguous


# One entry per games.GAMES key that's actually sourced from TCGplayer
# (i.e. everything except pokemon_japan, which is TCGdex -- see
# tcgdex_catalog.py). `label`/`vision_name` live once in games.GAMES;
# this only adds what's specific to fetching this game from tcgcsv.com.
TCGPLAYER_GAMES: dict[str, TcgplayerGame] = {g.key: g for g in (
    TcgplayerGame("one_piece", 68, "op"),
    TcgplayerGame("dragon_ball_fusion_world", 80, "dbfw"),
    TcgplayerGame("dragon_ball_super", 27, "dbs"),
    TcgplayerGame("dragon_ball_z", 23, "dbz"),
)}

# Pokemon's main catalogue remains pokemontcg.io. TCGplayer also has exact
# products/images/prices for stamped special reprint sets which are absent
# there, notably the annual Trick or Trade sets. This source is deliberately
# separate from TCGPLAYER_GAMES so `--game all` cannot accidentally import
# TCGplayer's entire duplicate Pokemon catalogue.
POKEMON_SPECIAL_GAME = TcgplayerGame("pokemon", 3, "pkm")

# "OP01-024", "BT1-001", "FS11-01", "P-001" -> (set code, number)
_CODE_RE = re.compile(r"^([A-Za-z0-9]+)-(\d+[A-Za-z]?)$")
# Same rule as scripts/import_one_piece_from_optcgapi._is_plain_print: a
# trailing "(OP01-024)"-style code in the name is still the plain print;
# any other parenthetical ("(Alternate Art)", "(Gold)") marks a parallel.
_TRAILING_CODE_PAREN_RE = re.compile(r"\([A-Za-z0-9-]*\d[A-Za-z0-9-]*\)$")
_PARENTHETICAL_RE = re.compile(r"\(([^()]*)\)")

# TCGplayer's price subtypes, onto pokemontcg.io's tcgplayer.prices keys so
# the existing snapshot price reader (pricing.PokemonTcgPriceSource) needs no
# second parser. A DB/OP product almost always has exactly one subtype,
# since TCGplayer splits parallels into separate products.
_SUBTYPE_KEYS = {"Normal": "normal", "Foil": "holofoil", "Holofoil": "holofoil"}

IMAGE_WORKERS = 8


def _slug(*parts: str) -> str:
    joined = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def is_plain_print(name: str) -> bool:
    return "(" not in _TRAILING_CODE_PAREN_RE.sub("", name).strip()


def _parallel_label(name: str) -> str | None:
    stripped = _TRAILING_CODE_PAREN_RE.sub("", name).strip()
    labels = [m for m in _PARENTHETICAL_RE.findall(stripped) if m.strip()]
    return ", ".join(labels)[:64] if labels else None


def _extended(product: dict, field: str) -> str | None:
    return next((e.get("value") for e in product.get("extendedData") or [] if e.get("name") == field), None)


def _card_number(product: dict) -> str | None:
    number = (_extended(product, "Number") or "").strip()
    return None if not number or number.upper() == "N/A" else number


def fetch_groups(client: httpx.Client, game: TcgplayerGame) -> list[dict]:
    """Every group (set) in a game, oldest first -- the order plain-print
    ids must be claimed in (see the module docstring)."""
    response = client.get(f"{API_BASE}/{game.category_id}/groups")
    response.raise_for_status()
    groups = response.json()["results"]
    return sorted(groups, key=lambda g: (g.get("publishedOn") or "9999", g["groupId"]))


def _fetch(client: httpx.Client, game: TcgplayerGame, group_id: int, what: str) -> list[dict]:
    last_error: Exception | None = None
    for _ in range(3):
        try:
            response = client.get(f"{API_BASE}/{game.category_id}/{group_id}/{what}")
            response.raise_for_status()
            return response.json()["results"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_error = exc
    raise RuntimeError(f"tcgcsv {what} for group {group_id} failed: {last_error}")


def set_id_for(game: TcgplayerGame, group: dict) -> str:
    return f"{game.set_prefix}-{group['groupId']}"


def _raw_prices(price_rows: list[dict]) -> dict | None:
    prices: dict[str, dict] = {}
    for row in price_rows:
        key = _SUBTYPE_KEYS.get(row.get("subTypeName") or "", "normal")
        entry = {field: row.get(f"{field}Price") for field in ("market", "mid", "low", "high")}
        if any(entry.values()):
            prices[key] = entry
    return {"tcgplayer": {"prices": prices, "source": "tcgcsv"}} if prices else None


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
    """Runs on a worker thread: no DB access here, only files and hashing."""
    downloaded = False
    if force or not destination.exists():
        if not _download(client, url, destination):
            return False, None, None
        downloaded = True
    phash, art = _compute_phashes(destination)
    return downloaded, phash, art


def cache_group(
    session: Session, game: TcgplayerGame, group: dict, cache_dir: Path, client: httpx.Client,
    force: bool = False,
) -> CacheResult:
    """Cache one TCGplayer group as a CardSet with hashed reference cards.

    Safe to re-run: existing rows are updated in place (prices refresh on
    every run), and images already on disk are re-hashed, not re-downloaded,
    unless `force` is set.
    """
    set_id = set_id_for(game, group)
    products = _fetch(client, game, group["groupId"], "products")
    try:
        prices_by_product: dict[int, list[dict]] = {}
        for row in _fetch(client, game, group["groupId"], "prices"):
            prices_by_product.setdefault(row["productId"], []).append(row)
    except RuntimeError:
        prices_by_product = {}  # a card list without prices still identifies cards

    singles = [p for p in products if _card_number(p) is not None]
    card_set = session.get(CardSet, set_id) or CardSet(id=set_id)
    session.add(card_set)
    card_set.name = group.get("name") or set_id
    card_set.code = group.get("abbreviation")
    card_set.series = GAME_LABELS.get(game.key, game.key.replace("_", " ").title())
    card_set.game = game.key
    published = (group.get("publishedOn") or "")[:10]
    card_set.release_date = published.replace("-", "/") or None
    card_set.total = len(singles)

    jobs: list[tuple[CatalogCard, str, Path]] = []
    # A special reprint set can contain cards from several original sets,
    # including two different cards with the same printed N/TOTAL. Such a
    # pair cannot share our usual number-derived id; retain each TCGplayer
    # product id instead. This also fixes the old behaviour where the later
    # product silently overwrote the earlier card in the same cache run.
    number_counts: dict[str, int] = {}
    for product in singles:
        key = (_card_number(product) or "").upper()
        number_counts[key] = number_counts.get(key, 0) + 1
    # Rows added in this call aren't visible to session.get until a flush,
    # so a number TCGplayer lists twice in one group is tracked here.
    added: dict[str, CatalogCard] = {}
    for product in singles:
        number = _card_number(product)
        name = product.get("name") or ""
        plain = is_plain_print(name)
        code = _CODE_RE.match(number)
        card_id = f"tcgp-{product['productId']}"
        if plain and number_counts[number.upper()] == 1:
            slug = _slug(game.key, code.group(1), code.group(2)) if code else _slug(game.key, card_set.code or "", number)
            claimed = added.get(slug) or session.get(CatalogCard, slug)
            if claimed is not None and claimed.set_id != set_id:
                continue  # a reprint of a card its original set already covers
            card_id = slug

        card = added.get(card_id) or session.get(CatalogCard, card_id)
        is_new = card is None
        if is_new:
            card = CatalogCard(id=card_id, set_id=set_id)
            session.add(card)
        added[card_id] = card
        card.set_id = set_id
        card.number = number
        card.name = name
        card.rarity = _extended(product, "Rarity")
        card.supertype = _extended(product, "CardType")
        card.raw_prices = _raw_prices(prices_by_product.get(product["productId"], []))
        has_image = product.get("imageCount", 1) != 0  # 0 means TCGplayer's placeholder art
        card.image_url = f"https://tcgplayer-cdn.tcgplayer.com/product/{product['productId']}_400w.jpg" if has_image else None

        # The generic catalogue record every valuation reader keys off
        # (see commit._catalog_item_id_for). Existing rows -- e.g. One Piece
        # items optcgapi already imported -- keep their own name and image.
        if is_new and session.get(CatalogItem, card_id) is None:
            session.add(CatalogItem(
                id=card_id, item_type=CatalogItemType.TCG_CARD, name=name,
                image_url=f"https://tcgplayer-cdn.tcgplayer.com/product/{product['productId']}_in_1000x1000.jpg" if has_image else None,
            ))
        if is_new and session.get(TcgCard, card_id) is None:
            session.add(TcgCard(
                catalog_item_id=card_id, game=game.key, set_id=set_id,
                set_code=code.group(1) if code else card_set.code, number=number,
                character=product.get("cleanName") or name, rarity=card.rarity,
                parallel=None if plain else _parallel_label(name), release_date=card_set.release_date,
            ))

        if card.image_url:
            jobs.append((card, card.image_url, cache_dir / set_id / f"{product['productId']}.jpg"))
        else:
            card.local_image_path = card.phash = card.art_phash = None

    downloaded = failed = hashed = 0
    with ThreadPoolExecutor(IMAGE_WORKERS) as pool:
        results = pool.map(lambda job: _fetch_and_hash(client, job[1], job[2], force), jobs)
        for (card, _, path), (was_downloaded, phash, art) in zip(jobs, results):
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
        set_id=set_id, set_name=card_set.name, cards_total=len(singles),
        images_downloaded=downloaded, images_failed=failed, hashed=hashed, from_cache=False,
    )
