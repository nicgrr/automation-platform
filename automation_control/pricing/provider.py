"""Common contract for bulk, marketplace and specialist price providers."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .identity import PricingIdentity


class ProviderCapability(StrEnum):
    BULK_CURRENT = "bulk_current"
    ACTIVE_LISTINGS = "active_listings"
    SOLD_LISTINGS = "sold_listings"
    RETAIL_ASK = "retail_ask"
    BUYLIST = "buylist"
    HISTORY = "history"
    PRODUCT_LOOKUP = "product_lookup"
    QUOTA = "quota"
    HEALTH = "health"


class PricingProviderError(RuntimeError):
    """A provider operation failed without exposing credentials or payloads."""


class UnsupportedProviderCapability(PricingProviderError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderQuota:
    limit: int | None
    used: int
    reset_at: datetime | None = None
    estimated_cost_per_request: Decimal = Decimal("0")
    currency: str = "AUD"

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)


@dataclass(frozen=True, slots=True)
class ProviderHealthReport:
    healthy: bool
    checked_at: datetime
    message: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedPricingObservation:
    identity: PricingIdentity
    provider_key: str
    source_kind: str
    original_price: Decimal
    original_currency: str
    observed_at: datetime
    aud_price: Decimal | None = None
    shipping_price: Decimal | None = None
    source_item_id: str | None = None
    title: str | None = None
    item_url: str | None = None
    sold_at: datetime | None = None
    listed_at: datetime | None = None
    marketplace: str | None = None
    match_confidence: Decimal = Decimal("1")
    raw: dict[str, Any] = field(default_factory=dict)


class PricingProviderAdapter(ABC):
    """Small synchronous interface wrapped around source-specific adapters.

    Implementations advertise capabilities. Unsupported operations fail
    explicitly rather than quietly returning an empty list, keeping "no
    matches" distinct from "this provider cannot answer that question".
    """

    key: str
    capabilities: frozenset[ProviderCapability] = frozenset()

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def _unsupported(self, capability: ProviderCapability):
        raise UnsupportedProviderCapability(f"{self.key} does not support {capability.value}")

    def get_product(self, identity: PricingIdentity) -> dict[str, Any] | None:
        return self._unsupported(ProviderCapability.PRODUCT_LOOKUP)

    def get_prices(self, identity: PricingIdentity) -> list[NormalizedPricingObservation]:
        return self._unsupported(ProviderCapability.BULK_CURRENT)

    def search_active(self, identity: PricingIdentity) -> list[NormalizedPricingObservation]:
        return self._unsupported(ProviderCapability.ACTIVE_LISTINGS)

    def search_sold(self, identity: PricingIdentity) -> list[NormalizedPricingObservation]:
        return self._unsupported(ProviderCapability.SOLD_LISTINGS)

    def get_historical_prices(self, identity: PricingIdentity) -> list[NormalizedPricingObservation]:
        return self._unsupported(ProviderCapability.HISTORY)

    def get_quota(self) -> ProviderQuota:
        return ProviderQuota(limit=None, used=0)

    def health_check(self) -> ProviderHealthReport:
        return self._unsupported(ProviderCapability.HEALTH)
