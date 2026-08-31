from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from automation_control.adapters.r2 import R2UploadError, upload_card_image

CREDS = dict(endpoint_url="https://r2.example.com", access_key_id="key", secret_access_key="secret", bucket_name="my-bucket", public_base_url="https://pub.example.com/cards")


def test_upload_card_image_raises_if_file_missing(tmp_path):
    missing = tmp_path / "nope.jpg"
    with pytest.raises(R2UploadError, match="does not exist"):
        upload_card_image(missing, "captured/x-front.jpg", **CREDS)


def test_upload_card_image_returns_public_url_on_success(tmp_path):
    local = tmp_path / "front.jpg"
    local.write_bytes(b"fake image bytes")

    with patch("automation_control.adapters.r2.boto3.client") as mock_boto_client:
        mock_client = mock_boto_client.return_value
        url = upload_card_image(local, "captured/abc-front.jpg", **CREDS)

    assert url == "https://pub.example.com/cards/captured/abc-front.jpg"
    mock_client.upload_file.assert_called_once_with(str(local), "my-bucket", "captured/abc-front.jpg", ExtraArgs={"ContentType": "image/jpeg"})


def test_upload_card_image_detects_png_content_type(tmp_path):
    local = tmp_path / "front.png"
    local.write_bytes(b"fake png bytes")

    with patch("automation_control.adapters.r2.boto3.client") as mock_boto_client:
        mock_client = mock_boto_client.return_value
        upload_card_image(local, "captured/abc-front.png", **CREDS)

    assert mock_client.upload_file.call_args.kwargs["ExtraArgs"]["ContentType"] == "image/png"


def test_upload_card_image_strips_trailing_slash_from_base_url(tmp_path):
    local = tmp_path / "front.jpg"
    local.write_bytes(b"x")
    creds = {**CREDS, "public_base_url": "https://pub.example.com/cards/"}

    with patch("automation_control.adapters.r2.boto3.client"):
        url = upload_card_image(local, "captured/abc-front.jpg", **creds)

    assert url == "https://pub.example.com/cards/captured/abc-front.jpg"


def test_upload_card_image_wraps_boto_errors(tmp_path):
    local = tmp_path / "front.jpg"
    local.write_bytes(b"x")

    with patch("automation_control.adapters.r2.boto3.client") as mock_boto_client:
        mock_client = mock_boto_client.return_value
        mock_client.upload_file.side_effect = ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "PutObject")
        with pytest.raises(R2UploadError, match="failed to upload"):
            upload_card_image(local, "captured/abc-front.jpg", **CREDS)
