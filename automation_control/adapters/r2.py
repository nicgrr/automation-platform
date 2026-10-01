"""Uploads captured-card images to Cloudflare R2 so eBay's Inventory API has
a public HTTPS URL to point at. Only used by the DB-driven capture ->
price-review -> listings-review pipeline -- the Excel-driven CLI still
expects images to already be uploaded by hand, per UPLOAD_INSTRUCTIONS.md.

R2 is S3-compatible, so this is a thin boto3 client rather than hand-rolled
httpx like the other adapters -- there's no benefit to reimplementing
multipart upload/signing ourselves.
"""

from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError


class R2UploadError(Exception):
    """Could not upload an image to R2. Callers should surface this rather
    than silently skipping the image -- a listing built with a missing or
    unreachable photo URL is worse than one that fails to build at all."""


def _client(*, endpoint_url: str, access_key_id: str, secret_access_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
        config=BotoConfig(signature_version="s3v4"),
    )


def upload_card_image(
    local_path: str | Path,
    key: str,
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    bucket_name: str,
    public_base_url: str,
) -> str:
    """Upload one local image file to R2 under `key` and return its public
    URL (public_base_url + key -- matches config.toml's [images].base_url
    convention already used by the Excel-driven export path)."""
    path = Path(local_path)
    if not path.exists():
        raise R2UploadError(f"local image file does not exist: {path}")

    content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    try:
        client = _client(endpoint_url=endpoint_url, access_key_id=access_key_id, secret_access_key=secret_access_key)
        client.upload_file(str(path), bucket_name, key, ExtraArgs={"ContentType": content_type})
    except (BotoCoreError, ClientError) as exc:
        raise R2UploadError(f"failed to upload {path} to R2 as {key}: {exc}") from exc

    return f"{public_base_url.rstrip('/')}/{key}"


def is_configured(settings) -> bool:
    """Whether every setting `upload_card_image`/`ensure_uploaded` needs is
    actually present. One definition -- listings_review.py used to keep its
    own private copy of exactly this check; that's now just an import, so
    the two call sites (the captured-card pipeline, and the scan-ingest
    bulk-export path below) can never quietly disagree about what
    "configured" means."""
    return bool(all([
        getattr(settings, "r2_endpoint_url", None),
        getattr(settings, "r2_access_key_id", None),
        getattr(settings, "r2_secret_access_key", None),
        getattr(settings, "r2_bucket_name", None),
    ]))


def ensure_uploaded(
    local_path: str | Path,
    key: str,
    *,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    bucket_name: str,
    public_base_url: str,
) -> str:
    """Like `upload_card_image`, but skips the upload (and its network
    round-trip) when `key` already holds this exact file.

    `upload_card_image`'s caller (a fresh single-item capture) always has
    a new photo worth pushing, so it uploads unconditionally -- that
    behaviour is untouched here. This one is for a scanned photo that gets
    looked up on every export run of a set that might not have changed
    since the last one: re-uploading an unchanged file on every run would
    just burn R2's write-operation quota for nothing. HEAD first (one
    cheap read), and only PUT when the key is missing or the local file
    is newer than what's already stored -- the same "changed since"
    reasoning storefront/photos.py already uses for its own thumbnail
    cache, applied to R2 instead of a local disk cache.
    """
    path = Path(local_path)
    if not path.exists():
        raise R2UploadError(f"local image file does not exist: {path}")

    client = _client(endpoint_url=endpoint_url, access_key_id=access_key_id, secret_access_key=secret_access_key)
    try:
        head = client.head_object(Bucket=bucket_name, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey"):
            raise R2UploadError(f"failed to check for an existing upload of {key}: {exc}") from exc
        head = None
    except BotoCoreError as exc:
        raise R2UploadError(f"failed to check for an existing upload of {key}: {exc}") from exc

    remote_mtime = head["LastModified"].timestamp() if head else None
    if remote_mtime is None or path.stat().st_mtime > remote_mtime:
        content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        try:
            client.upload_file(str(path), bucket_name, key, ExtraArgs={"ContentType": content_type})
        except (BotoCoreError, ClientError) as exc:
            raise R2UploadError(f"failed to upload {path} to R2 as {key}: {exc}") from exc

    return f"{public_base_url.rstrip('/')}/{key}"
