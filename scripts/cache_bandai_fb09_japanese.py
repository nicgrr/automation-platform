#!/usr/bin/env python
"""Cache Bandai's official Japanese Fusion World catalogue images.

The English TCGplayer cache is a different physical printing, so these use a
separate set id and Japanese-language catalogue rows. Re-runnable: existing
images are reused and database records are refreshed in place.

    .venv/bin/python -m scripts.cache_bandai_fb09_japanese --set FB11 --dry-run
    .venv/bin/python -m scripts.cache_bandai_fb09_japanese --set FB11
"""

import argparse
import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.models import CardSet, CatalogCard, CatalogItem, CatalogItemType, TcgCard
from automation_control.scan_ingest.catalog import _compute_phashes

LIST_BASE = "https://www.dbs-cardgame.com/fw/jp/cardlist/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EzBay private catalogue cache)"}


@dataclass(frozen=True)
class JapaneseSet:
    code: str
    category: str
    set_id: str
    name: str
    release_date: str
    english_set_id: str
    printed_total: int

    @property
    def list_url(self) -> str:
        return f"{LIST_BASE}?category%5B0%5D={self.category}&search=true"


SETS = {
    "FB09": JapaneseSet("FB09", "584009", "dbfw-jp-fb09", "Dual Evolution (Japanese)",
                         "2026-03-14", "dbfw-24569", 123),
    "FB11": JapaneseSet("FB11", "584011", "dbfw-jp-fb11", "Brightness of Hope (Japanese)",
                         "2026-09-12", "dbfw-24716", 123),
}


def english_metadata(session, english_set_id: str = "dbfw-24569") -> dict[str, tuple[str, str | None]]:
    """Return English names/rarities while leaving the physical cards Japanese."""
    rows = session.query(CatalogCard).filter(CatalogCard.set_id == english_set_id).all()
    names: dict[str, str] = {}
    rarities: dict[str, str | None] = {}
    for card in rows:
        # The English cache may contain separate "(Alternate Art)" records.
        # The Japanese catalogue already records that distinction as Parallel,
        # so the user-facing title should remain the clean English card name.
        clean = re.sub(r"\s*\(Alternate Art\)\s*$", "", card.name).strip()
        current = names.get(card.number)
        if current is None or "(Alternate Art)" not in card.name:
            names[card.number] = clean
            rarities[card.number] = getattr(card, "rarity", None)
    return {number: (name, rarities.get(number)) for number, name in names.items()}


def english_display_names(session) -> dict[str, str]:
    """Backward-compatible FB09 name helper used by the original tests."""
    return {number: name for number, (name, _) in english_metadata(session).items()}


@dataclass(frozen=True)
class OfficialCard:
    number: str
    name: str
    image_url: str
    image_stem: str
    variant: str | None

    @property
    def card_id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.image_stem.lower()).strip("-")
        return f"dbfw-jp-{slug}"


def parse_card_list(html: str, set_code: str = "FB09") -> list[OfficialCard]:
    pattern = re.compile(
        rf'data-src=["\'](?P<src>[^"\']*/jp/(?P<file>{re.escape(set_code)}-[^"\']+?\.webp))["\']\s+'
        rf'alt=["\'](?P<number>{re.escape(set_code)}-\d{{3}})\s+(?P<name>[^"\']+)["\']',
        re.I,
    )
    cards = []
    seen = set()
    for match in pattern.finditer(html):
        stem = Path(match.group("file")).stem
        key = stem.lower()
        if key in seen:
            continue
        seen.add(key)
        variant = "Super Parallel" if stem.endswith("_p2") else "Parallel" if stem.endswith("_p1") else None
        cards.append(OfficialCard(
            number=match.group("number").upper(), name=match.group("name").strip(),
            image_url=urljoin(LIST_BASE, match.group("src")), image_stem=stem, variant=variant,
        ))
    return cards


async def _download_missing(cards: list[OfficialCard], directory: Path) -> None:
    semaphore = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        async def download(card: OfficialCard):
            destination = directory / f"{card.card_id}.webp"
            if destination.exists() and destination.stat().st_size:
                return
            async with semaphore:
                response = await client.get(card.image_url)
                response.raise_for_status()
                destination.write_bytes(response.content)
        await asyncio.gather(*(download(card) for card in cards))


async def run(dry_run: bool = False, set_code: str = "FB09") -> int:
    set_info = SETS[set_code]
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(set_info.list_url)
        response.raise_for_status()
    cards = parse_card_list(response.text, set_info.code)
    if len(cards) < 120:
        raise SystemExit(f"Bandai page yielded only {len(cards)} {set_info.code} cards; refusing a partial cache")
    print(f"official Japanese {set_info.code} records found: {len(cards)}")
    if dry_run:
        print("--dry-run: no images downloaded and no database rows written")
        return 0

    directory = Path(get_settings().scan_cache_dir) / set_info.set_id
    directory.mkdir(parents=True, exist_ok=True)
    await _download_missing(cards, directory)

    now = datetime.now(UTC)
    with SessionLocal() as session:
        metadata = english_metadata(session, set_info.english_set_id)
        card_set = session.get(CardSet, set_info.set_id)
        if card_set is None:
            card_set = CardSet(id=set_info.set_id)
            session.add(card_set)
        card_set.name, card_set.code, card_set.series = set_info.name, f"{set_info.code}-JP", "Fusion World Japanese"
        card_set.release_date, card_set.printed_total, card_set.total = (
            set_info.release_date, set_info.printed_total, len(cards)
        )
        card_set.cached_at, card_set.game = now, "dragon_ball_fusion_world"

        for card in cards:
            display_name, rarity = metadata.get(card.number, (card.name, None))
            local = directory / f"{card.card_id}.webp"
            phash, art = _compute_phashes(local)
            record = session.get(CatalogCard, card.card_id)
            if record is None:
                record = CatalogCard(id=card.card_id, set_id=set_info.set_id)
                session.add(record)
            record.number, record.name = card.number, display_name
            record.rarity, record.supertype = rarity, "Dragon Ball Fusion World Card"
            record.image_url, record.local_image_path = card.image_url, str(local)
            record.phash, record.art_phash = phash, art

            item = session.get(CatalogItem, card.card_id)
            if item is None:
                item = CatalogItem(id=card.card_id, item_type=CatalogItemType.TCG_CARD, name=display_name)
                session.add(item)
            item.name, item.image_url = display_name, card.image_url

            tcg = session.get(TcgCard, card.card_id)
            if tcg is None:
                tcg = TcgCard(catalog_item_id=card.card_id, game="dragon_ball_fusion_world")
                session.add(tcg)
            tcg.set_id, tcg.set_code, tcg.number = set_info.set_id, set_info.code, card.number
            tcg.character, tcg.rarity = display_name, rarity
            tcg.variant, tcg.parallel = card.variant, card.variant
            tcg.language, tcg.release_date = "Japanese", set_info.release_date
        session.commit()
    print(f"cached {len(cards)} Japanese {set_info.code} records with reference images in {directory}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--set", default="FB09", choices=sorted(SETS), dest="set_code")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.dry_run, args.set_code))


if __name__ == "__main__":
    raise SystemExit(main())
