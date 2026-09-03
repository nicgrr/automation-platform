"""Conservative foil assessment for identified scan crops.

This starts in shadow mode: callers log the assessment but continue using
the operator-selected variant.  The catalogue can rule impossible printings
in or out, while regional image measurements provide calibration data for a
future automatic classifier without risking inventory corruption today.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

from ..models import CardVariant, CatalogCard
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
    if len(candidates) == 1:
        return FoilAssessment(candidates, candidates[0], 0.98, "only catalogue-supported printing", metrics)

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
