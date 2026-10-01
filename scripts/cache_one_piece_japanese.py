#!/usr/bin/env python
"""Cache every Japanese One Piece Card Game set from Bandai's card list.

Each Japanese printing is stored separately from the English TCGplayer
catalogue, including parallel-art suffixes such as ``_p1``. Display names
remain English when the same card number exists in the English catalogue;
language and official Japanese artwork remain Japanese.

    .venv/bin/python -m scripts.cache_one_piece_japanese --dry-run
    .venv/bin/python -m scripts.cache_one_piece_japanese
    .venv/bin/python -m scripts.cache_one_piece_japanese --series 550101
"""

import argparse
import html
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
from sqlalchemy import select

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardSet, CatalogCard, CatalogItem, CatalogItemType, TcgCard
from automation_control.scan_ingest.catalog import _compute_phashes

BASE = "https://www.onepiece-cardgame.com"
LIST_URL = f"{BASE}/cardlist/"
ENGLISH_LIST_URL = "https://asia-en.onepiece-cardgame.com/cardlist/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EzBay private catalogue cache)"}
_worker_state = threading.local()
ENGLISH_NAME_OVERRIDES = {
    "EB03-006": "Nami",
    "EB03-027": "Marguerite",
    "OP14-083": "Miss Wednesday",
    "P-120": "Sanji",
    "P-150": "Kuzan",
    "P-151": "Smoker",
    "P-159": "Monkey.D.Luffy",
}
SERIES_RE = re.compile(r'<option value="(550\d+)"[^>]*>(.*?)</option>', re.S)
CODE_RE = re.compile(r"【([A-Z]+-?\d+)】", re.I)
CARD_RE = re.compile(
    r'<a class="modalOpen"[^>]*><img[^>]*data-src="(?P<src>[^"]+/card/(?P<file>[^"]+?\.png)[^"]*)"'
    r'[^>]*alt="(?P<alt>[^"]*)"[^>]*></a>\s*<dl class="modalCol"[^>]*>\s*<dt>.*?'
    r'<div class="infoCol">\s*<span>(?P<code>[^<]+)</span>\s*\|\s*<span>(?P<rarity>[^<]+)</span>'
    r'\s*\|\s*<span>(?P<category>[^<]+)</span>.*?<div class="cardName">(?P<name>.*?)</div>',
    re.S,
)


@dataclass(frozen=True)
class JapaneseCard:
    id: str
    number: str
    japanese_name: str
    rarity: str
    category: str
    image_url: str
    variant: str | None


def series_from_index(page: str) -> list[tuple[str, str]]:
    seen = set()
    rows = []
    for series_id, raw_title in SERIES_RE.findall(page):
        if series_id in seen:
            continue
        seen.add(series_id)
        title = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(raw_title)))
        rows.append((series_id, " ".join(title.split())))
    return rows


def parse_cards(page: str) -> list[JapaneseCard]:
    cards = []
    seen = set()
    for match in CARD_RE.finditer(page):
        filename = match.group("file")
        stem = Path(filename).stem.lower().replace("_", "-")
        card_id = f"opjp-{stem}"
        if card_id in seen:
            continue
        seen.add(card_id)
        suffix = re.search(r"_p(\d+)$", Path(filename).stem, re.I)
        cards.append(JapaneseCard(
            id=card_id,
            number=match.group("code").strip().upper(),
            japanese_name=html.unescape(re.sub(r"<[^>]+>", "", match.group("name"))).strip(),
            rarity=match.group("rarity").strip(),
            category=match.group("category").strip().title(),
            image_url=urljoin(LIST_URL, match.group("src")),
            variant=f"Parallel {suffix.group(1)}" if suffix else None,
        ))
    return cards


def _clean_english_name(name: str, number: str) -> str:
    value = re.sub(rf"\s*-\s*{re.escape(number)}(?:\s.*)?$", "", name, flags=re.I)
    value = re.sub(r"\s*\((?:Alternate Art|Parallel|Manga|SP|Gold|Foil)[^)]*\)\s*$", "", value, flags=re.I)
    value = re.sub(r"\s*\([A-Z0-9-]*\d[A-Z0-9-]*\)\s*$", "", value, flags=re.I)
    return value.strip() or name


def english_names(session) -> dict[str, str]:
    rows = session.execute(
        select(TcgCard.number, CatalogItem.name)
        .join(CatalogItem, CatalogItem.id == TcgCard.catalog_item_id)
        .where(TcgCard.game == "one_piece", TcgCard.language == "English")
    ).all()
    result = {}
    for number, name in rows:
        if not number or not name:
            continue
        clean = _clean_english_name(name, number)
        current = result.get(number.upper())
        if current is None or len(clean) < len(current):
            result[number.upper()] = clean
    return result


def _download_and_hash(card: JapaneseCard, destination: Path, force: bool) -> tuple[str | None, str | None]:
    if force or not destination.exists() or destination.stat().st_size == 0:
        client = getattr(_worker_state, "client", None)
        if client is None:
            client = httpx.Client(timeout=45, headers=HEADERS, follow_redirects=True)
            _worker_state.client = client
        response = client.get(card.image_url)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
    return _compute_phashes(destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--series", help="only one official series id, e.g. 550101")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)

    with httpx.Client(timeout=45, headers=HEADERS, follow_redirects=True) as client:
        index = client.get(LIST_URL)
        index.raise_for_status()
        series = series_from_index(index.text)
        if args.series:
            series = [row for row in series if row[0] == args.series]
            if not series:
                raise SystemExit(f"unknown Japanese One Piece series id: {args.series}")

        pages = []
        for i, (series_id, title) in enumerate(series, 1):
            response = client.get(LIST_URL, params={"search": "true", "series": series_id})
            response.raise_for_status()
            cards = parse_cards(response.text)
            if not cards:
                raise SystemExit(f"{series_id} returned no cards; refusing a partial cache")
            # Bandai uses the same series suffix and image/card ids on the
            # English-Asia site. It often publishes English names there
            # before TCGplayer has the corresponding international group.
            english_id = f"556{series_id[3:]}"
            english_response = client.get(
                ENGLISH_LIST_URL, params={"search": "true", "series": english_id},
            )
            english_cards = parse_cards(english_response.text) if english_response.status_code == 200 else []
            official_english = {card.id: card.japanese_name for card in english_cards}
            official_english_by_number = {}
            for english_card in english_cards:
                official_english_by_number.setdefault(english_card.number, english_card.japanese_name)
            pages.append((series_id, title, cards, official_english, official_english_by_number))
            print(f"[{i}/{len(series)}] {series_id} {title}: {len(cards)} printings", flush=True)

    print(f"official Japanese catalogue: {len(pages)} sets, {sum(len(x[2]) for x in pages)} printings")
    if args.dry_run:
        return 0

    cache_root = Path(get_settings().scan_cache_dir)
    with SessionLocal() as session:
        names = english_names(session)
        for i, (series_id, title, cards, official_english, official_english_by_number) in enumerate(pages, 1):
            set_id = f"opjp-{series_id}"
            directory = cache_root / set_id
            destinations = {card.id: directory / f"{card.id}.png" for card in cards}
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                hashes = dict(zip(
                    (card.id for card in cards),
                    pool.map(lambda card: _download_and_hash(card, destinations[card.id], args.force), cards),
                ))

            product_code = (CODE_RE.search(title).group(1).replace("-", "").upper() if CODE_RE.search(title) else None)
            card_set = session.get(CardSet, set_id)
            if card_set is None:
                card_set = CardSet(id=set_id)
                session.add(card_set)
            card_set.name = f"{title} (Japanese)"
            card_set.code = f"{product_code}-JP" if product_code else f"JP-{series_id}"
            card_set.series = "One Piece Card Game Japanese"
            # Language is a printing attribute, not a separate game. Keeping
            # this as one_piece makes the normal One Piece scan route search
            # both English and Japanese reference sets in one pass.
            card_set.game = "one_piece"
            card_set.printed_total = len({card.number for card in cards})
            card_set.total = len(cards)
            card_set.cached_at = datetime.now(UTC)

            source_url = f"{LIST_URL}?search=true&series={series_id}"
            for card in cards:
                display_name = (
                    ENGLISH_NAME_OVERRIDES.get(card.number)
                    or official_english.get(card.id)
                    or official_english_by_number.get(card.number)
                    or names.get(card.number, card.japanese_name)
                )
                phash, art_hash = hashes[card.id]
                record = session.get(CatalogCard, card.id)
                if record is None:
                    record = CatalogCard(id=card.id, set_id=set_id)
                    session.add(record)
                record.set_id, record.number, record.name = set_id, card.number, display_name
                record.rarity, record.supertype = card.rarity, card.category
                record.image_url, record.local_image_path = card.image_url, str(destinations[card.id])
                record.phash, record.art_phash = phash, art_hash
                record.raw_prices = {"catalog_source": source_url, "japanese_name": card.japanese_name}

                item = session.get(CatalogItem, card.id)
                if item is None:
                    item = CatalogItem(id=card.id, item_type=CatalogItemType.TCG_CARD, name=display_name)
                    session.add(item)
                item.name, item.image_url = display_name, card.image_url

                tcg = session.get(TcgCard, card.id)
                if tcg is None:
                    tcg = TcgCard(catalog_item_id=card.id, game="one_piece")
                    session.add(tcg)
                tcg.game, tcg.set_id, tcg.set_code = "one_piece", set_id, product_code
                tcg.number, tcg.character, tcg.rarity = card.number, display_name, card.rarity
                tcg.variant, tcg.parallel, tcg.language = card.variant, card.variant, "Japanese"
            session.commit()
            print(f"  cached [{i}/{len(pages)}] {card_set.code}: {len(cards)} images/hashes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
