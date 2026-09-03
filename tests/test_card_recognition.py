from unittest.mock import MagicMock, patch

import pytest

from automation_control.card_recognition import ExtractedCard, ExtractedCards, RecognitionError, RotationCheck, _image_block, detect_rotation, extract_card_details


def test_image_block_base64_encodes_file(tmp_path):
    path = tmp_path / "front.jpg"
    path.write_bytes(b"fake-jpeg-bytes")
    block = _image_block(path)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/jpeg"
    assert block["source"]["data"]


def test_extract_card_details_sends_two_images_and_prompt(tmp_path):
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    front.write_bytes(b"front-bytes")
    back.write_bytes(b"back-bytes")

    fake_card = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    fake_response = MagicMock(parsed_output=ExtractedCards(cards=[fake_card]))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.parse.return_value = fake_response

        result = extract_card_details(front, back, api_key="fake-key")

        assert result == [fake_card]
        MockAnthropic.assert_called_once_with(api_key="fake-key")
        call_kwargs = mock_client.messages.parse.call_args.kwargs
        assert call_kwargs["model"] == "claude-opus-5"
        assert call_kwargs["output_format"] is ExtractedCards
        content = call_kwargs["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "image", "text"]


def test_extraction_prompts_ask_for_a_foil_observation(tmp_path):
    """Asked once, from the photo's own glare/reflections -- never inferred
    from the card's name or rarity, since that would make foil_observation
    just restate what identification already knows rather than add a new
    signal."""
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")
    fake_response = MagicMock(parsed_output=ExtractedCards(cards=[]))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        for back in (None, tmp_path / "back.jpg"):
            if back:
                back.write_bytes(b"back-bytes")
            extract_card_details(front, back, api_key="fake-key")
            content = MockAnthropic.return_value.messages.parse.call_args.kwargs["messages"][0]["content"]
            text_block = next(b for b in content if b["type"] == "text")
            assert "foil_observation" in text_block["text"]
            assert "name or rarity" in text_block["text"]


def test_extracted_card_defaults_bounding_box_to_none_and_rotation_to_zero():
    card = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    assert card.bounding_box is None
    assert card.rotation_degrees == 0
    assert card.foil_observation == ""


def test_extracted_card_accepts_a_foil_observation():
    card = ExtractedCard(
        character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common",
        language="English", graded="Raw", unreadable_fields=[], foil_observation="over the character artwork only",
    )
    assert card.foil_observation == "over the character artwork only"


def test_extracted_card_accepts_bounding_box_and_rotation():
    card = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[], bounding_box=[0.1, 0.2, 0.6, 0.9], rotation_degrees=90)
    assert card.bounding_box == [0.1, 0.2, 0.6, 0.9]
    assert card.rotation_degrees == 90


def test_extract_card_details_returns_multiple_cards_from_one_photo(tmp_path):
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")

    cards = [
        ExtractedCard(character="Zapdos ex", set_name="Promo", card_number="SVP 049", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
        ExtractedCard(character="Flareon", set_name="Promo", card_number="SVP 167", rarity="Promo", language="English", graded="Raw", unreadable_fields=[]),
    ]
    fake_response = MagicMock(parsed_output=ExtractedCards(cards=cards))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        result = extract_card_details(front, None, api_key="fake-key")
        assert result == cards


def test_extract_card_details_returns_empty_list_when_no_card_found(tmp_path):
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")

    fake_response = MagicMock(parsed_output=ExtractedCards(cards=[]))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        result = extract_card_details(front, None, api_key="fake-key")
        assert result == []


def test_extract_card_details_propagates_api_errors(tmp_path):
    front = tmp_path / "front.jpg"
    back = tmp_path / "back.jpg"
    front.write_bytes(b"front-bytes")
    back.write_bytes(b"back-bytes")

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.parse.side_effect = RuntimeError("network error")

        try:
            extract_card_details(front, back, api_key="fake-key")
            raise AssertionError("should have raised")
        except RuntimeError as exc:
            assert str(exc) == "network error"


def test_extract_card_details_raises_recognition_error_on_unparseable_response(tmp_path):
    # e.g. the photo shows cards from a different game than the prompt asks
    # about -- the model's response doesn't fit ExtractedCards, and the SDK
    # surfaces that as parsed_output=None rather than an exception.
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")
    fake_response = MagicMock(parsed_output=None)

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        with pytest.raises(RecognitionError):
            extract_card_details(front, None, api_key="fake-key")


def test_extract_card_details_defaults_to_pokemon_prompt(tmp_path):
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")
    fake_response = MagicMock(parsed_output=ExtractedCards(cards=[]))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        extract_card_details(front, None, api_key="fake-key")
        content = MockAnthropic.return_value.messages.parse.call_args.kwargs["messages"][0]["content"]
        text_block = next(b for b in content if b["type"] == "text")
        assert "Pokemon TCG" in text_block["text"]


def test_extract_card_details_accepts_a_different_game(tmp_path):
    front = tmp_path / "front.jpg"
    front.write_bytes(b"front-bytes")
    fake_response = MagicMock(parsed_output=ExtractedCards(cards=[]))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        MockAnthropic.return_value.messages.parse.return_value = fake_response
        extract_card_details(front, None, api_key="fake-key", game="One Piece Card Game")
        content = MockAnthropic.return_value.messages.parse.call_args.kwargs["messages"][0]["content"]
        text_block = next(b for b in content if b["type"] == "text")
        assert "One Piece Card Game" in text_block["text"]
        assert "Pokemon TCG" not in text_block["text"]


def test_detect_rotation_returns_degrees_from_response(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo-bytes")
    fake_response = MagicMock(parsed_output=RotationCheck(rotation_degrees=180))

    with patch("automation_control.card_recognition.Anthropic") as MockAnthropic:
        mock_client = MockAnthropic.return_value
        mock_client.messages.parse.return_value = fake_response

        result = detect_rotation(photo, api_key="fake-key")

        assert result == 180
        call_kwargs = mock_client.messages.parse.call_args.kwargs
        assert call_kwargs["output_format"] is RotationCheck
        content = call_kwargs["messages"][0]["content"]
        assert [block["type"] for block in content] == ["image", "text"]
