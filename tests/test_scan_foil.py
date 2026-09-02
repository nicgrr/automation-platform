import cv2
import numpy as np

from automation_control.models import CardVariant, CatalogCard
from automation_control.scan_ingest.foil import assess_foil, available_variants


def _card(prices, **kwargs):
    return CatalogCard(
        id="sv-test-1", set_id="sv-test", number="1", name="Test",
        raw_prices={"tcgplayer": {"prices": prices}}, **kwargs,
    )


def test_available_variants_uses_distinct_catalogue_price_keys():
    card = _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}})
    assert available_variants(card) == (CardVariant.NORMAL, CardVariant.REVERSE_HOLO)


def test_unique_catalogue_printing_is_high_confidence(tmp_path):
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"holofoil": {"market": 1}}))
    assert result.suggestion is CardVariant.HOLO
    assert result.confidence == 0.98


def test_missing_catalogue_data_is_unknown_not_normal(tmp_path):
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({}))
    assert result.candidates == ()
    assert result.suggestion is None
