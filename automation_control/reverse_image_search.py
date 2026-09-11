"""Reverse image search for a captured photo, via a real Google Lens run
on Apify's cloud -- not a local tool, and not the same thing as the
Apify MCP server (that's a separate integration, wired into a Claude
session directly; this is the CRM's own backend calling Apify's REST API).

This app is Tailscale-only (see ARCHITECTURE.md) -- Apify's cloud actor
can never fetch an image back from a URL on this box, so the photo is
read locally and sent as base64 in the request body instead of as a URL.
The actor's own input schema supports exactly this (`imagesBase64`),
which is what makes calling Apify directly workable at all here.
"""

import base64
from pathlib import Path

import httpx

APIFY_API_BASE = "https://api.apify.com/v2"
REQUEST_TIMEOUT_SECONDS = 90.0  # a real Lens search run, not a quick call


class ReverseImageSearchError(Exception):
    pass


def _guess_mime(path: Path) -> str:
    return {
        ".png": "image/png", ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")


async def search_image(image_path: Path, token: str, actor_id: str, search_type: str = "visual-match") -> list[dict]:
    """Run the Google Lens actor synchronously against one local photo and
    return its dataset items. Raises ReverseImageSearchError on any
    non-2xx response or network failure -- the caller shows that as a
    clear error, never a silent empty result."""
    data = image_path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    payload = {
        "searchTypes": [search_type],
        "imagesBase64": [f"data:{_guess_mime(image_path)};base64,{encoded}"],
    }
    url = f"{APIFY_API_BASE}/actors/{actor_id}/run-sync-get-dataset-items"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, params={"token": token}, json=payload)
        except httpx.HTTPError as exc:
            raise ReverseImageSearchError(f"could not reach Apify: {exc}") from exc

    if response.status_code >= 400:
        raise ReverseImageSearchError(f"Apify run failed ({response.status_code}): {response.text[:300]}")

    try:
        items = response.json()
    except ValueError as exc:
        raise ReverseImageSearchError("Apify returned a response that wasn't valid JSON") from exc
    return items if isinstance(items, list) else []
