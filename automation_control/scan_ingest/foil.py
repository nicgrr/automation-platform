"""Conservative foil assessment for identified scan crops.

No longer purely shadow mode (updated 2026-09-29 -- see session.py, which
is the actual caller and the source of truth for what happens with this
assessment): a catalogue fact (exactly one known printing exists, >=0.98
confidence) or a trained-classifier suggestion clearing
`session.CONFIDENCE_AUTOCOMMIT` (0.95, calibrated against real
cross-validated accuracy -- see foil_features.py/train_foil_classifier.py)
overrides the operator's declared batch variant outright. Below that bar,
the assessment is still logged but never acted on, so most cards -- the
ones the model isn't confident about -- fall through to the operator's own
call rather than a shaky guess.
"""

from dataclasses import dataclass, field
from pathlib import Path
import re

import cv2
import imagehash
import numpy as np
from PIL import Image

from ..models import CardVariant, CatalogCard
from . import foil_features
from .identify import ART_REGION, art_phash


@dataclass(frozen=True)
class FoilAssessment:
    candidates: tuple[CardVariant, ...]
    suggestion: CardVariant | None
    confidence: float
    reason: str
    metrics: dict[str, float] = field(default_factory=dict)

    def log_summary(self) -> str:
        possible = "/".join(v.value for v in self.candidates) or "unknown"
        suggestion = self.suggestion.value if self.suggestion else "uncertain"
        measurements = " ".join(f"{key}={value:.3f}" for key, value in self.metrics.items())
        return (
            f"foil shadow: possible={possible}, suggestion={suggestion}, "
            f"confidence={self.confidence:.2f}, reason={self.reason}"
            + (f", {measurements}" if measurements else "")
        )


def available_variants(card: CatalogCard) -> tuple[CardVariant, ...]:
    """Printings represented by the card's cached TCGPlayer price keys.

    This is evidence, not a complete printing authority: an empty result is
    kept as unknown instead of being interpreted as a normal card.
    """
    prices = (((card.raw_prices or {}).get("tcgplayer") or {}).get("prices") or {})
    variants: list[CardVariant] = []
    if prices.get("normal") or prices.get("unlimited"):
        variants.append(CardVariant.NORMAL)
    if prices.get("reverseHolofoil"):
        variants.append(CardVariant.REVERSE_HOLO)
    if prices.get("holofoil") or prices.get("1stEditionHolofoil"):
        variants.append(CardVariant.HOLO)
    return tuple(variants)


def _game_key(card: CatalogCard) -> str | None:
    """Game without requiring every unit-test card to have a loaded set row."""
    try:
        if card.card_set and card.card_set.game:
            return card.card_set.game
    except Exception:
        pass
    if card.set_id.startswith(("op-", "opjp-")):
        return "one_piece"
    if card.set_id.startswith("dbfw-"):
        return "dragon_ball_fusion_world"
    if card.set_id.startswith("dbs-"):
        return "dragon_ball_super"
    return None


def catalogue_variant(card: CatalogCard) -> tuple[CardVariant | None, str | None]:
    """Return a finish established by catalogue facts, never image texture.

    TCGplayer's exact product subtype is the strongest evidence. For the
    official Japanese caches, which do not carry TCGplayer prices, game rules
    fill the gap: One Piece R/SR/SEC/Leader cards and Dragon Ball rare-and-up
    cards are foil, and an official parallel image is foil regardless of its
    base rarity. If a catalogue explicitly exposes more than one finish we
    leave it undecided instead of flattening a real normal/foil choice.
    """
    variants = available_variants(card)
    game = _game_key(card)
    rarity = re.sub(r"[^A-Z]+", " ", (card.rarity or "").upper()).strip()
    parallel = bool(re.search(r"-p\d+$", card.id, re.I))

    if game == "one_piece":
        if parallel:
            return CardVariant.HOLO, "official One Piece parallel printing"
        if rarity in {
            "R", "SR", "SEC", "L", "SP", "TR",
            "RARE", "SUPER RARE", "SECRET RARE", "LEADER", "SPECIAL",
        } or rarity.startswith("SP "):
            return CardVariant.HOLO, f"One Piece {card.rarity} rarity is foil"
        if card.id.startswith("opjp-") and rarity in {"C", "UC", "COMMON", "UNCOMMON"}:
            return CardVariant.NORMAL, f"One Piece {card.rarity} base printing is non-foil"

    if game == "dragon_ball_fusion_world":
        if parallel:
            return CardVariant.HOLO, "official Dragon Ball parallel printing"
        if rarity in {
            "R", "SR", "SCR", "SEC", "ER", "SPR", "GDR",
            "RARE", "SUPER RARE", "SECRET RARE", "EXPANSION RARE", "SPECIAL RARE",
        }:
            return CardVariant.HOLO, f"Dragon Ball {card.rarity} rarity is foil"
        if card.id.startswith("dbfw-jp-") and rarity in {"C", "UC", "COMMON", "UNCOMMON"}:
            return CardVariant.NORMAL, f"Dragon Ball {card.rarity} base printing is non-foil"

    if game == "dragon_ball_super":
        if parallel:
            return CardVariant.HOLO, "official Dragon Ball parallel printing"
        # Masters-era plain R cards can genuinely exist in both normal and
        # foil. Higher named rarities and Expansion Rares are foil products;
        # do not let an occasional bad marketplace subtype call an SPR
        # normal (seen on BT14-033 in the live cache).
        if rarity in {
            "SR", "SCR", "SEC", "ER", "SPR", "GDR",
            "SUPER RARE", "SECRET RARE", "EXPANSION RARE", "SPECIAL RARE",
        }:
            return CardVariant.HOLO, f"Dragon Ball {card.rarity} rarity is foil"

    if len(variants) == 1:
        return variants[0], "only catalogue-supported printing"
    if len(variants) > 1:
        return None, None

    return None, None


def _surface_metrics(scan_path: Path, card: CatalogCard) -> dict[str, float]:
    """Compare reference similarity inside and outside the artwork window.

    Reverse foil normally disrupts the body more than the artwork; regular
    holo tends to do the opposite.  pHash deltas are intentionally logged
    rather than treated as calibrated probabilities yet.
    """
    if not card.phash or not card.art_phash:
        return {}
    try:
        with Image.open(scan_path) as image:
            rgb = image.convert("RGB")
            whole_distance = float(imagehash.phash(rgb) - imagehash.hex_to_hash(card.phash))
            art_distance = float(art_phash(rgb) - imagehash.hex_to_hash(card.art_phash))

        scan = cv2.imread(str(scan_path))
        if scan is None:
            return {}
        hsv = cv2.cvtColor(scan, cv2.COLOR_BGR2HSV)
        height, width = hsv.shape[:2]
        x0, y0, x1, y1 = (
            int(width * ART_REGION[0]), int(height * ART_REGION[1]),
            int(width * ART_REGION[2]), int(height * ART_REGION[3]),
        )
        art = hsv[y0:y1, x0:x1]
        body_mask = np.ones((height, width), dtype=bool)
        body_mask[y0:y1, x0:x1] = False
        # Bright, low-saturation pixels are a stable glare proxy.  Keep it
        # alongside the hash delta so later calibration can learn which
        # signal works for each set/era.
        glare = (hsv[:, :, 2] >= 235) & (hsv[:, :, 1] <= 45)
        art_glare = float(np.mean((art[:, :, 2] >= 235) & (art[:, :, 1] <= 45)))
        body_glare = float(np.mean(glare[body_mask]))
        return {
            "whole_d": whole_distance,
            "art_d": art_distance,
            "body_bias": whole_distance - art_distance,
            "art_glare": art_glare,
            "body_glare": body_glare,
            # Same "which region differs more" question as body_bias, but
            # from a literal photometric signal (bright, low-saturation
            # pixels) instead of similarity-to-reference -- an independent
            # measurement of the same thing, not a restatement of it. Kept
            # as its own metric (not folded into the suggestion) because a
            # spot-check against tonight's real log data found the two
            # signals disagreeing on the two most glare-heavy cards logged
            # so far (body_bias strongly negative -- "suggests holo" --
            # while glare sat entirely on the body, not the art). With no
            # confirmed-holo sample yet to say which signal was right, that
            # disagreement is exactly the kind of thing shadow mode exists
            # to surface, not paper over with an invented confidence boost.
            "glare_bias": body_glare - art_glare,
        }
    except Exception:
        return {}


def assess_foil(scan_path: Path, card: CatalogCard) -> FoilAssessment:
    candidates = available_variants(card)
    metrics = _surface_metrics(scan_path, card)
    known_variant, known_reason = catalogue_variant(card)
    if known_variant is not None:
        return FoilAssessment(candidates or (known_variant,), known_variant, 0.98, known_reason or "catalogue rule", metrics)

    # The texture model was trained and calibrated on Pokemon. One Piece
    # and Dragon Ball have stronger game/rarity rules, and when their own
    # catalogue says both finishes exist the model must not break that tie.
    # This also prevents impossible reverse-holo suggestions in those games.
    if _game_key(card) in {"one_piece", "dragon_ball_fusion_world", "dragon_ball_super"}:
        return FoilAssessment(candidates, None, 0.0, "catalogue does not establish one finish", metrics)

    # The trained classifier (scripts/train_foil_classifier.py) replaces the
    # weak heuristic below once it exists: a real cross-validated check
    # against 177 labels (2026-09-19) found the heuristic's body_bias signal
    # had almost no separating power between confirmed holo and reverse-holo
    # samples, while texture features (local binary patterns, gradient/FFT
    # energy, computed separately for the art vs. body region -- see
    # foil_features.py) reached ~91% cross-validated accuracy. The heuristic
    # stays below as the bootstrap fallback for a fresh install with no
    # trained model yet, and keeps collecting the same log data either way.
    image_features = foil_features.extract_features(scan_path)
    if image_features is not None:
        prediction = foil_features.predict(image_features)
        if prediction is not None:
            predicted_value, probability = prediction
            predicted = CardVariant(predicted_value)
            if predicted in candidates or not candidates:
                return FoilAssessment(
                    candidates or (predicted,), predicted, probability,
                    "trained classifier (texture features)", metrics,
                )

    suggestion: CardVariant | None = None
    reason = "collecting regional foil measurements"
    # A deliberately weak shadow suggestion.  These thresholds only create
    # labelled log data; they never select the committed inventory variant.
    bias = metrics.get("body_bias")
    if bias is not None and CardVariant.REVERSE_HOLO in candidates and bias >= 4:
        suggestion, reason = CardVariant.REVERSE_HOLO, "body differs from reference more than artwork"
    elif bias is not None and CardVariant.HOLO in candidates and bias <= -4:
        suggestion, reason = CardVariant.HOLO, "artwork differs from reference more than card body"
    elif CardVariant.NORMAL in candidates and bias is not None and abs(bias) < 4:
        suggestion, reason = CardVariant.NORMAL, "no strong regional foil bias"

    # Note whether the independent glare signal points the same way, purely
    # as calibration context -- see the comment on glare_bias above for why
    # this stays informational rather than adjusting confidence: agreement
    # here isn't yet confirmed to mean anything, only logged so it can be
    # checked against real holo/reverse-holo cards once some exist.
    glare_bias = metrics.get("glare_bias")
    if suggestion in (CardVariant.HOLO, CardVariant.REVERSE_HOLO) and glare_bias is not None:
        expected_sign = 1 if suggestion is CardVariant.REVERSE_HOLO else -1
        agrees = (glare_bias > 0) == (expected_sign > 0) if glare_bias != 0 else False
        reason += "; glare direction agrees" if agrees else "; glare direction disagrees"

    return FoilAssessment(candidates, suggestion, 0.50 if suggestion else 0.0, reason, metrics)
