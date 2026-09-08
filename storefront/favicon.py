"""The browser-tab icon, plus the default social-share image.

Both are rasterised once from the exact same stroke geometry as the
brand mark in brand.py (not a generic placeholder, and not hand-exported
from a design tool that could drift out of sync with it) and cached to
disk on first request. No new asset pipeline: this is the same
Pillow dependency photos.py already uses for thumbnails.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw, ImageFont

from .brand import BRASS

router = APIRouter()

INK = "#111111"
PAPER = "#ffffff"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

_CACHE_DIR = Path(__file__).resolve().parent / ".generated-assets"

# The mark's own path data (brand.py:logo_mark), scaled from its 120x120
# viewBox to whatever raster size is requested, rather than kept as a
# second hand-drawn copy that could quietly diverge from the SVG.
_STROKES = [
    ((36, 84), (36, 38), INK),
    ((36, 38), (64, 84), INK),
    ((64, 84), (64, 38), INK),
    ((64, 84), (90, 84), BRASS),
]


def _draw_mark(draw: "ImageDraw.ImageDraw", scale: float, offset: tuple[float, float] = (0, 0),
               stroke_width: float = 9) -> None:
    ox, oy = offset
    width = max(2, round(stroke_width * scale))
    for (x1, y1), (x2, y2), color in _STROKES:
        p1 = (x1 * scale + ox, y1 * scale + oy)
        p2 = (x2 * scale + ox, y2 * scale + oy)
        draw.line([p1, p2], fill=color, width=width)
        r = width / 2
        for x, y in (p1, p2):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _favicon_png(size: int) -> Image.Image:
    """The mark alone, on a plain paper square -- legible down to the
    16px browsers actually render a tab icon at."""
    image = Image.new("RGB", (size, size), PAPER)
    draw = ImageDraw.Draw(image)
    pad = size * 0.16
    mark_size = size - 2 * pad
    scale = mark_size / 120
    _draw_mark(draw, scale, offset=(pad, pad))
    return image


def _social_share_png() -> Image.Image:
    """The default og:image -- 1200x630, the size every platform expects.
    Product pages override this with the item's own photo; every other
    page (home, department, cart, shipping...) falls back to this."""
    w, h = 1200, 630
    image = Image.new("RGB", (w, h), PAPER)
    draw = ImageDraw.Draw(image)

    mark_scale = 120 / 120  # 120px tall mark
    mark_h = 120
    _draw_mark(draw, mark_scale, offset=(w / 2 - 60, 90))

    wordmark_font = ImageFont.truetype(_FONT_BOLD, 108)
    tagline_font = ImageFont.truetype(_FONT_MONO, 22)

    word = "NL VAULT"
    bbox = draw.textbbox((0, 0), word, font=wordmark_font)
    tw = bbox[2] - bbox[0]
    draw.text((w / 2 - tw / 2, 250), word, font=wordmark_font, fill=INK)

    tagline = "TRADING CARDS & COLLECTIBLES · SHIPPED FROM AUSTRALIA"
    bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
    tw = bbox[2] - bbox[0]
    draw.text((w / 2 - tw / 2, 400), tagline, font=tagline_font, fill="#6b6b6b")

    return image


def _cached(name: str, builder) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _CACHE_DIR / name
    if not path.exists():
        builder().save(path)
    return path


_HEADERS = {"Cache-Control": "public, max-age=604800"}


@router.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> FileResponse:
    path = _cached("favicon-32.png", lambda: _favicon_png(32))
    return FileResponse(path, media_type="image/png", headers=_HEADERS)


@router.get("/favicon.svg", include_in_schema=False)
def favicon_svg() -> Response:
    from .brand import logo_mark

    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'>"
        f"<rect width='120' height='120' rx='20' fill='{PAPER}'/>"
        f"{logo_mark(120, framed=False)}"
        "</svg>"
    )
    return Response(svg, media_type="image/svg+xml", headers=_HEADERS)


@router.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon() -> FileResponse:
    path = _cached("favicon-180.png", lambda: _favicon_png(180))
    return FileResponse(path, media_type="image/png", headers=_HEADERS)


@router.get("/social-share.png", include_in_schema=False)
def social_share() -> FileResponse:
    path = _cached("social-share.png", _social_share_png)
    return FileResponse(path, media_type="image/png", headers=_HEADERS)
