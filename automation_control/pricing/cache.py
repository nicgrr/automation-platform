"""Persistent positive and negative response cache for pricing providers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PricingCacheEntry


def utcnow() -> datetime:
    return datetime.now(UTC)


def cache_key(endpoint: str, parameters: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"endpoint": endpoint, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass(frozen=True, slots=True)
class CacheResult:
    payload: dict[str, Any]
    result_count: int
    negative: bool
    fetched_at: datetime


def get_cached(
    session: Session, *, provider_key: str, endpoint: str,
    parameters: dict[str, Any], now: datetime | None = None,
) -> CacheResult | None:
    checked_at = now or utcnow()
    key = cache_key(endpoint, parameters)
    row = session.scalar(select(PricingCacheEntry).where(
        PricingCacheEntry.provider_key == provider_key,
        PricingCacheEntry.cache_key == key,
    ))
    if row is None or _aware(row.expires_at) <= _aware(checked_at):
        return None
    row.hit_count += 1
    row.last_accessed_at = checked_at
    return CacheResult(row.payload, row.result_count, row.negative, _aware(row.fetched_at))


def put_cached(
    session: Session, *, provider_key: str, endpoint: str,
    parameters: dict[str, Any], payload: dict[str, Any], result_count: int,
    ttl: timedelta, now: datetime | None = None,
) -> PricingCacheEntry:
    if ttl.total_seconds() <= 0:
        raise ValueError("cache TTL must be positive")
    if result_count < 0:
        raise ValueError("result_count cannot be negative")
    written_at = now or utcnow()
    key = cache_key(endpoint, parameters)
    row = session.scalar(select(PricingCacheEntry).where(
        PricingCacheEntry.provider_key == provider_key,
        PricingCacheEntry.cache_key == key,
    ))
    if row is None:
        row = PricingCacheEntry(provider_key=provider_key, cache_key=key, endpoint=endpoint)
        session.add(row)
    row.payload = payload
    row.result_count = result_count
    row.negative = result_count == 0
    row.fetched_at = written_at
    row.expires_at = written_at + ttl
    row.last_accessed_at = written_at
    row.hit_count = 0
    session.flush()
    return row
