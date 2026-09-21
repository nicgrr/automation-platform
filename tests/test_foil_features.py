import cv2
import numpy as np

from automation_control.scan_ingest import foil_features
from automation_control.scan_ingest.foil_features import FEATURE_KEYS, extract_features, predict


def test_extract_features_returns_exactly_the_declared_feature_keys(tmp_path):
    path = tmp_path / "card.jpg"
    # A textured image, not a flat fill -- LBP/gradient features need some
    # actual variation to compute over.
    rng = np.random.default_rng(0)
    image = rng.integers(0, 255, size=(300, 210, 3), dtype=np.uint8)
    cv2.imwrite(str(path), image)

    features = extract_features(path)

    assert features is not None
    assert set(features) == set(FEATURE_KEYS)
    assert all(isinstance(v, float) for v in features.values())


def test_extract_features_returns_none_for_an_unreadable_path(tmp_path):
    assert extract_features(tmp_path / "does-not-exist.jpg") is None


def test_predict_returns_none_when_no_model_is_trained_yet(monkeypatch):
    monkeypatch.setattr(foil_features, "_load_model", lambda: None)
    assert predict({"anything": 1.0}) is None


def test_predict_returns_the_top_class_and_its_probability(monkeypatch):
    class _FakeClf:
        classes_ = np.array(["holo", "normal", "reverse_holo"])

    class _FakePipeline:
        named_steps = {"clf": _FakeClf()}

        def predict_proba(self, x):
            return np.array([[0.1, 0.05, 0.85]])

    monkeypatch.setattr(
        foil_features, "_load_model",
        lambda: {"pipeline": _FakePipeline(), "feature_keys": ["a", "b"]},
    )

    result = predict({"a": 1.0, "b": 2.0})

    assert result == ("reverse_holo", 0.85)
