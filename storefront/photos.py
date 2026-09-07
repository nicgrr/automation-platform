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
THUMB_QUALITY = 82


def _media_root() -> Path:
    return Path(get_settings().scan_media_dir).resolve()


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
    root = _media_root()
    if not resolved.is_relative_to(root):
        return None
    if not resolved.is_file():
        return None
    return resolved


def photo_path_for_listing(session: Session, listing_id: str) -> Path | None:
    """The seller's own photo behind an active listing, if there is one."""
    row = session.execute(
        select(InventoryItem.scan_image_path)
        .join(ShopListing, ShopListing.inventory_item_id == InventoryItem.id)
        .where(
            ShopListing.id == listing_id,
            ShopListing.status == ShopListingStatus.ACTIVE,
        )
    ).first()
    if row is None:
        return None
    return safe_source(row[0])


def has_own_photo(session: Session, listing_id: str) -> bool:
    return photo_path_for_listing(session, listing_id) is not None


@router.get("/photo/{listing_id}")
def listing_photo(listing_id: str, session: Session = Depends(get_session)) -> FileResponse:
    source = photo_path_for_listing(session, listing_id)
    if source is None:
        raise HTTPException(status_code=404, detail="no photo for this listing")

    thumb = _thumb_dir() / f"{listing_id.replace('/', '_')}-{THUMB_WIDTH}.jpg"
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                if image.width > THUMB_WIDTH:
                    height = round(image.height * (THUMB_WIDTH / image.width))
                    image = image.resize((THUMB_WIDTH, height), Image.LANCZOS)
                image.save(thumb, "JPEG", quality=THUMB_QUALITY, optimize=True)
        except Exception:
            # never fail a product page over a thumbnail
            return FileResponse(source, headers={"Cache-Control": "public, max-age=86400"})
    return FileResponse(thumb, headers={"Cache-Control": "public, max-age=604800"})
