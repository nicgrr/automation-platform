"""Static brand assets that are real third-party files checked into the
repo, rather than generated at request time (see favicon.py for those).

pokemon-tcg-logo.svg is the official Pokémon Trading Card Game logo
(Wikimedia Commons, "File:Pokémon Trading Card Game logo.svg"), used
here purely to identify the real, licensed product this shop sells --
nominative use for identification, not an implied endorsement by or
affiliation with The Pokémon Company. Hosted locally rather than
hotlinked so it doesn't depend on Wikimedia's own availability, and
because it's a fixed brand mark rather than something that changes.

whatnot-icon.svg is the icon mark cropped out of Whatnot's own combination
logo (Wikimedia Commons, "File:Whatnot Logo 2025.svg", public domain) --
just the circular icon, not the "Whatnot" wordmark next to it, since this
is used at social-icon size in common.py's footer row. Same nominative-use
reasoning and same local-hosting reasoning as the Pokémon logo above; kept
in its own real brand colors (unlike the currentColor placeholder icons
next to it) since a real logo shouldn't be recolored to match arbitrary UI
text.

one-piece-logo.svg ("File:One piece logo 1.svg") and dragon-ball-logo.png
("File:Dragon Ball Logo.png") are from Wikimedia Commons, both marked public
domain there; same nominative-use and local-hosting reasoning as above.
ebay-icon.png is eBay's coloured wordmark, cropped from its app icon (supplied
by the owner) for the social icon row, identifying the marketplace the shop
sells through.

collectables-banner.jpg is composed from Sonny Angel's own official artwork
(sonnyangel.com: its white wordmark over its figures photo), cropped to the
tile's 16:9.

GAME_LOGOS is the lookup app.py's department tiles use -- a game with no
entry here just falls back to a card/lot photo instead (see
app.py:_dept_hero), rather than guessing at a logo that doesn't exist.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_ASSETS_DIR = Path(__file__).resolve().parent / "static_assets"

GAME_LOGOS = {
    "pokemon": "/assets/pokemon-tcg-logo.svg",
    "one_piece": "/assets/one-piece-logo.svg",
    "dragon_ball": "/assets/dragon-ball-logo.png",
}

# A non-game department's tile art, by slug (a game department uses
# GAME_LOGOS above).
DEPARTMENT_ART = {
    "collectibles": "/assets/collectables-banner.jpg",
}

_HEADERS = {"Cache-Control": "public, max-age=604800"}


@router.get("/assets/pokemon-tcg-logo.svg", include_in_schema=False)
def pokemon_tcg_logo() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "pokemon-tcg-logo.svg", media_type="image/svg+xml", headers=_HEADERS)


@router.get("/assets/whatnot-icon.svg", include_in_schema=False)
def whatnot_icon() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "whatnot-icon.svg", media_type="image/svg+xml", headers=_HEADERS)


@router.get("/assets/nl-vault-banner.jpg", include_in_schema=False)
def banner() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "nl-vault-banner.jpg", media_type="image/jpeg", headers=_HEADERS)


@router.get("/assets/nl-vault-banner-900.jpg", include_in_schema=False)
def banner_small() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "nl-vault-banner-900.jpg", media_type="image/jpeg", headers=_HEADERS)


@router.get("/assets/one-piece-logo.svg", include_in_schema=False)
def one_piece_logo() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "one-piece-logo.svg", media_type="image/svg+xml", headers=_HEADERS)


@router.get("/assets/dragon-ball-logo.png", include_in_schema=False)
def dragon_ball_logo() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "dragon-ball-logo.png", media_type="image/png", headers=_HEADERS)


@router.get("/assets/ebay-icon.png", include_in_schema=False)
def ebay_icon() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "ebay-icon.png", media_type="image/png", headers=_HEADERS)


@router.get("/assets/collectables-banner.jpg", include_in_schema=False)
def collectables_banner() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "collectables-banner.jpg", media_type="image/jpeg", headers=_HEADERS)
