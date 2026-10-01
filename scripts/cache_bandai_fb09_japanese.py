#!/usr/bin/env python
"""Cache Bandai's official Japanese FB09 catalogue and reference images.

The English TCGplayer cache is a different physical printing, so these use a
separate set id and Japanese-language catalogue rows. Re-runnable: existing
images are reused and database records are refreshed in place.

    .venv/bin/python -m scripts.cache_bandai_fb09_japanese --dry-run
    .venv/bin/python -m scripts.cache_bandai_fb09_japanese
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

LIST_URL = "https://www.dbs-cardgame.com/fw/jp/cardlist/?category%5B0%5D=584009&search=true"
SET_ID = "dbfw-jp-fb09"
SET_NAME = "Dual Evolution (Japanese)"
RELEASE_DATE = "2026-03-14"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EzBay private catalogue cache)"}


def english_display_names(session) -> dict[str, str]:
    """Return FB09's English names while leaving the physical cards Japanese."""
    rows = session.query(CatalogCard).filter(CatalogCard.set_id == "dbfw-24569").all()
    names: dict[str, str] = {}
    for card in rows:
        # The English cache may contain separate "(Alternate Art)" records.
        # The Japanese catalogue already records that distinction as Parallel,
        # so the user-facing title should remain the clean English card name.
        clean = re.sub(r"\s*\(Alternate Art\)\s*$", "", card.name).strip()
        current = names.get(card.number)
        if current is None or "(Alternate Art)" not in card.name:
            names[card.number] = clean
    return names


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


def parse_card_list(html: str) -> list[OfficialCard]:
    pattern = re.compile(
        r'data-src=["\'](?P<src>[^"\']*/jp/(?P<file>FB09-[^"\']+?\.webp))["\']\s+'
        r'alt=["\'](?P<number>FB09-\d{3})\s+(?P<name>[^"\']+)["\']',
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
            image_url=urljoin(LIST_URL, match.group("src")), image_stem=stem, variant=variant,
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


async def run(dry_run: bool = False) -> int:
    async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(LIST_URL)
        response.raise_for_status()
    cards = parse_card_list(response.text)
    if len(cards) < 120:
        raise SystemExit(f"Bandai page yielded only {len(cards)} FB09 cards; refusing a partial cache")
    print(f"official Japanese FB09 records found: {len(cards)}")
    if dry_run:
        print("--dry-run: no images downloaded and no database rows written")
        return 0

    directory = Path(get_settings().scan_cache_dir) / SET_ID
    directory.mkdir(parents=True, exist_ok=True)
    await _download_missing(cards, directory)

    now = datetime.now(UTC)
    with SessionLocal() as session:
        english_names = english_display_names(session)
        card_set = session.get(CardSet, SET_ID)
        if card_set is None:
            card_set = CardSet(id=SET_ID)
            session.add(card_set)
        card_set.name, card_set.code, card_set.series = SET_NAME, "FB09-JP", "Fusion World Japanese"
        card_set.release_date, card_set.printed_total, card_set.total = RELEASE_DATE, 123, len(cards)
        card_set.cached_at, card_set.game = now, "dragon_ball_fusion_world"

        for card in cards:
            display_name = english_names.get(card.number, card.name)
            local = directory / f"{card.card_id}.webp"
            phash, art = _compute_phashes(local)
            record = session.get(CatalogCard, card.card_id)
            if record is None:
                record = CatalogCard(id=card.card_id, set_id=SET_ID)
                session.add(record)
            record.number, record.name = card.number, display_name
            record.rarity, record.supertype = card.variant, "Dragon Ball Fusion World Card"
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
            tcg.set_id, tcg.set_code, tcg.number = SET_ID, "FB09", card.number
            tcg.character, tcg.rarity = display_name, card.variant
            tcg.variant, tcg.parallel = card.variant, card.variant
            tcg.language, tcg.release_date = "Japanese", RELEASE_DATE
        session.commit()
    print(f"cached {len(cards)} Japanese FB09 records with reference images in {directory}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
