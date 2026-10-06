import cv2
import numpy as np

from automation_control.models import CardSet, CardVariant, CatalogCard
from automation_control.scan_ingest import foil as foil_mod
from automation_control.scan_ingest.foil import ART_REGION, _surface_metrics, assess_foil, available_variants, catalogue_variant


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


def _game_card(game, *, card_id, set_id, rarity, prices=None):
    card = CatalogCard(
        id=card_id, set_id=set_id, number="001", name="Test", rarity=rarity,
        raw_prices={"tcgplayer": {"prices": prices}} if prices is not None else None,
    )
    card.card_set = CardSet(id=set_id, name="Test set", game=game)
    return card


def test_one_piece_rare_and_super_rare_are_catalogue_holo():
    for rarity in ("R", "SR", "SEC", "L"):
        card = _game_card("one_piece", card_id=f"opjp-test-{rarity.lower()}", set_id="opjp-test", rarity=rarity)
        assert catalogue_variant(card)[0] is CardVariant.HOLO


def test_one_piece_official_common_base_is_normal_but_parallel_is_holo():
    base = _game_card("one_piece", card_id="opjp-op01-001", set_id="opjp-test", rarity="C")
    parallel = _game_card("one_piece", card_id="opjp-op01-001-p1", set_id="opjp-test", rarity="C")
    assert catalogue_variant(base)[0] is CardVariant.NORMAL
    assert catalogue_variant(parallel)[0] is CardVariant.HOLO


def test_fusion_world_rare_and_super_rare_are_catalogue_holo():
    for rarity in ("Rare", "Super Rare", "Secret Rare"):
        card = _game_card("dragon_ball_fusion_world", card_id="dragon-ball-fusion-world-test", set_id="dbfw-test", rarity=rarity)
        assert catalogue_variant(card)[0] is CardVariant.HOLO


def test_masters_plain_rare_with_both_printings_stays_undecided():
    card = _game_card(
        "dragon_ball_super", card_id="dragon-ball-super-test", set_id="dbs-test", rarity="Rare",
        prices={"normal": {"market": 1}, "holofoil": {"market": 2}},
    )
    assert catalogue_variant(card)[0] is None


def test_masters_special_rare_wins_over_bad_normal_marketplace_subtype():
    card = _game_card(
        "dragon_ball_super", card_id="tcgp-test", set_id="dbs-test", rarity="Special Rare",
        prices={"normal": {"market": 1}},
    )
    assert catalogue_variant(card)[0] is CardVariant.HOLO


def test_non_pokemon_ambiguous_finish_never_uses_pokemon_texture_model(tmp_path, monkeypatch):
    card = _game_card(
        "dragon_ball_super", card_id="dragon-ball-super-test", set_id="dbs-test", rarity="Rare",
        prices={"normal": {"market": 1}, "holofoil": {"market": 2}},
    )
    monkeypatch.setattr(foil_mod.foil_features, "extract_features", lambda *_: (_ for _ in ()).throw(AssertionError("must not run")))
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, card)
    assert result.suggestion is None
    assert "catalogue" in result.reason


def _glare_patch_image(glare_in_art: bool) -> np.ndarray:
    """A mid-gray card-shaped image with one bright, low-saturation (i.e.
    glare-reading) patch, placed either inside the artwork window or
    clearly outside it in the body/border."""
    width, height = 300, 420
    image = np.full((height, width, 3), 120, dtype=np.uint8)
    x0, y0, x1, y1 = ART_REGION
    if glare_in_art:
        ax0, ay0, ax1, ay1 = int(width * x0), int(height * y0), int(width * x1), int(height * y1)
        image[ay0:ay1, ax0:ax1] = 255
    else:
        image[0:20, 0:20] = 255  # top-left corner sits outside ART_REGION
    return image


def test_surface_metrics_glare_bias_is_negative_when_glare_sits_on_the_art(tmp_path):
    path = tmp_path / "art_glare.jpg"
    cv2.imwrite(str(path), _glare_patch_image(glare_in_art=True))
    metrics = _surface_metrics(path, _card({}, phash="0" * 16, art_phash="0" * 16))
    assert metrics["art_glare"] > metrics["body_glare"]
    assert metrics["glare_bias"] < 0  # body_glare - art_glare


def test_surface_metrics_glare_bias_is_positive_when_glare_sits_on_the_body(tmp_path):
    path = tmp_path / "body_glare.jpg"
    cv2.imwrite(str(path), _glare_patch_image(glare_in_art=False))
    metrics = _surface_metrics(path, _card({}, phash="0" * 16, art_phash="0" * 16))
    assert metrics["body_glare"] > metrics["art_glare"]
    assert metrics["glare_bias"] > 0


def test_assess_foil_notes_when_glare_direction_agrees_with_the_bias_suggestion(tmp_path, monkeypatch):
    """Reverse holo -> body_bias >= 4 suggests REVERSE_HOLO; glare_bias > 0
    means the glare itself also sits more on the body than the art -- the
    same direction, from an independent measurement."""
    monkeypatch.setattr(foil_mod, "_surface_metrics", lambda *a, **k: {"body_bias": 6.0, "glare_bias": 0.02})
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    assert result.suggestion is CardVariant.REVERSE_HOLO
    assert "glare direction agrees" in result.reason
    assert result.confidence == 0.50  # agreement is informational -- it never raises confidence


def test_assess_foil_notes_when_glare_direction_disagrees_with_the_bias_suggestion(tmp_path, monkeypatch):
    monkeypatch.setattr(foil_mod, "_surface_metrics", lambda *a, **k: {"body_bias": 6.0, "glare_bias": -0.02})
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    assert result.suggestion is CardVariant.REVERSE_HOLO
    assert "glare direction disagrees" in result.reason


def test_assess_foil_says_nothing_about_glare_when_it_suggests_normal(tmp_path, monkeypatch):
    """The agreement note is only meaningful for a holo/reverse-holo
    suggestion -- "normal" has no directional prediction to check glare
    against."""
    monkeypatch.setattr(foil_mod, "_surface_metrics", lambda *a, **k: {"body_bias": 1.0, "glare_bias": 0.02})
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    assert result.suggestion is CardVariant.NORMAL
    assert "glare" not in result.reason


def test_assess_foil_uses_the_trained_classifier_when_its_prediction_is_a_real_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(foil_mod.foil_features, "extract_features", lambda *a, **k: {"some": "features"})
    monkeypatch.setattr(foil_mod.foil_features, "predict", lambda features: ("reverse_holo", 0.97))
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    assert result.suggestion is CardVariant.REVERSE_HOLO
    assert result.confidence == 0.97
    assert "trained classifier" in result.reason


def test_assess_foil_ignores_a_classifier_prediction_the_catalogue_rules_out(tmp_path, monkeypatch):
    """The catalogue's own cached prices are still a hard fact the model
    doesn't get to override -- a card with no cached holo printing at all
    can't actually be holo, whatever the classifier guesses from the image."""
    monkeypatch.setattr(foil_mod.foil_features, "extract_features", lambda *a, **k: {"some": "features"})
    monkeypatch.setattr(foil_mod.foil_features, "predict", lambda features: ("holo", 0.99))
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    # Falls through to the heuristic instead of trusting the ruled-out guess.
    assert result.suggestion is not CardVariant.HOLO


def test_assess_foil_falls_back_to_the_heuristic_when_the_image_cant_be_read(tmp_path, monkeypatch):
    monkeypatch.setattr(foil_mod.foil_features, "extract_features", lambda *a, **k: None)
    monkeypatch.setattr(foil_mod, "_surface_metrics", lambda *a, **k: {"body_bias": 6.0, "glare_bias": 0.02})
    path = tmp_path / "card.jpg"
    cv2.imwrite(str(path), np.full((200, 140, 3), 120, dtype=np.uint8))
    result = assess_foil(path, _card({"normal": {"market": 1}, "reverseHolofoil": {"market": 2}}))
    assert result.suggestion is CardVariant.REVERSE_HOLO
    assert "trained classifier" not in result.reason
