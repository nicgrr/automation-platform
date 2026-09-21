"""Richer per-region image features for holo/reverse-holo discrimination.

The existing shadow-mode heuristic (automation_control/scan_ingest/foil.py)
uses only average brightness/glare and phash distance per region, and a
direct check against 238 real labels found those had almost no separating
power. This tries texture-based features instead (local binary patterns,
gradient energy, FFT high-frequency energy, saturation variance) computed
separately for the artwork region and the body/border region, on the
theory that foil is a *texture* difference (a fine glittery pattern), not
a brightness difference -- averaging away the texture is plausibly why the
brightness-based signal was too weak to use.
"""

import os

import joblib
import numpy as np
from PIL import Image
from skimage.feature import local_binary_pattern

MODEL_PATH = "scan_ingest_data/models/foil_classifier.joblib"
_model_cache: dict | None = None
_model_load_attempted = False

ART_REGION = (0.06, 0.10, 0.94, 0.52)  # matches identify.py's ART_REGION
LBP_POINTS = 16
LBP_RADIUS = 2

# Fixed, explicit feature order -- the trained model's coefficients are
# positional, so this list (not dict insertion order, which is an
# implementation detail) is the contract between training and inference.
_SCALAR_KEYS = ("grad_energy", "grad_std", "high_freq_ratio", "sat_std", "glare")
FEATURE_KEYS = tuple(
    [f"art_{k}" for k in _SCALAR_KEYS] + [f"art_lbp_{i}" for i in range(LBP_POINTS + 2)]
    + [f"body_{k}" for k in _SCALAR_KEYS] + [f"body_lbp_{i}" for i in range(LBP_POINTS + 2)]
    + [f"ratio_{k}" for k in ("grad_energy", "high_freq_ratio", "sat_std", "glare")]
)


def _region_masks(height, width):
    x0, y0, x1, y1 = (
        int(width * ART_REGION[0]), int(height * ART_REGION[1]),
        int(width * ART_REGION[2]), int(height * ART_REGION[3]),
    )
    art_mask = np.zeros((height, width), dtype=bool)
    art_mask[y0:y1, x0:x1] = True
    body_mask = ~art_mask
    return art_mask, body_mask


def _region_features(gray: np.ndarray, hsv: np.ndarray, mask: np.ndarray) -> dict:
    ys, xs = np.where(mask)
    if len(ys) < 100:
        return {}
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    sub_gray = gray[y0:y1, x0:x1]
    sub_hsv = hsv[y0:y1, x0:x1]
    sub_mask = mask[y0:y1, x0:x1]

    # LBP: a fine repeating micro-texture (foil's glitter) shows up as a
    # distinct histogram shape versus a flat printed surface, even when
    # average brightness is identical.
    lbp = local_binary_pattern(sub_gray, LBP_POINTS, LBP_RADIUS, method="uniform")
    lbp_vals = lbp[sub_mask]
    n_bins = LBP_POINTS + 2
    hist, _ = np.histogram(lbp_vals, bins=n_bins, range=(0, n_bins), density=True)

    # Gradient energy: foil's micro-facets create lots of small, sharp
    # brightness transitions that flat print doesn't.
    gy, gx = np.gradient(sub_gray.astype(float))
    grad_mag = np.sqrt(gx**2 + gy**2)
    grad_energy = float(np.mean(grad_mag[sub_mask]))
    grad_std = float(np.std(grad_mag[sub_mask]))

    # FFT high-frequency energy ratio: foil's diffraction pattern adds
    # broadband high-frequency content a smooth print surface lacks.
    fft = np.fft.fft2(sub_gray.astype(float))
    fft_mag = np.abs(np.fft.fftshift(fft))
    h, w = fft_mag.shape
    cy, cx = h // 2, w // 2
    low_radius = min(h, w) // 8
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    low = fft_mag[dist <= low_radius].sum()
    total = fft_mag.sum()
    high_freq_ratio = float(1 - (low / total)) if total > 0 else 0.0

    # Saturation variance: foil creates localized saturation dips at glare
    # points against otherwise-saturated print ink, raising local variance.
    sat = sub_hsv[:, :, 1][sub_mask].astype(float)
    sat_std = float(np.std(sat))

    # Existing glare proxy, kept as one more signal.
    val = sub_hsv[:, :, 2][sub_mask]
    glare = float(np.mean((val >= 235) & (sat <= 45)))

    features = {
        "grad_energy": grad_energy, "grad_std": grad_std,
        "high_freq_ratio": high_freq_ratio, "sat_std": sat_std, "glare": glare,
    }
    for i, v in enumerate(hist):
        features[f"lbp_{i}"] = float(v)
    return features


def extract_features(image_path) -> dict | None:
    try:
        with Image.open(image_path) as img:
            rgb = np.array(img.convert("RGB"))
    except Exception:
        return None
    import cv2
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    height, width = gray.shape
    art_mask, body_mask = _region_masks(height, width)

    art = _region_features(gray, hsv, art_mask)
    body = _region_features(gray, hsv, body_mask)
    if not art or not body:
        return None

    out = {}
    for k, v in art.items():
        out[f"art_{k}"] = v
    for k, v in body.items():
        out[f"body_{k}"] = v
    # Ratios: the region-vs-region comparison is plausibly more informative
    # than either region's absolute value, since a bright/glossy photo (any
    # variant) can shift both regions' absolutes together.
    for key in ("grad_energy", "high_freq_ratio", "sat_std", "glare"):
        a, b = art.get(key, 0), body.get(key, 0)
        out[f"ratio_{key}"] = (a - b) / (a + b + 1e-6)
    assert set(out) == set(FEATURE_KEYS), "extract_features drifted from FEATURE_KEYS"
    return out


def _load_model() -> dict | None:
    """Lazily loaded and cached -- this runs once per process (the scan
    service is long-running), not once per card. Missing model file is the
    expected bootstrap state (see scripts/train_foil_classifier.py's
    MIN_LABELS_PER_CLASS guard), not an error: callers fall back to having
    no prediction rather than crashing a scan session over it.
    """
    global _model_cache, _model_load_attempted
    if _model_load_attempted:
        return _model_cache
    _model_load_attempted = True
    if os.path.exists(MODEL_PATH):
        _model_cache = joblib.load(MODEL_PATH)
    return _model_cache


def predict(features: dict) -> tuple[str, float] | None:
    """(predicted variant value, probability) from the trained classifier,
    or None if no model has been trained yet. Probability is the model's
    own calibrated confidence in its top class, from cross-validated
    predict_proba -- see train_foil_classifier.py's docstring for what
    that number has actually measured out to (~97% accurate at >=0.95,
    against 177 labels as of 2026-09-19)."""
    model = _load_model()
    if model is None:
        return None
    x = np.array([[features[k] for k in model["feature_keys"]]])
    pipeline = model["pipeline"]
    proba = pipeline.predict_proba(x)[0]
    classes = pipeline.named_steps["clf"].classes_
    best = int(np.argmax(proba))
    return classes[best], float(proba[best])
