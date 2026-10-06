"""Polite server-side client for TCGCSV's once-daily bulk catalogue."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Callable

import httpx


BASE_URL = "https://tcgcsv.com"
USER_AGENT = "EzBay/1.0 (multi-source pricing; server-side daily sync)"


class TcgCsvError(RuntimeError):
    pass


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def extended_fields(product: dict) -> dict[str, str]:
    return {
        str(item.get("name")): str(item.get("value"))
        for item in (product.get("extendedData") or [])
        if item.get("name") and item.get("value") not in (None, "")
    }


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


CATEGORY_NAMES: dict[str, tuple[str, ...]] = {
    "pokemon": ("pokemon",),
    "one_piece": ("one piece card game", "one piece"),
    "dragon_ball_super": ("dragon ball super ccg", "dragon ball super"),
    "dragon_ball_fusion_world": ("dragon ball super fusion world", "fusion world"),
    "dragon_ball_z": ("dragon ball z tcg", "dragon ball z"),
}


def select_relevant_categories(categories: list[dict], game_keys: set[str]) -> list[dict]:
    """Resolve current provider IDs from category names, never fixed numbers."""
    selected: dict[str, dict] = {}
    normalized = [
        (category, normalize_name(str(category.get("displayName") or category.get("name") or "")))
        for category in categories
    ]
    # Match most-specific aliases first so Fusion World is not swallowed by
    # the broader Dragon Ball Super category.
    for game_key in game_keys:
        aliases = sorted(CATEGORY_NAMES.get(game_key, ()), key=len, reverse=True)
        exact = [category for category, name in normalized if name in aliases]
        partial = [
            category for category, name in normalized
            if any(alias in name for alias in aliases)
        ]
        candidates = exact or partial
        if candidates:
            category = candidates[0]
            selected[str(category["categoryId"])] = category
    return sorted(selected.values(), key=lambda item: int(item["categoryId"]))


class TcgCsvClient:
    """HTTP client honoring TCGCSV's user-agent and pacing guidance."""

    def __init__(
        self, *, client: httpx.Client | None = None, request_interval: float = 0.1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
        # An injected client may have its own headers; still identify EzBay.
        self.client.headers["User-Agent"] = USER_AGENT
        self.request_interval = max(0.0, request_interval)
        self._sleep = sleep
        self._last_request_at: float | None = None
        self.request_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _pace(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_interval:
                self._sleep(self.request_interval - elapsed)

    def _get(self, path: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            self._pace()
            try:
                response = self.client.get(f"{BASE_URL}{path}")
                self.request_count += 1
                self._last_request_at = time.monotonic()
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    self._sleep(0.5 * (attempt + 1))
        raise TcgCsvError(f"TCGCSV request failed for {path}: {last_error}")

    def _collection(self, path: str) -> list[dict]:
        try:
            payload = self._get(path).json()
        except ValueError as exc:
            raise TcgCsvError(f"TCGCSV returned invalid JSON for {path}") from exc
        if payload.get("success") is False:
            raise TcgCsvError(f"TCGCSV reported an error for {path}: {payload.get('errors') or 'unknown error'}")
        results = payload.get("results")
        if not isinstance(results, list):
            raise TcgCsvError(f"TCGCSV response for {path} has no results list")
        return results

    def last_updated(self) -> datetime:
        raw = self._get("/last-updated.txt").text.strip()
        timestamp = parse_timestamp(raw)
        if timestamp is None:
            raise TcgCsvError(f"TCGCSV returned an invalid last-updated timestamp: {raw[:80]!r}")
        return timestamp

    def categories(self) -> list[dict]:
        return self._collection("/tcgplayer/categories")

    def groups(self, category_id: str | int) -> list[dict]:
        return self._collection(f"/tcgplayer/{category_id}/groups")

    def products(self, category_id: str | int, group_id: str | int) -> list[dict]:
        return self._collection(f"/tcgplayer/{category_id}/{group_id}/products")

    def prices(self, category_id: str | int, group_id: str | int) -> list[dict]:
        return self._collection(f"/tcgplayer/{category_id}/{group_id}/prices")
