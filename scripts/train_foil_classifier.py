"""Train the holo/reverse-holo/normal classifier from real /foil-review
labels and save it for scan_ingest/foil.py to load.

Why this exists: automation_control/scan_ingest/foil.py's original shadow
heuristic (whole-card vs. art-region phash distance, "body_bias") was
checked against 238 real labels early in this project and found to have
almost no separating power -- confirmed/rejected ranges nearly completely
overlapped. That doesn't mean the problem is unsolvable, just that
*brightness-averaged* signals wash out the actual tell: foil is a
*texture* (a fine glittery diffraction pattern), not a brightness
difference. automation_control/scan_ingest/foil_features.py computes
texture features instead (local binary patterns, gradient energy, FFT
high-frequency ratio, saturation variance), each computed separately for
the artwork region and the body/border region -- holo concentrates foil
texture in the artwork, reverse holo in the body/border.

A 5-fold cross-validated logistic regression on this feature set scores
~91% overall accuracy (91% recall on reverse holo, 91% recall on holo,
90% recall on normal) on the 177 labels available 2026-09-19 -- a real,
measured result, not a guess. Restricting to predictions the model is at
least 95% confident in covers about half the labels at ~97% accuracy on
that half; see assess_foil's use of CONFIDENCE_AUTOCOMMIT for how that
threshold is applied. Both numbers should be expected to improve as more
/foil-review labels accumulate -- re-run this script periodically (it
always retrains from scratch on the current full label set, there's no
incremental state to manage).

    .venv/bin/python -m scripts.train_foil_classifier
    .venv/bin/python -m scripts.train_foil_classifier --report-only  # cross-validate, don't save
"""

import argparse
import json
import os
import sys

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import FoilLabel, InventoryItem
from automation_control.scan_ingest.foil_features import FEATURE_KEYS, extract_features

MODEL_PATH = "scan_ingest_data/models/foil_classifier.joblib"
MIN_LABELS_PER_CLASS = 10


def _load_dataset() -> tuple[list[dict], list[str]]:
    features, labels = [], []
    with SessionLocal() as session:
        rows = session.scalars(select(FoilLabel)).all()
        for row in rows:
            if row.actual_variant is None:
                continue
            inv = session.get(InventoryItem, row.inventory_item_id)
            if not inv or not inv.scan_image_path or not os.path.exists(inv.scan_image_path):
                continue
            feats = extract_features(inv.scan_image_path)
            if feats is None:
                continue
            features.append(feats)
            labels.append(row.actual_variant.value)
    return features, labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-only", action="store_true", help="cross-validate and print a report, don't save a model")
    args = parser.parse_args(argv)

    print("loading labeled images and extracting features (this reads every labeled scan image)...")
    features, labels = _load_dataset()
    counts = {label: labels.count(label) for label in set(labels)}
    print(f"{len(labels)} usable labeled examples: {counts}")

    # A class below the minimum is excluded, not a reason to refuse to
    # train at all -- CardVariant grew a 4th and 5th value (pokeball/
    # masterball pattern) that will start at 0-1 real examples, and a
    # couple of those shouldn't freeze retraining for normal/holo/
    # reverse_holo, which already have plenty. Once a thin class crosses
    # the threshold it joins the trained set on the next run automatically.
    thin = {label for label, count in counts.items() if count < MIN_LABELS_PER_CLASS}
    if thin:
        print(f"excluding {sorted(thin)} -- fewer than {MIN_LABELS_PER_CLASS} labeled examples "
              f"so far, keep confirming those at /foil-review")
        features = [f for f, label in zip(features, labels) if label not in thin]
        labels = [label for label in labels if label not in thin]

    if len(set(labels)) < 2:
        print(f"error: need at least 2 classes with {MIN_LABELS_PER_CLASS}+ labeled examples each "
              f"to train honestly -- keep confirming cards at /foil-review", file=sys.stderr)
        return 1

    X = np.array([[f[k] for k in FEATURE_KEYS] for f in features])
    y = np.array(labels)

    pipeline = Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
    ])

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    preds = cross_val_predict(pipeline, X, y, cv=cv)
    print("\n5-fold cross-validated performance (this is the honest estimate, not training accuracy):")
    print(classification_report(y, preds, zero_division=0))
    print("confusion matrix (rows=true, cols=pred), labels:", sorted(set(y)))
    print(confusion_matrix(y, preds, labels=sorted(set(y))))

    if args.report_only:
        print("\n--report-only: no model saved.")
        return 0

    pipeline.fit(X, y)
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"pipeline": pipeline, "feature_keys": FEATURE_KEYS, "n_training_examples": len(labels)}, MODEL_PATH)
    print(f"\nsaved model trained on all {len(labels)} examples to {MODEL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
