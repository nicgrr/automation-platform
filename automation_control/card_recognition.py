import base64
import mimetypes
from pathlib import Path

from anthropic import Anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5"

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

EXTRACTION_PROMPT_FRONT_AND_BACK = (
    "Identify every distinct Pokemon TCG card visible in these front and back "
    "photos -- there may be just one card, or several laid out together in the "
    "same shot. Return one entry per physical card. For each card, extract "
    "character/card name, set name, card number, rarity, language, and "
    "grading status (\"Raw\" if ungraded, or the grading company and number "
    "if slabbed). If a field for a given card is not clearly legible, list "
    "its name in that card's unreadable_fields rather than guessing a value "
    "for it. If no card is clearly identifiable in the photo, return an "
    "empty list. " + _BBOX_INSTRUCTIONS
)

EXTRACTION_PROMPT_FRONT_ONLY = (
    "Identify every distinct Pokemon TCG card visible in this front photo -- "
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
    "in the photo, return an empty list. " + _BBOX_INSTRUCTIONS
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


def extract_card_details(front_path: Path, back_path: Path | None, api_key: str) -> list[ExtractedCard]:
    content = [_image_block(front_path)]
    if back_path is not None:
        content.append(_image_block(back_path))
        content.append({"type": "text", "text": EXTRACTION_PROMPT_FRONT_AND_BACK})
    else:
        content.append({"type": "text", "text": EXTRACTION_PROMPT_FRONT_ONLY})

    client = Anthropic(api_key=api_key)
    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
        output_format=ExtractedCards,
    )
    return response.parsed_output.cards


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
