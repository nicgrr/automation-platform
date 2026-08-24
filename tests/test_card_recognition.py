from unittest.mock import MagicMock, patch

from automation_control.card_recognition import ExtractedCard, ExtractedCards, RotationCheck, _image_block, detect_rotation, extract_card_details


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


def test_extracted_card_defaults_bounding_box_to_none_and_rotation_to_zero():
    card = ExtractedCard(character="Pikachu", set_name="Base Set", card_number="58/102", rarity="Common", language="English", graded="Raw", unreadable_fields=[])
    assert card.bounding_box is None
    assert card.rotation_degrees == 0


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
