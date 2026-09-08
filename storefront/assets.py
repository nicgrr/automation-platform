"""Static brand assets that are real third-party files checked into the
repo, rather than generated at request time (see favicon.py for those).

pokemon-tcg-logo.svg is the official Pokémon Trading Card Game logo
(Wikimedia Commons, "File:Pokémon Trading Card Game logo.svg"), used
here purely to identify the real, licensed product this shop sells --
nominative use for identification, not an implied endorsement by or
affiliation with The Pokémon Company. Hosted locally rather than
hotlinked so it doesn't depend on Wikimedia's own availability, and
because it's a fixed brand mark rather than something that changes.

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
}

_HEADERS = {"Cache-Control": "public, max-age=604800"}


@router.get("/assets/pokemon-tcg-logo.svg", include_in_schema=False)
def pokemon_tcg_logo() -> FileResponse:
    return FileResponse(_ASSETS_DIR / "pokemon-tcg-logo.svg", media_type="image/svg+xml", headers=_HEADERS)
