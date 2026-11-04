"""The Collectables page: figurines live on Vinted, linking out to buy there.

Nothing is sold through this site, so it can't sell a figure twice. A row only
appears while its Vinted listing is active, and its photos stop being served
once it isn't: a sold figure drops off when the owner marks it sold.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from automation_control.database import get_session
from automation_control.models import PlatformConfig, VintedListing, VintedListingStatus

from .photos import THUMB_QUALITY, THUMB_WIDTH, _thumb_dir, safe_source

router = APIRouter()

DEPARTMENT_SLUG = "collectibles"
_SOURCE_PREFIX = "vinted_fallback_source:"
# The owner's referral link, labelled as ours on the page. Buy links stay direct to each item.
INVITE_URL = "https://www.vinted.com.au/invite/nlvaultco/LybacAF0z3Cha2ia2m3c4oj2fdcahxbbd"


def live_listings(session: Session) -> list[VintedListing]:
    rows = session.scalars(
        select(VintedListing)
        .where(VintedListing.status == VintedListingStatus.ACTIVE)
        .order_by(VintedListing.title)
    ).all()
    return [row for row in rows if row.url.startswith("https://")]


def is_showing(session: Session, slug: str) -> bool:
    return slug == DEPARTMENT_SLUG and session.scalar(
        select(VintedListing.id).where(VintedListing.status == VintedListingStatus.ACTIVE).limit(1)
    ) is not None


def source_details(session: Session, listing_id: str) -> dict:
    row = session.get(PlatformConfig, _SOURCE_PREFIX + listing_id)
    return dict(row.value or {}) if row else {}


def _photo_source(session: Session, listing_id: str) -> Path | None:
    listing = session.get(VintedListing, listing_id)
    if listing is None or listing.status != VintedListingStatus.ACTIVE:
        return None
    paths = source_details(session, listing_id).get("paths") or []
    return safe_source(paths[0]) if paths else None


def has_photo(session: Session, listing_id: str) -> bool:
    return _photo_source(session, listing_id) is not None


@router.get("/vinted-photo/{listing_id}")
def vinted_photo(listing_id: str, session: Session = Depends(get_session)) -> FileResponse:
    source = _photo_source(session, listing_id)
    if source is None:
        raise HTTPException(status_code=404, detail="no photo for this listing")
    thumb = _thumb_dir() / f"vinted-{listing_id.replace('/', '_')}-{source.stem}-{THUMB_WIDTH}.jpg"
    # Sold figures should vanish soon after, so this caches for an hour, not a week.
    headers = {"Cache-Control": "public, max-age=3600"}
    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image, ImageOps

            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                if image.width > THUMB_WIDTH:
                    image = image.resize((THUMB_WIDTH, round(image.height * THUMB_WIDTH / image.width)), Image.LANCZOS)
                image.save(thumb, "JPEG", quality=THUMB_QUALITY, optimize=True)
        except Exception:
            return FileResponse(source, headers=headers)
    return FileResponse(thumb, headers=headers)
