"""Set/card reference caching for the scan-ingest pipeline.

Fetches a set's full card list and reference images from pokemontcg.io
once, precomputes each card's perceptual hash, and stores all of it locally
(DB rows + image files). Subsequent scan sessions for the same set read
purely from the cache -- which matters because the upstream API is
noticeably flaky (bursts of 500s), so the fewer times we depend on it the
better.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import imagehash
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters import pokemontcg_catalog
from ..models import CardSet, CatalogCard
from .identify import art_phash


@dataclass
class CacheResult:
    set_id: str
    set_name: str
    cards_total: int
    images_downloaded: int
    images_failed: int
    hashed: int
    from_cache: bool

    @property
    def ready(self) -> bool:
        """Whether this set can actually be used for pHash identification --
        a set cached with no usable hashes would silently fall back to
        OCR-only matching, so callers should check rather than assume."""
        return self.hashed > 0


def _compute_phashes(image_path: Path) -> tuple[str | None, str | None]:
    """Whole-card and artwork-window hashes, from one decode of the file.

    The artwork hash is what identifies reverse holos, whose foil pattern
    covers everything except that window -- see identify.ART_REGION.
    """
    try:
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            return str(imagehash.phash(rgb)), str(art_phash(rgb))
    except Exception:
        # A corrupt/partial download shouldn't abort caching the whole set;
        # the card stays in the DB with phash=None and is simply skipped as
        # a pHash candidate (OCR can still match it).
        return None, None


def is_cached(session: Session, set_id: str) -> bool:
    card_set = session.get(CardSet, set_id)
    return bool(card_set and card_set.cached_at)


def cached_sets(session: Session) -> list[CardSet]:
    return list(session.scalars(select(CardSet).where(CardSet.cached_at.is_not(None)).order_by(CardSet.release_date)))


def get_cached_cards(session: Session, set_id: str) -> list[CatalogCard]:
    return list(session.scalars(select(CatalogCard).where(CatalogCard.set_id == set_id).order_by(CatalogCard.number)))


def _upsert_set(session: Session, payload: dict) -> CardSet:
    card_set = session.get(CardSet, payload["id"])
    if card_set is None:
        card_set = CardSet(id=payload["id"])
        session.add(card_set)
    card_set.name = payload.get("name") or payload["id"]
    card_set.code = payload.get("ptcgoCode")
    card_set.series = payload.get("series")
    card_set.release_date = payload.get("releaseDate")
    card_set.printed_total = payload.get("printedTotal")
    card_set.total = payload.get("total")
    images = payload.get("images") or {}
    card_set.logo_url = images.get("logo")
    card_set.symbol_url = images.get("symbol")
    return card_set


def _upsert_card(session: Session, set_id: str, payload: dict) -> CatalogCard:
    card = session.get(CatalogCard, payload["id"])
    if card is None:
        card = CatalogCard(id=payload["id"], set_id=set_id)
        session.add(card)
    card.number = str(payload.get("number") or "")
    card.name = payload.get("name") or ""
    card.rarity = payload.get("rarity")
    card.artist = payload.get("artist")
    card.supertype = payload.get("supertype")
    images = payload.get("images") or {}
    # The "small" image is ~245px wide -- plenty for a 64-bit perceptual
    # hash, and a fraction of the bandwidth/disk of the hi-res version,
    # which matters when caching a few hundred cards per set.
    card.image_url = images.get("small") or images.get("large")
    # tcgplayer/cardmarket pricing rides along in the same response -- free
    # to capture, unlike a dedicated per-card price lookup. See
    # scan_ingest/pricing.py for how (and how cautiously) this gets used.
    raw_prices = {k: payload[k] for k in ("tcgplayer", "cardmarket") if payload.get(k)}
    card.raw_prices = raw_prices or None
    return card


def cache_set(
    session: Session, set_id: str, cache_dir: Path, api_key: str | None = None,
    force: bool = False, progress=None,
) -> CacheResult:
    """Ensure one set's cards, images, and hashes are cached locally.

    Returns immediately (`from_cache=True`) if the set is already cached,
    unless `force` is set. Individual image failures are counted and
    tolerated rather than aborting -- a set is still usable with a few
    cards missing hashes, and re-running fills only the gaps.
    """
    if not force and is_cached(session, set_id):
        card_set = session.get(CardSet, set_id)
        cards = get_cached_cards(session, set_id)
        return CacheResult(
            set_id=set_id, set_name=card_set.name, cards_total=len(cards),
            images_downloaded=0, images_failed=0,
            hashed=sum(1 for c in cards if c.phash), from_cache=True,
        )

    set_payload = pokemontcg_catalog.get_set(set_id, api_key=api_key)
    card_payloads = pokemontcg_catalog.get_set_cards(set_id, api_key=api_key)

    card_set = _upsert_set(session, set_payload)
    set_image_dir = cache_dir / set_id
    downloaded = failed = hashed = 0

    for index, payload in enumerate(card_payloads):
        card = _upsert_card(session, set_id, payload)
        if progress:
            progress(index + 1, len(card_payloads), card.name)

        if not card.image_url:
            failed += 1
            continue

        image_path = set_image_dir / f"{card.number}.png"
        if not image_path.exists() or force:
            try:
                pokemontcg_catalog.download_image(card.image_url, image_path)
                downloaded += 1
            except Exception:
                failed += 1
                continue
        card.local_image_path = str(image_path)
        card.phash, card.art_phash = _compute_phashes(image_path)
        if card.phash:
            hashed += 1

    card_set.cached_at = datetime.now(UTC)
    session.commit()

    return CacheResult(
        set_id=set_id, set_name=card_set.name, cards_total=len(card_payloads),
        images_downloaded=downloaded, images_failed=failed, hashed=hashed, from_cache=False,
    )


def search_sets(query: str, api_key: str | None = None) -> list[dict]:
    """Find sets by name, code, or id substring -- the operator picks the
    set they're about to scan from this."""
    needle = query.strip().lower()
    return [
        s for s in pokemontcg_catalog.list_sets(api_key=api_key)
        if needle in (s.get("name") or "").lower()
        or needle in (s.get("ptcgoCode") or "").lower()
        or needle in (s.get("id") or "").lower()
    ]


def sets_with_printed_total(total: int, api_key: str | None = None) -> list[dict]:
    """Every set whose printed card count is `total` -- the lookup behind
    identifying an uncached set from the "199/264" printed on its cards.

    The denominator is not unique on its own (a handful of sets share a
    printed total), so this returns all candidates and leaves choosing
    between them to the caller, which can disambiguate by actually trying
    to match the scanned art against each.
    """
    return [s for s in pokemontcg_catalog.list_sets(api_key=api_key) if s.get("printedTotal") == total]
