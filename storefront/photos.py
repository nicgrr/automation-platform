"""Serving the seller's own photographs to shoppers.

Until now the shop showed pokemontcg.io reference art -- the publisher's
studio render of a pristine card. For ungraded singles that is close to
misleading: condition is the thing the buyer is actually assessing, and a
stock image can't show a whitened edge or a soft corner. The scans already
exist (1,014 of them), so this puts the real card in front of the buyer.

Three constraints shape the route, in order of how much they matter:

  * It only serves photos for things that are *actively listed*. The
    inventory table covers the whole collection, so a route keyed on
    inventory id with no listing check would let anyone walk the entire
    holding one id at a time. Listing something publicly is the consent.
  * The path comes out of the database, but it's still resolved and
    confined to the configured media directory before anything is opened.
    A row rewritten to "/etc/passwd" reads as a 404, not a disclosure.
  * Thumbnails are cached to disk and served with a long max-age, because
    the alternative is re-encoding a 750x1050 scan on every grid impression.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.config import get_settings
from automation_control.database import get_session
from automation_control.models import InventoryItem, ShopListing, ShopListingStatus

router = APIRouter()

THUMB_WIDTH = 420
LARGE_WIDTH = 900
THUMB_QUALITY = 82


CARD_RATIO = 63 / 88  # width / height of a trading card
_CROP_MARGIN = 0.025


def card_box(image) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the card in a front photo shot on a
    smooth dark backdrop, or None when it can't be found confidently.

    The card is the busy region (artwork, text, borders) while the backdrop
    is smooth and the acrylic stand is faint, so the density of strong edges
    gives the card's width and top edge. A portrait card's height then
    follows from its fixed proportions, because the stand would otherwise
    be read as part of it; a landscape card keeps its measured height.
    Only used on fronts: a card back is a flat colour with too little
    texture to measure.
    """
    import cv2
    import numpy as np

    w, h = image.size
    scale = 500 / max(w, h)
    sw, sh = max(1, round(w * scale)), max(1, round(h * scale))
    gray = cv2.cvtColor(np.asarray(image.convert("RGB").resize((sw, sh))), cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (0, 0), 1.2)
    magnitude = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    dense = cv2.blur((magnitude > 60).astype(np.float32), (9, 9)) > 0.28
    columns = np.where(dense.mean(axis=0) > 0.25 * dense.mean(axis=0).max())[0]
    if len(columns) < 20:
        return None
    x0, x1 = int(columns.min()), int(columns.max())
    band = dense[:, x0:x1 + 1].mean(axis=1)
    rows = np.where(band > 0.25 * band.max())[0]
    if len(rows) < 20:
        return None
    y0, y_seen = int(rows.min()), int(rows.max())
    card_w = x1 - x0 + 1
    seen_h = y_seen - y0 + 1
    card_h = seen_h if seen_h / card_w < 1.15 else round(card_w / CARD_RATIO)
    y1 = min(sh - 1, y0 + card_h)
    if card_w < 0.2 * sw or (y1 - y0) < 0.2 * sh:
        return None
    pad_x, pad_y = round(card_w * _CROP_MARGIN), round(card_h * _CROP_MARGIN)
    box = (x0 - pad_x, y0 - pad_y, x1 + 1 + pad_x, y1 + 1 + pad_y)
    left, top, right, bottom = (round(v / scale) for v in box)
    return max(0, left), max(0, top), min(w, right), min(h, bottom)


def _media_roots() -> list[Path]:
    """Every directory a real, seller-taken photo can legitimately live
    under. Bulk-scanned cards land under scan_media_dir; AI-assisted
    single-item captures (collectibles.py's review flow) land under
    captured_cards_dir instead -- a genuine second source, not a
    workaround, so both are allowed roots rather than widening either one.
    """
    settings = get_settings()
    return [Path(settings.scan_media_dir).resolve(), Path(settings.captured_cards_dir).resolve()]


def _media_root() -> Path:
    return _media_roots()[0]


def _thumb_dir() -> Path:
    directory = _media_root() / "shop-thumbs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_source(raw_path: str | None) -> Path | None:
    """Resolve a stored photo path, or None if it escapes the media root.

    Kept separate from the route so the containment rule is directly
    testable -- this is the check that turns a database write into a
    non-event rather than an arbitrary file read.
    """
    if not raw_path:
        return None
    try:
        resolved = Path(raw_path).resolve()
    except (OSError, RuntimeError):
        return None
    if not any(resolved.is_relative_to(root) for root in _media_roots()):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _photo_sources(session: Session, listing_id: str) -> list[Path]:
    """Every usable photo for an active listing, best first: the seller's
    professional front/back pair (ShopListing.ebay_photo_paths, written by
    the photo-batch tool) ahead of the original scan of the card.

    The scan stays as the fallback so a listing with no professional photo
    yet looks exactly as it did before.
    """
    listing = session.get(ShopListing, listing_id)
    from . import live_inventory
    if live_inventory.project(session, listing) is None:
        return []
    found: list[Path] = []
    for raw in listing.ebay_photo_paths or []:
        source = safe_source(raw) if isinstance(raw, str) else None
        if source is not None:
            found.append(source)
    if not found:
        row = session.execute(
            select(InventoryItem.scan_image_path)
            .join(ShopListing, ShopListing.inventory_item_id == InventoryItem.id)
            .where(ShopListing.id == listing_id)
        ).first()
        scan = safe_source(row[0]) if row else None
        if scan is not None:
            found.append(scan)
    return found


def photo_path_for_listing(session: Session, listing_id: str, index: int = 0) -> Path | None:
    """The seller's own photo behind an active listing, if there is one.
    `index` 0 is the front; 1 is the back when a professional pair exists."""
    sources = _photo_sources(session, listing_id)
    return sources[index] if index < len(sources) else None


def has_own_photo(session: Session, listing_id: str) -> bool:
    return photo_path_for_listing(session, listing_id) is not None


# Bump when how photos are processed changes (e.g. the card crop), so every
# browser re-fetches rather than showing a cached older rendering.
PHOTO_RENDER_VERSION = "2"


def photo_url(session: Session, listing_id: str, *, n: int = 0, large: bool = False) -> str:
    """The URL for a listing's photo, carrying a version so a changed photo
    (scan -> professional) or a changed crop is never served from a browser's
    week-long cache of the old image at the same address."""
    source = photo_path_for_listing(session, listing_id, index=n)
    token = f"{PHOTO_RENDER_VERSION}{source.stem[:10]}" if source else PHOTO_RENDER_VERSION
    params = [f"v={token}"] + ([f"n={n}"] if n else []) + (["large=true"] if large else [])
    return f"/photo/{listing_id}?{'&'.join(params)}"


def photo_count(session: Session, listing_id: str) -> int:
    return len(_photo_sources(session, listing_id))


@router.get("/photo/{listing_id}")
def listing_photo(listing_id: str, n: int = 0, large: bool = False, v: str = "",
                  session: Session = Depends(get_session)) -> FileResponse:
    source = photo_path_for_listing(session, listing_id, index=max(n, 0))
    if source is None:
        raise HTTPException(status_code=404, detail="no photo for this listing")

    width = LARGE_WIDTH if large else THUMB_WIDTH
    crop = n <= 0
    # The source file's own name is part of the cache key, so swapping a
    # listing's photo (scan -> professional) can't be served from a stale thumb.
    thumb = _thumb_dir() / f"{listing_id.replace('/', '_')}-{source.stem}-{width}{'c' if crop else ''}.jpg"
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                box = card_box(image) if crop else None
                # Only a loose photo (card on a backdrop) is cropped; a scan
                # that is already tight on the card is left exactly as it is.
                if box and (box[2] - box[0]) * (box[3] - box[1]) < 0.6 * image.width * image.height:
                    image = image.crop(box)
                if image.width > width:
                    height = round(image.height * (width / image.width))
                    image = image.resize((width, height), Image.LANCZOS)
                image.save(thumb, "JPEG", quality=THUMB_QUALITY, optimize=True)
        except Exception:
            # never fail a product page over a thumbnail
            return FileResponse(source, headers={"Cache-Control": "public, max-age=86400"})
    # A versioned URL changes whenever the photo does, so it can be cached for
    # a long time; an unversioned one only briefly.
    return FileResponse(thumb, headers={"Cache-Control": f"public, max-age={604800 if v else 3600}"})
