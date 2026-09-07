"""NL Vault brand assets -- the Ligature mark (concept 02) and palette.

The mark is one continuous set of strokes that reads as both letters: the
N's final stem doubles as the L's stem, and the brass foot completes the
L. Drawn as inline SVG rather than an image file so it stays crisp at any
size, inverts by inheriting currentColor, and costs no extra request.
"""

INK = "#14181d"
BRASS = "#b3852f"
BONE = "#f7f5f1"
TEAL = "#14625f"


def logo_mark(size: int = 40, framed: bool = True) -> str:
    """The icon alone -- used in the header and as a favicon."""
    frame = (
        "<rect x='10' y='10' width='100' height='100' rx='24' fill='none' "
        "stroke='currentColor' stroke-width='2' opacity='.3'/>"
        if framed else ""
    )
    return (
        f"<svg viewBox='0 0 120 120' width='{size}' height='{size}' role='img' "
        "aria-label='NL Vault' class='nl-mark'>"
        f"{frame}"
        "<g fill='none' stroke='currentColor' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'>"
        "<path d='M36,84 V38'/><path d='M36,38 L64,84'/><path d='M64,84 V38'/>"
        "</g>"
        f"<path d='M64,84 H90' fill='none' stroke='{BRASS}' stroke-width='9' stroke-linecap='round'/>"
        "</svg>"
    )


def logo_lockup(size: int = 34) -> str:
    """Mark plus wordmark, for the site header."""
    return (
        "<span class='nl-lockup'>"
        f"{logo_mark(size, framed=False)}"
        "<span class='nl-words'><b>NL</b><i>VAULT</i></span>"
        "</span>"
    )
