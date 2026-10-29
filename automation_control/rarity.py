"""Rarity and printing-family helpers shared by the inventory pages, the eBay
price import and card identification.

One Piece prints a star next to the rarity on alternate-art ("parallel")
cards -- SEC★, SR★, R★ -- and a base SEC and its alt art are very different
cards worth very different money. The catalogue sources only carry the bare
rarity ("SEC") and put the alt-art-ness in the card name or parallel label, so
the star is derived from that here rather than hand-typed on every card.

Printing *families* exist because one printed code covers several physical
cards: the base card, its alt art, a manga rare and a serial-numbered chase.
Matching a listing, or a scan, to the wrong family is how a ~$20 card ends up
valued at $3,600.
"""

import re

STAR = "★"  # ★

# Bare rarities that gain a star when the printing is an alt art. SP/TR/P
# and the like are their own rarities and never get one.
STAR_ELIGIBLE = {"L", "C", "UC", "R", "SR", "SEC"}

# Offered in the rarity edit box as suggestions; free text is still allowed.
ONE_PIECE_RARITIES = [
    "C", "UC", "R", "SR", "SEC", "L", "SP", "TR", "P", "PR",
    f"R{STAR}", f"SR{STAR}", f"SEC{STAR}", f"L{STAR}", f"UC{STAR}", f"C{STAR}",
]

# Order matters only in that the first family a title matches is not special:
# every family that matches is reported.
_FAMILY_PATTERNS = {
    "manga": re.compile(r"\bmanga\b|\bmanga\s*rare\b", re.I),
    "serial": re.compile(r"\bserial(?:\s*numbered)?\b|\bsn\b", re.I),
    "wanted": re.compile(r"\bwanted\b", re.I),
    "alt": re.compile(
        r"\balt(?:ernate)?\.?(?:\s*art)?\b|\bparallel\b|\bsuper\s+alt\w*\b|\baa\b"
        # a printed star after the rarity: "SR*", "SEC★", "R ☆"
        r"|\b[A-Z]{1,3}\s?[*\u2605\u2606](?!\w)",
        re.I,
    ),
    # SP / SP-card / "special" are their own, far pricier, printing of the card
    "sp": re.compile(r"\bsp\b|\bspc\b|\bsp[\s-]*card\b|\bspecial\s+(?:foil|card)\b", re.I),
}


def printing_families(text: str | None) -> set[str]:
    """Which special-printing families a card name or listing title claims.
    An empty set means "looks like the plain base card"."""
    if not text:
        return set()
    return {family for family, pattern in _FAMILY_PATTERNS.items() if pattern.search(text)}


_NOT_A_SINGLE = re.compile(
    r"\b(?:psa|bgs|cgc|sgc|ace\s*grad\w*|graded|slab(?:bed)?)\s*\d*\b"
    r"|\b\d+\s*x\b|\bx\s*\d+\b|\bplay\s*sets?\b|\bplaysets?\b|\blots?\b|\bbundle\b"
    r"|\bchoose\b|\byour\s+(?:pick|choice|card)\b|\bsingles?\b",
    re.I,
)
_FOREIGN = re.compile(r"\b(?:jap\w*|jp|jpn|chinese|korean|thai|simplified)\b", re.I)


def is_single_raw_card(title: str | None) -> bool:
    """False for graded slabs, multi-card lots/playsets and "choose your
    card" listings -- none of which are the price of one raw copy."""
    return not _NOT_A_SINGLE.search(title or "")


def listing_is_wrong_language(title: str | None, card_language: str | None) -> bool:
    """An English card must not be priced from a Japanese/Chinese listing."""
    if (card_language or "English").lower() != "english":
        return False
    return bool(_FOREIGN.search(title or ""))


def listing_matches_card(listing_title: str | None, card_name: str | None) -> bool:
    """False when the listing is plainly a *different printing* of the card.

    A listing may claim no more special families than the card itself does:
    a base card must not be priced from manga/serial/alt-art listings, and
    an alt art must not be priced from a manga or serial-numbered one, nor
    from a listing that doesn't claim to be an alt art at all.
    """
    listing, card = printing_families(listing_title), printing_families(card_name)
    if not listing <= (card | _implied(card_name)):
        return False
    # A special printing is only priced from listings that say so: a title
    # with no printing words at all is the plain card, which is the wrong
    # (usually far cheaper) comp for an alt art, manga or serial-numbered.
    return not card or bool(listing & card)


def _implied(card_name: str | None) -> set[str]:
    # A manga or serial/wanted card is, by nature, also an "alternate art".
    families = printing_families(card_name)
    return {"alt"} if families else set()


def normalize_star(rarity: str | None) -> str | None:
    """"SEC *", "sec☆", "SEC star" -> "SEC★". Leaves everything else alone."""
    if rarity is None:
        return None
    text = rarity.strip()
    match = re.fullmatch(r"([A-Za-z]{1,4})\s*(?:[*☆★✩]|\(?star\)?)", text, re.I)
    if match:
        return match.group(1).upper() + STAR
    return text


def display_rarity(rarity: str | None, card_name: str | None = None, parallel: str | None = None) -> str:
    """Rarity as shown to people: stars an alt-art printing of a bare
    rarity ("SEC" + "(Parallel)" -> "SEC★")."""
    text = normalize_star(rarity) or ""
    if not text:
        return "Unknown"
    if STAR in text or text.upper() not in STAR_ELIGIBLE:
        return text
    if printing_families(card_name) or printing_families(parallel):
        return text + STAR
    return text
