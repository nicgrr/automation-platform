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

# Lens returns matches already ordered by its own relevance (`position`),
# and a figure photo routinely draws 50+. Past the first couple of dozen
# they're reposts and lookalikes, and every one carries an inline base64
# thumbnail of several KB -- so the tail costs real page weight and cache
# size while adding nothing a human picking a match would read.
MAX_MATCHES = 30


class ReverseImageSearchError(Exception):
    pass


def _guess_mime(path: Path) -> str:
    return {
        ".png": "image/png", ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")


def _safe_link(value) -> str | None:
    """Only http(s) links survive. Every field here is scraped from
    third-party pages, so it's untrusted text that ends up in an href --
    a `javascript:` URL would otherwise be rendered as a clickable link."""
    if not isinstance(value, str):
        return None
    link = value.strip()
    return link if link.lower().startswith(("http://", "https://")) else None


def _normalise(match: dict) -> dict | None:
    link = _safe_link(match.get("link") or match.get("url"))
    if link is None:
        return None
    thumbnail = match.get("thumbnail") or match.get("thumbnailUrl")
    if not isinstance(thumbnail, str) or not thumbnail.startswith(("http://", "https://", "data:image/")):
        thumbnail = None
    return {
        "title": match.get("title") if isinstance(match.get("title"), str) else None,
        "link": link,
        "thumbnail": thumbnail,
        "source": match.get("source") if isinstance(match.get("source"), str) else None,
        "price": match.get("price") if isinstance(match.get("price"), str) else None,
    }


def _flatten_matches(dataset_items: list) -> list[dict]:
    """Pull the individual matches out of the actor's dataset items.

    The actor writes **one dataset item per image searched**, not one per
    match, and the matches sit a level down under the key named by that
    item's own `searchType`:

        {"searchType": "visual-match",
         "visual-match": {"inputUrl": "base64:0", "results": [{...}, ...]}}

    Reading a dataset item as if it were a match is what made this
    feature look broken: the run succeeded, one item came back, and every
    title/link/thumbnail read off it was None -- so the review page said
    Lens "found nothing usable" while the run had in fact matched the
    figure correctly. A flat match is still accepted so a different actor
    version can't break this again.
    """
    matches = []
    for item in dataset_items:
        if not isinstance(item, dict):
            continue
        nested = item.get(item.get("searchType")) if isinstance(item.get("searchType"), str) else None
        candidates = [nested] if isinstance(nested, dict) else [
            value for value in item.values() if isinstance(value, dict) and isinstance(value.get("results"), list)
        ]
        if candidates:
            for group in candidates:
                matches.extend(result for result in group.get("results", []) if isinstance(result, dict))
        else:
            matches.append(item)  # already-flat shape
    return [m for m in (_normalise(match) for match in matches) if m is not None][:MAX_MATCHES]


async def search_image(image_path: Path, token: str, actor_id: str, search_type: str = "visual-match") -> list[dict]:
    """Run the Google Lens actor synchronously against one local photo and
    return its individual matches, flattened out of the per-image dataset
    items it actually writes (see _flatten_matches) and normalised to
    title/link/thumbnail/source/price. Raises ReverseImageSearchError on any
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
        payload = response.json()
    except ValueError as exc:
        raise ReverseImageSearchError("Apify returned a response that wasn't valid JSON") from exc
    return _flatten_matches(payload if isinstance(payload, list) else [])
