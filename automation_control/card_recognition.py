import base64
import mimetypes
from pathlib import Path

from anthropic import Anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5"
DEFAULT_GAME = "Pokemon TCG"

_BBOX_INSTRUCTIONS = (
    "For each card, also return its bounding_box in the FRONT photo: "
    "[x_min, y_min, x_max, y_max], each a fraction from 0 to 1 of the image's "
    "width (x) or height (y), drawn tightly around just that one physical "
    "card (not the others in frame). This is used to crop each card out into "
    "its own image, so favor a slightly generous box over a too-tight one "
    "that clips an edge. Also return rotation_degrees: how many degrees "
    "clockwise the cropped box must be rotated so the card reads upright "
    "(0, 90, 180, or 270 -- 0 if it's already upright)."
)

_FOIL_INSTRUCTIONS = (
    "Also return foil_observation: a short, literal description of where you "
    "can see holographic or foil texture on the card IN THIS PHOTO -- for "
    "example 'over the character artwork only', 'over the body/border but "
    "not the artwork', 'the entire card', or 'no foil visible'. Base this "
    "purely on what the glare and reflections in the photo actually show, "
    "never on the card's name or rarity -- leave it an empty string if the "
    "photo's lighting doesn't make it possible to tell."
)


class RecognitionError(Exception):
    """The model's response didn't match the expected schema -- e.g. the
    photo shows cards from a different game than the one asked about, or a
    transient bad response. Distinct from a successfully-parsed empty list
    (which just means no card was found, a normal outcome)."""


def _extraction_prompt_front_and_back(game: str) -> str:
    return (
        f"Identify every distinct {game} card visible in these front and back "
        "photos -- there may be just one card, or several laid out together in the "
        "same shot. Return one entry per physical card. For each card, extract "
        "character/card name, set name, card number, rarity, language, and "
        "grading status (\"Raw\" if ungraded, or the grading company and number "
        "if slabbed). If a field for a given card is not clearly legible, list "
        "its name in that card's unreadable_fields rather than guessing a value "
        "for it. If no card is clearly identifiable in the photo, return an "
        "empty list. " + _BBOX_INSTRUCTIONS + " " + _FOIL_INSTRUCTIONS
    )


def _extraction_prompt_front_only(game: str) -> str:
    return (
        f"Identify every distinct {game} card visible in this front photo -- "
        "there may be just one card, or several laid out together in the same "
        "shot (no back photo was provided). Return one entry per physical card. "
        "For each card, extract character/card name, set name, card number, "
        "rarity, and language from what's visible. For grading status, note "
        "\"Raw\" only if the card clearly isn't in a graded slab; if it is "
        "slabbed, read the grading company and grade off the visible label, but "
        "list graded in that card's unreadable_fields if the certification "
        "number isn't visible from the front alone. If a field for a given card "
        "is not clearly legible, list its name in that card's unreadable_fields "
        "rather than guessing a value for it. If no card is clearly identifiable "
        "in the photo, return an empty list. " + _BBOX_INSTRUCTIONS + " " + _FOIL_INSTRUCTIONS
    )


class ExtractedCard(BaseModel):
    character: str
    set_name: str
    card_number: str
    rarity: str
    language: str
    graded: str
    unreadable_fields: list[str]
    bounding_box: list[float] | None = None
    rotation_degrees: int = 0
    foil_observation: str = ""


class ExtractedCards(BaseModel):
    cards: list[ExtractedCard]


class RotationCheck(BaseModel):
    rotation_degrees: int


ROTATION_CHECK_PROMPT = (
    "This photo shows one or more Pokemon TCG cards. Determine how many "
    "degrees clockwise the ENTIRE PHOTO must be rotated so the card text is "
    "upright and reads left-to-right. Respond with rotation_degrees: 0, 90, "
    "180, or 270 (0 if it's already upright)."
)


def _image_block(path: Path) -> dict:
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def extract_card_details(front_path: Path, back_path: Path | None, api_key: str, game: str = DEFAULT_GAME) -> list[ExtractedCard]:
    content = [_image_block(front_path)]
    if back_path is not None:
        content.append(_image_block(back_path))
        content.append({"type": "text", "text": _extraction_prompt_front_and_back(game)})
    else:
        content.append({"type": "text", "text": _extraction_prompt_front_only(game)})

    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractedCards,
    )
    if response.parsed_output is None:
        raise RecognitionError(f"model did not return a response matching the expected schema (game={game!r})")
    return response.parsed_output.cards


_COLLECTIBLE_PROMPT = (
    "Identify every distinct collectible figure visible in this photo -- "
    "Sonny Angel, Smiski, or similar blind-box vinyl/mini figures. There may "
    "be just one, or several laid out together. For each, extract: brand "
    "(e.g. \"Sonny Angel\", \"Smiski\"), series (the theme, e.g. \"Fruits "
    "Series\"), character (the specific motif, e.g. \"Peach\"), variant "
    "(colourway or pose if the same character comes in more than one), "
    "is_secret (true only if this is visibly a rare/chase/secret figure -- "
    "false by default, never guess true from rarity alone), and "
    "blind_box_series (the specific numbered box line this came from, if "
    "identifiable from packaging in the photo). If a field isn't clearly "
    "identifiable, list its name in that figure's unreadable_fields rather "
    "than guessing a value for it. If no collectible figure is clearly "
    "identifiable in the photo, return an empty list. "
    "For each figure, also return its bounding_box: [x_min, y_min, x_max, "
    "y_max], each a fraction from 0 to 1 of the image's width (x) or height "
    "(y), drawn tightly around just that one figure including its headpiece "
    "(not the ones beside it). This is used to crop each figure into its own "
    "image for review, so favor a slightly generous box over a tight one "
    "that clips the head."
)


class ExtractedCollectible(BaseModel):
    brand: str
    series: str
    character: str
    variant: str
    is_secret: bool
    blind_box_series: str
    unreadable_fields: list[str]
    bounding_box: list[float] | None = None


class ExtractedCollectibles(BaseModel):
    collectibles: list[ExtractedCollectible]


def extract_collectible_details(photo_path: Path, api_key: str, max_tokens: int = 16384,
                                context: str = "") -> list[ExtractedCollectible]:
    """The Module 19 counterpart to extract_card_details, for non-TCG
    collectibles (Sonny Angel, Smiski, blind boxes) rather than cards.
    Same model, same single-image-plus-prompt shape, same contract: never
    raises for "nothing recognizable in the photo" (an empty list), only
    for a response that doesn't parse against the expected schema at all.
    The caller (collectibles.py) is what makes this non-destructive --
    results land in CapturedCollectible pending human confirmation, never
    straight into the catalogue.
    """
    # 2048 was sized for a photo of one figure. A display-cabinet shelf
    # holds forty-odd, and a truncated response fails the schema parse
    # outright -- the whole batch is lost, not partially returned -- so the
    # ceiling is a parameter with headroom. Bounding boxes cost another
    # four floats per figure, which is what pushed 42 figures past 8192.
    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": [
            _image_block(photo_path),
            {"type": "text", "text": _COLLECTIBLE_PROMPT + (f"\n\n{context}" if context else "")},
        ]}],
        output_format=ExtractedCollectibles,
    )
    if response.parsed_output is None:
        raise RecognitionError("model did not return a response matching the expected schema for collectibles")
    return response.parsed_output.collectibles


_LISTING_PROMPT = (
    "The following is a marketplace listing for one or more trading cards -- "
    "either a screenshot/photo of the listing text, or the text itself. "
    "Extract every distinct card being offered as a separate item. For each, "
    "extract: description (the character/card name and any descriptive title "
    "text, e.g. \"Divine Departure\" or \"Alternate Art\"), set_code (the "
    "letter+number prefix before a dash in a set/card code, e.g. \"OP13\" "
    "from \"OP13-076\", or \"PRB02\" from \"PRB02-001\" -- leave unreadable if "
    "the listing gives no such code), card_number (the digits after the dash, "
    "preserving any leading zeros, e.g. \"076\"), condition (e.g. \"NM\", "
    "\"LP\", or \"unknown\" if not stated), and asking_price (the numeric "
    "price only -- ignore \"ONO\"/\"OBO\"/\"obo\" wording and currency "
    "symbols). If a field isn't clearly present for a given item, list its "
    "name in that item's unreadable_fields rather than guessing a value. If "
    "no price is stated at all for an item, list asking_price in "
    "unreadable_fields and use 0 as its value. If nothing resembling a card "
    "listing is present, return an empty list."
)


class ExtractedListingItem(BaseModel):
    description: str
    set_code: str
    card_number: str
    condition: str
    asking_price: float
    unreadable_fields: list[str]


class ExtractedListing(BaseModel):
    items: list[ExtractedListingItem]


def extract_listing_items(api_key: str, photo_path: Path | None = None, text: str | None = None) -> list[ExtractedListingItem]:
    """Read a marketplace listing (a screenshot/photo of one, pasted text,
    or both) and extract each card being offered with its asking price --
    the text-based counterpart to extract_card_details, which reads a photo
    of the physical card itself rather than a seller's description of it.
    At least one of photo_path/text must be given. Same contract as the
    other extract_* functions: an empty list means nothing was found, not
    an error; RecognitionError means the model's response didn't parse.
    """
    if photo_path is None and not (text and text.strip()):
        raise RecognitionError("extract_listing_items needs a photo, some text, or both")

    content: list[dict] = []
    if photo_path is not None:
        content.append(_image_block(photo_path))
    if text and text.strip():
        content.append({"type": "text", "text": f"Listing text:\n\n{text.strip()}"})
    content.append({"type": "text", "text": _LISTING_PROMPT})

    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractedListing,
    )
    if response.parsed_output is None:
        raise RecognitionError("model did not return a response matching the expected schema for a listing")
    return response.parsed_output.items


def detect_rotation(image_path: Path, api_key: str) -> int:
    """Ask the AI how many degrees clockwise a single already-saved photo
    needs to be rotated to be upright. Used for retroactively fixing photos
    captured before auto-orientation existed -- not part of the normal
    capture flow, which gets its per-card rotation from extract_card_details
    instead.
    """
    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": [_image_block(image_path), {"type": "text", "text": ROTATION_CHECK_PROMPT}]}],
        output_format=RotationCheck,
    )
    return response.parsed_output.rotation_degrees


_COLLECTIBLE_LISTING_PROMPT = (
    "The text below is a marketplace listing (eBay, Facebook Marketplace, a "
    "shop page) for one or more collectible figures -- Sonny Angel, Smiski or "
    "similar blind-box vinyl figures. It is raw page text and will contain "
    "navigation, related items and other noise; identify only the figure the "
    "listing is actually SELLING. Extract: brand (e.g. \"Sonny Angel\"), "
    "series (the theme line, e.g. \"Fruit Series\", \"Animal Series 4\", "
    "\"Hippers Harvest Series\"), character (the specific motif, e.g. "
    "\"Peach\"), variant, is_secret (true only if the listing says secret, "
    "chase, or rare -- never inferred from a high price alone), quantity "
    "(how many figures are included; 1 unless it clearly says otherwise), "
    "is_sealed (true if it's an unopened blind box rather than a loose "
    "figure), asking_price (the numeric price only, no currency symbol; 0 if "
    "no price is stated) and currency (e.g. \"AUD\", \"USD\"). Put the name "
    "of any field you cannot determine into unreadable_fields rather than "
    "guessing it. If the text is not a listing for a collectible figure at "
    "all, return an empty list."
)


class ExtractedCollectibleListing(BaseModel):
    brand: str
    series: str
    character: str
    variant: str
    is_secret: bool
    quantity: int
    is_sealed: bool
    asking_price: float
    currency: str
    unreadable_fields: list[str]


class ExtractedCollectibleListings(BaseModel):
    listings: list[ExtractedCollectibleListing]


def extract_collectible_listing(text: str, api_key: str, max_tokens: int = 2048) -> list[ExtractedCollectibleListing]:
    """Identify the collectible a marketplace listing is selling.

    Text rather than an image, because the input is a pasted link whose
    page has been fetched. Same contract as the other extractors: an empty
    list means "nothing recognizable", which is a normal answer, not an
    error.
    """
    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": f"{_COLLECTIBLE_LISTING_PROMPT}\n\n---\n\n{text[:60000]}"}],
        output_format=ExtractedCollectibleListings,
    )
    if response.parsed_output is None:
        raise RecognitionError("model did not return a response matching the expected schema for a collectible listing")
    return response.parsed_output.listings
