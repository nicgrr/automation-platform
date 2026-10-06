"""Multi-source pricing primitives.

This package runs alongside the legacy ``card_prices`` readers during the
migration window. Provider responses are normalized and cached here before a
later phase promotes derived valuations into the user-facing pipeline.
"""

from .identity import PricingIdentity
from .provider import ProviderCapability, PricingProviderAdapter

__all__ = ["PricingIdentity", "PricingProviderAdapter", "ProviderCapability"]
