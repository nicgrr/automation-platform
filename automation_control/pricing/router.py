"""Cost-aware source routing policy; it performs no provider calls itself."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .provider import ProviderCapability


class PricingTier(StrEnum):
    BULK = "bulk"
    STANDARD = "standard"
    HIGH_VALUE = "high_value"


@dataclass(frozen=True, slots=True)
class PricingRoute:
    tier: PricingTier
    capabilities: tuple[ProviderCapability, ...]
    cache_seconds: int
    automatic: bool
    reason: str


def choose_route(
    estimated_value_aud: Decimal | None, *, manually_requested: bool = False,
    bulk_cache_days: int = 30, standard_cache_days: int = 7,
    high_value_cache_hours: int = 24,
) -> PricingRoute:
    value = estimated_value_aud or Decimal("0")
    if value < 0:
        raise ValueError("estimated value cannot be negative")
    if value < Decimal("10") and not manually_requested:
        return PricingRoute(
            PricingTier.BULK,
            (ProviderCapability.BULK_CURRENT,),
            bulk_cache_days * 86_400,
            True,
            "low-value item: use the bulk baseline and preserve marketplace quota",
        )
    if value < Decimal("100") and not manually_requested:
        return PricingRoute(
            PricingTier.STANDARD,
            (ProviderCapability.BULK_CURRENT, ProviderCapability.ACTIVE_LISTINGS),
            standard_cache_days * 86_400,
            True,
            "standard item: bulk baseline plus selective active listings",
        )
    return PricingRoute(
        PricingTier.HIGH_VALUE,
        (
            ProviderCapability.BULK_CURRENT,
            ProviderCapability.ACTIVE_LISTINGS,
            ProviderCapability.SOLD_LISTINGS,
        ),
        high_value_cache_hours * 3_600,
        manually_requested,
        "high-value or manual review: deeper evidence, subject to provider budgets",
    )
