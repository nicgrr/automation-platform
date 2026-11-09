"""Automatic identification of bulk photo uploads (2026-11-10).

Recreates the owner's 2026-11-09 Japanese One Piece batch: every card was read
correctly but held for review because language was ignored, a stray Latin
letter in a katakana name became the search prefix, romanisation differed,
and base/parallel printings could not be told apart by text.
"""
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image

from automation_control import printing_match
from automation_control.capture_promotion import resolve_catalogue_card
from automation_control.card_recognition import ExtractedAccessory, PrintingChoice
from automation_control.ebay_photo_batch import _detect_accessory
from automation_control.models import CardSet, CatalogItem, CatalogItemType, ListingPhotoPair, TcgCard


def _row(session, item_id, name, set_code, number, language, *, set_id="set", variant=None, rarity="SR", image_url=None):
    session.add(CatalogItem(id=item_id, item_type=CatalogItemType.TCG_CARD, name=name, image_url=image_url))
    session.add(TcgCard(catalog_item_id=item_id, game="one_piece", set_id=set_id, set_code=set_code,
                        number=number, character=name, rarity=rarity, variant=variant, parallel=variant,
                        language=language))


def _sets(session):
    session.add(CardSet(id="set", name="Royal Blood", code="OP10", game="one_piece"))
    session.add(CardSet(id="jp", name="ブースターパック 王族の血統【OP-10】 (Japanese)", code="OP10", game="one_piece"))


def _card(character, number, language="Japanese", set_name="", rarity="SR"):
    return SimpleNamespace(character=character, card_number=number, set_name=set_name, rarity=rarity, language=language)


def test_japanese_photo_prefers_japanese_rows_over_english_ones(session):
    _sets(session)
    _row(session, "en-1", "Tashigi", "OP10", "OP10-032", "English")
    _row(session, "en-2", "Tashigi (Parallel)", "OP10", "OP10-032", "English")
    _row(session, "jp-1", "Tashigi", "OP10", "OP10-032", "Japanese", set_id="jp")
    session.flush()

    resolved = resolve_catalogue_card(session, _card("たしぎ (Tashigi)", "OP10-032", set_name="OP-10 Royal Blood"))

    assert not isinstance(resolved, str)
    assert resolved[0].id == "jp-1"


def test_stray_latin_letter_in_katakana_does_not_hide_the_english_name(session):
    _sets(session)
    _row(session, "luffy", "Monkey.D.Luffy", "OP12", "OP12-015", "Japanese", set_id="jp")
    session.flush()

    resolved = resolve_catalogue_card(session, _card("モンキー・D・ルフィ (Monkey D. Luffy)", "OP12-015"))

    assert not isinstance(resolved, str) and resolved[0].id == "luffy"


def test_romanisation_difference_resolves_by_code_and_language_and_keeps_parallels(session):
    _sets(session)
    _row(session, "zoro", "Zoro-Juurou", "ST18", "ST18-004", "Japanese", set_id="jp")
    # A parallel filed under the set it was reprinted in must still compete.
    _row(session, "zoro-p1", "Zoro-Juurou", "OP12", "ST18-004", "Japanese", set_id="jp", variant="Parallel 1")
    _row(session, "zoro-en", "Zoro-Juurou", "ST18", "ST18-004", "English")
    session.flush()
    seen = []

    def choose(candidates):
        seen.append(sorted(item.id for item, _ in candidates))
        return next(pair for pair in candidates if pair[0].id == "zoro-p1")

    resolved = resolve_catalogue_card(session, _card("ゾロ十郎 (Zorojuro)", "ST18-004"), choose_printing=choose)

    assert seen == [["zoro", "zoro-p1"]]
    assert resolved[0].id == "zoro-p1"


def test_romanisation_fallback_never_accepts_an_unrelated_name(session):
    _sets(session)
    # Two rows, so the older "unique printed code" fallback does not apply.
    _row(session, "other", "Nami", "ST18", "ST18-004", "Japanese", set_id="jp")
    _row(session, "other-p1", "Nami", "ST18", "ST18-004", "Japanese", set_id="jp", variant="Parallel 1")
    session.flush()

    assert isinstance(resolve_catalogue_card(session, _card("ゾロ十郎 (Zorojuro)", "ST18-004")), str)


def test_printing_tie_break_is_used_only_for_one_language_and_can_decline(session):
    _sets(session)
    _row(session, "law", "Trafalgar Law", "OP10", "OP10-119", "Japanese", set_id="jp", rarity="SEC")
    _row(session, "law-p1", "Trafalgar Law", "OP10", "OP10-119", "Japanese", set_id="jp", rarity="SEC", variant="Parallel 1")
    session.flush()
    card = _card("トラファルガー・ロー (Trafalgar Law)", "OP10-119", rarity="SEC")

    held = resolve_catalogue_card(session, card, choose_printing=lambda _candidates: None)
    picked = resolve_catalogue_card(session, card, choose_printing=lambda candidates: candidates[1])

    assert isinstance(held, str) and "2 catalogue matches" in held
    assert picked[0].id == "law-p1"


def test_mixed_language_ties_are_never_sent_to_the_artwork_check(session):
    _sets(session)
    _row(session, "a", "Kaido", "OP17", "OP17-063", "English")
    _row(session, "b", "Kaido", "OP17", "OP17-063", "Japanese", set_id="jp", variant="Parallel 1")
    session.flush()
    calls = []

    result = resolve_catalogue_card(
        session, _card("カイドウ (Kaido)", "OP17-063", language=""),
        choose_printing=lambda candidates: calls.append(candidates),
    )

    assert isinstance(result, str) and calls == []


def _png(path: Path) -> Path:
    Image.new("RGB", (6, 8), "red").save(path)
    return path


def test_chooser_accepts_only_a_confident_single_pick_and_records_why(tmp_path, monkeypatch):
    refs = {"base": _png(tmp_path / "base.png"), "p1": _png(tmp_path / "p1.png")}
    monkeypatch.setattr(printing_match, "reference_image", lambda item, _settings: refs.get(item.id))
    candidates = [(SimpleNamespace(id="base"), None), (SimpleNamespace(id="p1"), None)]
    settings = SimpleNamespace(anthropic_api_key="test")

    monkeypatch.setattr(printing_match, "choose_printing",
                        lambda *_a: PrintingChoice(match=2, confident=True, reason="manga panel frame"))
    record = {}
    assert printing_match.photo_printing_chooser(tmp_path / "photo.jpg", settings, record)(candidates) == candidates[1]
    assert record == {"candidates": ["base", "p1"], "reason": "manga panel frame", "outcome": "matched", "chosen": "p1"}

    monkeypatch.setattr(printing_match, "choose_printing",
                        lambda *_a: PrintingChoice(match=1, confident=False, reason="blurry"))
    record = {}
    assert printing_match.photo_printing_chooser(tmp_path / "photo.jpg", settings, record)(candidates) is None
    assert record["outcome"] == "not confident"


def test_chooser_skips_the_model_when_any_reference_art_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(printing_match, "reference_image",
                        lambda item, _settings: _png(tmp_path / "a.png") if item.id == "a" else None)
    monkeypatch.setattr(printing_match, "choose_printing", lambda *_a: (_ for _ in ()).throw(AssertionError("called")))
    record = {}
    chooser = printing_match.photo_printing_chooser(tmp_path / "photo.jpg", SimpleNamespace(anthropic_api_key="k"), record)

    assert chooser([(SimpleNamespace(id="a"), None), (SimpleNamespace(id="b"), None)]) is None
    assert record["outcome"] == "reference art unavailable"


def test_reference_art_is_downloaded_once_and_non_images_are_rejected(tmp_path):
    from io import BytesIO
    buf = BytesIO(); Image.new("RGB", (4, 4)).save(buf, "PNG")
    hits = []

    def handler(request):
        hits.append(str(request.url))
        body = b"<html>nope</html>" if "bad" in str(request.url) else buf.getvalue()
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = SimpleNamespace(scan_cache_dir=str(tmp_path / "cache"))
    item = SimpleNamespace(id="opjp-op10-119-p1", image_url="https://cards.example/OP10-119_p1.png?260828")

    first = printing_match.reference_image(item, settings, client=client)
    second = printing_match.reference_image(item, settings, client=client)
    bad = printing_match.reference_image(SimpleNamespace(id="bad", image_url="https://cards.example/bad.png"), settings, client=client)

    assert first == second == tmp_path / "cache" / "catalogue_refs" / "opjp-op10-119-p1.png"
    assert len([h for h in hits if "OP10-119" in h]) == 1
    assert bad is None and not (tmp_path / "cache" / "catalogue_refs" / "bad.png").exists()


def test_sealed_accessory_is_named_instead_of_no_card(monkeypatch):
    from automation_control import card_recognition
    monkeypatch.setattr(card_recognition, "extract_accessory_details", lambda *_a, **_k: [ExtractedAccessory(
        brand="Pokémon Center", product_type="card sleeves", pack_size="65", color="Eevee", unreadable_fields=[])])
    pair = ListingPhotoPair(front_image_path="front.jpg", extracted={"cards": []}, issue="No card was confidently detected in this pair.")

    _detect_accessory(pair, SimpleNamespace(anthropic_api_key="k"))

    assert pair.extracted["item_kind"] == "accessory"
    assert pair.issue.startswith("Accessory detected: Pokémon Center card sleeves Eevee (65).")
