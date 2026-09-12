"""Serving stored photos: one place, instead of seven near-copies.

Every review surface in this app grew its own image endpoint, and they
had drifted into disagreeing about three things that matter:

* **Whether a missing file is a 404 or a 500.** Five endpoints checked
  `path.exists()` first; `/collectibles/capture/image/{id}` handed the
  path straight to `FileResponse`, so a photo deleted or moved off disk
  raised an unhandled error rather than a missing image. It also never
  checked whether the row had a path at all.
* **Cache headers.** The same file -- `InventoryItem.scan_image_path` --
  was served as `public, max-age=604800` by `/inventory/image` and
  `private, max-age=300` by `/foil-review/image` and
  `/inventory-audit/image`, and with no cache header at all by
  `/cards/image`. Now one policy: `private`, because every one of these
  is behind a session cookie and none of them should be storable by a
  shared cache, and a long max-age, because a stored photo is immutable
  in practice -- a re-scan writes a new path.
* **Thumbnailing.** `inventory_review` and `scan_feed` had two separate
  resize-and-cache implementations; the other five endpoints served
  full-size JPEGs to grids that displayed them a couple of hundred pixels
  wide.

`serve_thumb` keeps both callers' existing cache directories and widths,
so nothing already generated is invalidated.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .config import get_settings

# A week. These files don't change in place -- a correction or a re-scan
# writes a new path and the URL changes with it -- so the only cost of a
# long life here is a stale image after a manual file swap on disk.
IMAGE_MAX_AGE = 604800
THUMB_QUALITY = 78


def _cache_control(max_age: int) -> dict[str, str]:
    # `private`, never `public`: these sit behind dashboard auth, and
    # `public` would permit a shared proxy to keep a copy.
    return {"Cache-Control": f"private, max-age={max_age}"}


def serve_image(
    raw_path: str | Path | None,
    *,
    missing: str = "image file missing",
    unset: str = "no image for this item",
    max_age: int = IMAGE_MAX_AGE,
) -> FileResponse:
    """A stored photo, or a 404 that says which of the two things is
    wrong: the row has no path, or the path has no file. Those are
    different problems -- one is a data gap, the other a lost file -- and
    collapsing them into one message cost real debugging time on the
    photo-audit queue."""
    if not raw_path:
        raise HTTPException(status_code=404, detail=unset)
    path = Path(raw_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(path, headers=_cache_control(max_age))


def serve_thumb(
    source: Path,
    cache_name: str,
    width: int,
    *,
    subdir: str = "",
) -> FileResponse:
    """Resize once to `width` and cache on disk.

    Every tile in a grid is the same width regardless of whether it came
    from a 750x1050 scan, a ~70MB archived sheet, or 245x342 reference
    art, and a page of them costs kilobytes rather than megabytes.

    Never fails the page: an unreadable or un-decodable source falls back
    to serving the original, on a shorter cache life so a transient
    failure doesn't pin the full-size file in the browser for a week.
    """
    cache_dir = Path(get_settings().scan_media_dir) / "thumbs"
    if subdir:
        cache_dir = cache_dir / subdir
    cache_dir.mkdir(parents=True, exist_ok=True)
    thumb = cache_dir / cache_name

    if not thumb.exists() or thumb.stat().st_mtime < source.stat().st_mtime:
        try:
            from PIL import Image

            with Image.open(source) as image:
                image = image.convert("RGB")
                height = round(image.height * (width / image.width))
                image.resize((width, height), Image.LANCZOS).save(
                    thumb, "JPEG", quality=THUMB_QUALITY, optimize=True
                )
        except Exception:
            return FileResponse(source, headers=_cache_control(3600))
    return FileResponse(thumb, headers=_cache_control(IMAGE_MAX_AGE))
