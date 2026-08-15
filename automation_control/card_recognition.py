import base64
import mimetypes
from pathlib import Path

from anthropic import Anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5"

EXTRACTION_PROMPT_FRONT_AND_BACK = (
    "Identify every distinct Pokemon TCG card visible in these front and back "
    "photos -- there may be just one card, or several laid out together in the "
    "same shot. Return one entry per physical card. For each card, extract "
    "character/card name, set name, card number, rarity, language, and "
    "grading status (\"Raw\" if ungraded, or the grading company and number "
    "if slabbed). If a field for a given card is not clearly legible, list "
    "its name in that card's unreadable_fields rather than guessing a value "
    "for it. If no card is clearly identifiable in the photo, return an "
    "empty list."
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
    "in the photo, return an empty list."
)


class ExtractedCard(BaseModel):
    character: str
    set_name: str
    card_number: str
    rarity: str
    language: str
    graded: str
    unreadable_fields: list[str]


class ExtractedCards(BaseModel):
    cards: list[ExtractedCard]


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
