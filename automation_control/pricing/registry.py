"""Known provider metadata and adapter registration.

This stores no secrets. Environment credentials remain in ``Settings`` and a
provider being known does not imply that it is licensed, configured or safe to
call automatically.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import PricingProvider
from .provider import PricingProviderAdapter, ProviderCapability


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    key: str
    display_name: str
    capabilities: tuple[ProviderCapability, ...]
    default_enabled: bool = False
    commercial_use_status: str = "unverified"


PROVIDER_DEFINITIONS = (
    ProviderDefinition(
        "tcgcsv", "TCGCSV",
        (ProviderCapability.BULK_CURRENT, ProviderCapability.HISTORY, ProviderCapability.PRODUCT_LOOKUP),
        default_enabled=True,
        commercial_use_status="review_required",
    ),
    ProviderDefinition(
        "ebay", "eBay Official",
        (ProviderCapability.ACTIVE_LISTINGS, ProviderCapability.PRODUCT_LOOKUP, ProviderCapability.QUOTA),
    ),
    ProviderDefinition(
        "depop", "Depop", (ProviderCapability.ACTIVE_LISTINGS,),
        commercial_use_status="manual_only",
    ),
    ProviderDefinition(
        "vinted", "Vinted", (ProviderCapability.ACTIVE_LISTINGS,),
        commercial_use_status="manual_only",
    ),
    ProviderDefinition(
        "pokemonpricetracker", "Pokemon Price Tracker",
        (ProviderCapability.BULK_CURRENT, ProviderCapability.SOLD_LISTINGS, ProviderCapability.HISTORY),
    ),
    ProviderDefinition("cardtrader", "CardTrader", (ProviderCapability.ACTIVE_LISTINGS,)),
    ProviderDefinition("justtcg", "JustTCG", (ProviderCapability.BULK_CURRENT, ProviderCapability.HISTORY)),
    ProviderDefinition("apify", "Apify", (ProviderCapability.SOLD_LISTINGS, ProviderCapability.QUOTA)),
    ProviderDefinition("rapidapi", "RapidAPI", (ProviderCapability.SOLD_LISTINGS, ProviderCapability.QUOTA)),
    ProviderDefinition("serpapi", "SerpApi", (ProviderCapability.ACTIVE_LISTINGS, ProviderCapability.QUOTA)),
    ProviderDefinition("marketplace_insights", "eBay Marketplace Insights", (ProviderCapability.SOLD_LISTINGS,)),
)


def ensure_provider_rows(session: Session) -> list[PricingProvider]:
    """Idempotently create metadata rows without changing operator choices."""
    rows: list[PricingProvider] = []
    for definition in PROVIDER_DEFINITIONS:
        row = session.get(PricingProvider, definition.key)
        if row is None:
            row = PricingProvider(
                key=definition.key,
                display_name=definition.display_name,
                enabled=definition.default_enabled,
                capabilities=[capability.value for capability in definition.capabilities],
                commercial_use_status=definition.commercial_use_status,
            )
            session.add(row)
        rows.append(row)
    session.flush()
    return rows


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PricingProviderAdapter] = {}

    def register(self, adapter: PricingProviderAdapter) -> None:
        if adapter.key in self._adapters:
            raise ValueError(f"pricing provider {adapter.key!r} is already registered")
        self._adapters[adapter.key] = adapter

    def get(self, key: str) -> PricingProviderAdapter:
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise KeyError(f"pricing provider {key!r} is not registered") from exc

    def supporting(self, capability: ProviderCapability) -> list[PricingProviderAdapter]:
        return [adapter for adapter in self._adapters.values() if adapter.supports(capability)]
