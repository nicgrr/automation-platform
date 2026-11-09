"""Pick one catalogue printing for a photo by comparing it with reference art.

Text alone cannot separate a One Piece base print from its parallels: they
share name, code, rarity and set. Every Japanese One Piece catalogue row has
official artwork, so the bulk photo intake compares the owner's photo with
those images and accepts a choice only when the model is confident about
exactly one. Anything else stays with the owner for review.
"""
from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from .card_recognition import RecognitionError, choose_printing

MAX_REFERENCE_BYTES = 8 * 1024 * 1024
REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ROOT = Path(__file__).resolve().parents[1]


def _reference_dir(settings) -> Path:
    base = Path(getattr(settings, "scan_cache_dir", "scan_ingest_data/cache"))
    return (base if base.is_absolute() else _ROOT / base) / "catalogue_refs"


def reference_image(item, settings, *, client: httpx.Client | None = None) -> Path | None:
    """A local copy of the catalogue item's artwork, downloading it once."""
    url = (item.image_url or "").strip()
    if not url:
        return None
    if not url.startswith("https://"):
        path = Path(url)
        path = path if path.is_absolute() else _ROOT / path
        return path if path.is_file() else None

    suffix = Path(urlparse(url).path).suffix.lower()
    suffix = suffix if suffix in REFERENCE_SUFFIXES else ".png"
    target = _reference_dir(settings) / f"{re.sub(r'[^A-Za-z0-9_-]', '_', item.id)}{suffix}"
    if target.is_file() and target.stat().st_size:
        return target
    owns_client = client is None
    client = client or httpx.Client(
        timeout=15, follow_redirects=True, headers={"User-Agent": "EzBay catalogue reference"},
    )
    try:
        response = client.get(url)
        if response.status_code != 200 or len(response.content) > MAX_REFERENCE_BYTES:
            return None
        Image.open(BytesIO(response.content)).verify()
    except (httpx.HTTPError, OSError, ValueError):
        return None
    finally:
        if owns_client:
            client.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    partial.write_bytes(response.content)
    os.replace(partial, target)
    return target


def photo_printing_chooser(photo_path: Path, settings, record: dict):
    """Build the ``choose_printing`` callback for ``resolve_catalogue_card``.

    ``record`` receives what was compared and decided, so the pair keeps an
    audit trail of an artwork-based identification.
    """

    def choose(candidates):
        references = [reference_image(item, settings) for item, _tcg in candidates]
        record["candidates"] = [item.id for item, _tcg in candidates]
        if any(reference is None for reference in references):
            record["outcome"] = "reference art unavailable"
            return None
        try:
            choice = choose_printing(photo_path, references, settings.anthropic_api_key)
        except (RecognitionError, OSError) as exc:
            record["outcome"] = f"comparison failed: {type(exc).__name__}"
            return None
        record["reason"] = choice.reason
        if not choice.confident or not 1 <= choice.match <= len(candidates):
            record["outcome"] = "not confident"
            return None
        chosen = candidates[choice.match - 1]
        record["outcome"] = "matched"
        record["chosen"] = chosen[0].id
        return chosen

    return choose
