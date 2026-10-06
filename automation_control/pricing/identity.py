"""Exact product identity used by every pricing provider and cache key."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass


_SPACE = re.compile(r"\s+")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _SPACE.sub(" ", value.strip()).casefold()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class PricingIdentity:
    """The fields that must match before prices may be compared.

    In particular, raw and graded copies, languages, finishes and sealed
    products intentionally produce different keys. Missing fields are not
    silently filled with guesses.
    """

    catalog_item_id: str
    product_type: str = "single"
    variant: str | None = None
    language: str | None = None
    condition: str | None = None
    grading_company: str | None = None
    grade: str | None = None

    def normalized(self) -> dict[str, str | None]:
        values = asdict(self)
        return {key: _clean(value) for key, value in values.items()}

    @property
    def key(self) -> str:
        canonical = json.dumps(
            self.normalized(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, str | None]:
        return self.normalized()
