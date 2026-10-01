from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from automation_control.adapters.r2 import R2UploadError, ensure_uploaded, is_configured

KWARGS = dict(
    endpoint_url="https://r2.example.com", access_key_id="key", secret_access_key="secret",
    bucket_name="bucket", public_base_url="https://pub.example.com/",
)


@dataclass
class FakeSettings:
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None


def test_is_configured_true_when_every_field_is_set():
    assert is_configured(FakeSettings("e", "k", "s", "b")) is True


@pytest.mark.parametrize("missing", ["r2_endpoint_url", "r2_access_key_id", "r2_secret_access_key", "r2_bucket_name"])
def test_is_configured_false_when_any_single_field_is_missing(missing):
    fields = {"r2_endpoint_url": "e", "r2_access_key_id": "k", "r2_secret_access_key": "s", "r2_bucket_name": "b"}
    fields[missing] = None
    assert is_configured(FakeSettings(**fields)) is False


def _not_found_error():
    return ClientError({"Error": {"Code": "404"}}, "HeadObject")


def test_ensure_uploaded_uploads_a_key_that_has_never_been_seen(tmp_path):
    photo = tmp_path / "card.jpg"
    photo.write_bytes(b"fake-bytes")
    client = MagicMock()
    client.head_object.side_effect = _not_found_error()

    with patch("automation_control.adapters.r2._client", return_value=client):
        url = ensure_uploaded(photo, "scan/abc.jpg", **KWARGS)

    client.upload_file.assert_called_once()
    assert url == "https://pub.example.com/scan/abc.jpg"


def test_ensure_uploaded_skips_the_upload_when_the_key_is_already_current(tmp_path):
    """The whole point of this function over upload_card_image: an export
    that runs again against an unchanged photo must not re-upload it and
    burn R2's write quota for nothing."""
    photo = tmp_path / "card.jpg"
    photo.write_bytes(b"fake-bytes")
    client = MagicMock()
    client.head_object.return_value = {"LastModified": datetime.now(UTC) + timedelta(days=1)}

    with patch("automation_control.adapters.r2._client", return_value=client):
        url = ensure_uploaded(photo, "scan/abc.jpg", **KWARGS)

    client.upload_file.assert_not_called()
    assert url == "https://pub.example.com/scan/abc.jpg"


def test_ensure_uploaded_re_uploads_when_the_local_file_is_newer(tmp_path):
    """A re-scan (a better photo replacing a worse one) writes a new mtime
    on the same path -- this is what makes that visible in R2 too, the
    same "changed since" reasoning storefront/photos.py's own thumbnail
    cache already uses."""
    photo = tmp_path / "card.jpg"
    photo.write_bytes(b"fake-bytes")
    client = MagicMock()
    client.head_object.return_value = {"LastModified": datetime.now(UTC) - timedelta(days=1)}

    with patch("automation_control.adapters.r2._client", return_value=client):
        ensure_uploaded(photo, "scan/abc.jpg", **KWARGS)

    client.upload_file.assert_called_once()


def test_ensure_uploaded_raises_when_the_local_file_is_missing(tmp_path):
    with pytest.raises(R2UploadError, match="does not exist"):
        ensure_uploaded(tmp_path / "missing.jpg", "scan/abc.jpg", **KWARGS)


def test_ensure_uploaded_raises_on_a_real_client_error_rather_than_treating_it_as_missing(tmp_path):
    """Only a 404/NoSuchKey means "not uploaded yet". Anything else (bad
    credentials, a network blip) is a real failure the caller needs to
    know about, not silently treated as "go ahead and upload"."""
    photo = tmp_path / "card.jpg"
    photo.write_bytes(b"fake-bytes")
    client = MagicMock()
    client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")

    with patch("automation_control.adapters.r2._client", return_value=client):
        with pytest.raises(R2UploadError, match="failed to check"):
            ensure_uploaded(photo, "scan/abc.jpg", **KWARGS)

    client.upload_file.assert_not_called()


def test_ensure_uploaded_raises_when_the_upload_itself_fails(tmp_path):
    photo = tmp_path / "card.jpg"
    photo.write_bytes(b"fake-bytes")
    client = MagicMock()
    client.head_object.side_effect = _not_found_error()
    client.upload_file.side_effect = ClientError({"Error": {"Code": "500"}}, "PutObject")

    with patch("automation_control.adapters.r2._client", return_value=client):
        with pytest.raises(R2UploadError, match="failed to upload"):
            ensure_uploaded(photo, "scan/abc.jpg", **KWARGS)
