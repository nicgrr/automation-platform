"""The registry of every non-Pokemon-English game the scan service knows,
independent of which catalogue source backs it -- session.py reads this to
decide a sheet's vision prompt and whether a game-specific price source
exists, without caring whether the cards came from tcgcsv.com
(tcgplayer_catalog.py) or TCGdex (tcgdex_catalog.py).

`pokemon` itself (English, pokemontcg.io) is never a key here -- it's the
default the rest of the pipeline already assumes, kept out of this table so
every lookup (`GAMES.get(game)`) naturally returns None for it and falls
through to the original Pokemon-only code paths.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    key: str          # TcgCard.game / CardSet.game
    label: str        # shown in the UI
    vision_name: str  # what the Claude vision fallback is told it's looking at


GAMES: dict[str, Game] = {g.key: g for g in (
    Game("one_piece", "One Piece", "One Piece Card Game"),
    Game("dragon_ball_fusion_world", "Dragon Ball Super: Fusion World", "Dragon Ball Super Card Game Fusion World"),
    Game("dragon_ball_super", "Dragon Ball Super (Masters)", "Dragon Ball Super Card Game"),
    # TCGplayer only carries the Panini-era (2014+) DBZ TCG; the 2000s
    # Score-era cards aren't in this catalogue at all.
    Game("dragon_ball_z", "Dragon Ball Z (Panini)", "Dragon Ball Z TCG"),
    # TCGdex, not TCGplayer -- see tcgdex_catalog.py. No US market for
    # Japanese-print cards, so this game has no entry in session.py's
    # SNAPSHOT_PRICE_SOURCES; a scan commits with price unknown rather
    # than pricing off the wrong market.
    Game("pokemon_japan", "Pokémon (Japanese)", "Japanese-language Pokémon Trading Card Game"),
    # Not on TCGplayer at all -- Japan-only arcade/card game, never sold
    # through the US market. Sourced from a retailer's public Shopify
    # catalogue instead -- see cardotaku_catalog.py for why (no official
    # API, and the one fan database found sits behind a bot-challenge with
    # no API of its own).
    Game("super_dragon_ball_heroes", "Super Dragon Ball Heroes", "Super Dragon Ball Heroes (Japanese arcade card game)"),
    # Same story, same source (cardotaku.com) -- a newer, separate Bandai
    # arcade card game from Super Dragon Ball Heroes, easy to mix up on
    # sight (found the hard way: 2026-09-22, 7 cards hand-catalogued as
    # SDBH turned out to be this game instead, identified via eBay/Google
    # Lens reverse image search).
    Game("dragon_ball_super_divers", "Dragon Ball Super Divers", "Dragon Ball Super Divers (Japanese arcade card game)"),
    # Bandai's original late-1980s/1990s Japanese vending-machine cards.
    # Their continuous collector numbers and set parts are unrelated to
    # the later Score, Panini, Masters, Fusion World, Heroes or Divers
    # games, so keeping a distinct key prevents similarly named cards from
    # being filed into the wrong Dragon Ball product line.
    Game("dragon_ball_carddass", "Dragon Ball Carddass (Japanese)", "vintage Japanese Dragon Ball Carddass Hondan cards"),
    # Collector-made foil art cards sold under DBHA numbers.  These are
    # deliberately not labelled Super Dragon Ball Heroes: the publisher's
    # own product collection calls them unofficial SDBH-inspired cards.
    Game("dragon_ball_art_card", "Dragon Ball Art Cards (Unofficial)", "unofficial Dragon Ball character art cards"),
)}

# Display names for every game the scanner knows, Pokemon included.
GAME_LABELS: dict[str, str] = {"pokemon": "Pokémon", **{g.key: g.label for g in GAMES.values()}}
