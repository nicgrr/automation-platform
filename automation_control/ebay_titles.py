"""Buyer-facing eBay titles for canonical single-card listings.

``ShopListing.title`` is intentionally short because it is also used in the
EzBay storefront and admin screens. eBay has a different job: its 80
characters need to identify the exact printing in search results. Keep that
formatting here so every direct Inventory API publication uses one rule.
"""

from __future__ import annotations

import re

from scripts.convert_to_ebay_template import sanitize_title

from .card_display import display_name, display_set, language_family, printed_set_code
from .models import CardSet, CatalogItem, InventoryItem, TcgCard


EBAY_TITLE_MAX_LENGTH = 80

_GAME_TITLES = {
    "pokemon": "Pokémon TCG",
    "pokemon_japan": "Pokémon TCG",
    "one_piece": "One Piece TCG",
    "dragon_ball_fusion_world": "DB Fusion World",
    "dragon_ball_super": "DB Super TCG",
    "dragon_ball_z": "Dragon Ball Z TCG",
    "dragon_ball_super_divers": "DB Super Divers",
    "super_dragon_ball_heroes": "Super DB Heroes",
    "dragon_ball_carddass": "DB Carddass",
    "dragon_ball_art_card": "Dragon Ball Art Card",
    "dragon_ball_artbox": "Dragon Ball Z Artbox",
}

_RARITY_ABBREVIATIONS = {
    "super rare": "SR",
    "secret rare": "SCR",
    "double rare": "DR",
    "ultra rare": "UR",
    "hyper rare": "HR",
    "illustration rare": "IR",
    "special illustration rare": "SIR",
    "shiny ultra rare": "Shiny UR",
    "expansion rare": "EX Rare",
    "rare rainbow": "Rainbow Rare",
    "rare secret": "Secret Rare",
}


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", sanitize_title(text or "")).strip(" -·")


def _without_embedded_number(name: str, number: str) -> str:
    """Remove a catalogue number already embedded in a verbose card name."""
    if not number or number.casefold() == "n/a":
        return name
    escaped = re.escape(number)
    name = re.sub(rf"\(\s*{escaped}\s*\)", " ", name, flags=re.IGNORECASE)
    name = re.sub(
        rf"(?:\s*#\s*|\s+-\s+){escaped}(?=\s*$|\s*[-(])",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"\(\s*\)", " ", name)
    return _clean(name)


def _printed_number(card: TcgCard, card_set: CardSet | None) -> str:
    number = _clean(card.number)
    if not number or number.casefold() == "n/a":
        return ""
    if "/" in number or card.game not in {"pokemon", "pokemon_japan"} or not number.isdigit():
        return number
    if language_family(card.language) == 'zh':
        return number  # Never infer Chinese numbering from an English/Japanese set total.
    total = (card_set.printed_total or card_set.total) if card_set else None
    if not total:
        return number
    total_text = str(total)
    width = max(3, len(total_text))
    return f"{number.zfill(width)}/{total_text.zfill(width)}"


def _finish(card: TcgCard, inventory: InventoryItem | None) -> str:
    raw = ""
    if inventory is not None:
        raw = inventory.variant.value.replace("_", " ")
    elif card.variant:
        raw = card.variant.replace("_", " ")
    value = _clean(raw).title()
    return "" if value.casefold() in {"", "normal", "standard"} else value


def _compact_set_name(name: str, game: str) -> str:
    value = _clean(name)
    value = re.sub(r"\s*\(Uncoded\)\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+Alternate Art Reprints\s*$", " Alt Art", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+Trainer Gallery\s*$", " TG", value, flags=re.IGNORECASE)
    prefixes = {
        "dragon_ball_super_divers": "Dragon Ball Super Divers ",
        "super_dragon_ball_heroes": "Super Dragon Ball Heroes ",
        "dragon_ball_carddass": "Dragon Ball Carddass ",
    }
    prefix = prefixes.get(game)
    if prefix:
        bare_prefix = prefix.strip()
        if value.casefold() == bare_prefix.casefold():
            return ""
        if value.casefold().startswith(prefix.casefold()):
            value = value[len(prefix):]
    if game == "dragon_ball_carddass":
        value = re.sub(
            r"^Hondan\s*\(Buu Saga, Part Unconfirmed\)$",
            "Hondan Buu Saga",
            value,
            flags=re.IGNORECASE,
        )
    if value.casefold() == "special anniversary set":
        value = "Special Anniversary"
    return value


def _shorten(value: str, size: int) -> str:
    if len(value) <= size:
        return value
    if size <= 1:
        return value[:size]
    shortened = value[:size].rstrip(" -·")
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0].rstrip(" -·")
    return shortened or value[:size].rstrip(" -·")


def build_single_card_ebay_title(
    catalog_item: CatalogItem,
    card: TcgCard,
    card_set: CardSet | None,
    inventory: InventoryItem | None = None,
    *,
    max_length: int = EBAY_TITLE_MAX_LENGTH,
) -> str:
    """Build an exact-printing eBay title without exceeding 80 characters."""
    raw_number = _clean(card.number)
    components = {
        "name": _without_embedded_number(_clean(display_name(catalog_item, card)), raw_number),
        "number": _printed_number(card, card_set),
        "set": _compact_set_name(display_set(card, card_set), card.game) if card_set else "",
        "rarity": _clean(card.rarity),
        "parallel": _clean(card.parallel),
        "finish": _finish(card, inventory),
        "game": _GAME_TITLES.get(card.game, _clean(card.game.replace("_", " ")).title()),
        "language": _clean(card.language or "English"),
    }
    if language_family(card.language) == 'zh':
        code = printed_set_code(card.set_code) or printed_set_code(card_set.name if card_set else '')
        if code and code.lower() not in components['number'].lower():
            components['number'] = code + ' ' + components['number']
    if components["parallel"]:
        components["name"] = _clean(re.sub(
            rf"\(\s*{re.escape(components['parallel'])}\s*\)",
            " ",
            components["name"],
            flags=re.IGNORECASE,
        ))
    verbose_game_name = {
        "dragon_ball_super_divers": "Dragon Ball Super Divers",
        "super_dragon_ball_heroes": "Super Dragon Ball Heroes",
    }.get(card.game)
    if verbose_game_name and components["name"].casefold().startswith(verbose_game_name.casefold()):
        components["name"] = _clean(components["name"][len(verbose_game_name):])
    if raw_number and ("-" in raw_number or len(raw_number) >= 4):
        components["name"] = _clean(re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(raw_number)}(?![A-Za-z0-9])",
            " ",
            components["name"],
            flags=re.IGNORECASE,
        ))
    if components["rarity"]:
        components["name"] = _clean(re.sub(
            rf"(?:^|\s+){re.escape(components['rarity'])}$",
            " ",
            components["name"],
            flags=re.IGNORECASE,
        ))
    if components["parallel"] and components["set"].casefold().endswith(" alt art"):
        components["set"] = _clean(components["set"][:-len(" alt art")])
    if components["rarity"].casefold() in {"none", "unknown", "n/a"}:
        components["rarity"] = ""
    if components["finish"] and components["finish"].casefold() in components["rarity"].casefold():
        components["finish"] = ""
    if components["parallel"] and components["parallel"].casefold() in components["rarity"].casefold():
        components["parallel"] = ""

    order = ("name", "number", "set", "rarity", "parallel", "finish", "game", "language")

    def render() -> str:
        return _clean(" ".join(components[key] for key in order if components[key]))

    title = render()
    if len(title) <= max_length:
        return title

    components["rarity"] = _RARITY_ABBREVIATIONS.get(
        components["rarity"].casefold(), components["rarity"],
    )
    components["parallel"] = re.sub(
        r"Dragon Ball Z Dokkan Battle Alternate Art", "Dokkan Alt Art",
        components["parallel"], flags=re.IGNORECASE,
    )
    components["parallel"] = re.sub(
        r"Alternate Art", "Alt Art", components["parallel"], flags=re.IGNORECASE,
    )
    title = render()

    if len(title) > max_length and components["finish"].casefold() == "holo":
        components["finish"] = ""
        title = render()

    if len(title) > max_length and components["set"]:
        # The printed card number already carries the set code. A clipped
        # set name looks unfinished and adds less search value than omitting
        # it while retaining the exact number, printing and game.
        components["set"] = ""
        title = render()

    # Keep the full card name, exact number, game and language ahead of
    # secondary descriptors when unusually verbose imported data will not
    # fit. Parallel is retained longer than rarity because it distinguishes
    # two physical printings that may otherwise share every other field.
    for key in ("finish", "rarity", "parallel"):
        if len(title) <= max_length:
            break
        components[key] = ""
        title = render()

    if len(title) > max_length:
        excess = len(title) - max_length
        components["name"] = _shorten(
            components["name"], max(12, len(components["name"]) - excess),
        )
        title = render()
    return title[:max_length]
