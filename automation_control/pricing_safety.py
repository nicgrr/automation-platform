"""Conservative sale-price safety checks shared by every eBay handoff.

The check is intentionally a *gate*, not an automatic repricer.  Active
listings are noisy (postage, wrong language/printing and optimistic asks),
so a single cheapest listing must never become permission to sell a card
cheaply.  When a recent, meaningful market reference exists, a proposed
price more than 25% below it is held for a human review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CardPrice, InventoryItem, ShopListing
from .scan_ingest import fx


REFERENCE_MAX_AGE = timedelta(days=30)
MIN_MEANINGFUL_REFERENCE_AUD = Decimal("20.00")
REVIEW_FLOOR_RATIO = Decimal("0.75")

# These are market-level observations, rather than one unusually cheap live
# eBay seller.  The latest usable observation from each source is considered.
REFERENCE_SOURCES = {
    "manual": "manual valuation",
    "ebay_active_median": "eBay active median",
    "optcgapi_market": "One Piece market",
    "pokemonpricetracker_market": "Pokemon Price Tracker market",
    "pokemontcg_tcgplayer_market": "TCGplayer market",
    "pokemontcg_cardmarket_trendPrice": "Cardmarket trend",
    "tcgcsv_tcgplayer_market": "TCGplayer market",
    "tcgplayer_market": "TCGplayer market",
    "pricecharting_market": "PriceCharting market",
}


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PriceEvidence:
    source: str
    label: str
    value_aud: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class PricingSafetyAssessment:
    proposed_price: Decimal
    review_floor: Decimal | None
    evidence: tuple[PriceEvidence, ...]

    @property
    def blocked(self) -> bool:
        return self.review_floor is not None and self.proposed_price < self.review_floor

    @property
    def strongest(self) -> PriceEvidence | None:
        return max(self.evidence, key=lambda item: item.value_aud, default=None)

    def message(self) -> str:
        if not self.blocked or self.review_floor is None:
            return ""
        strongest = self.strongest
        source = (
            f"{strongest.label} is AUD ${strongest.value_aud:.2f}"
            if strongest else "a recent market reference is materially higher"
        )
        return (
            f"Pricing safety hold: proposed AUD ${self.proposed_price:.2f}; {source}. "
            f"Review the card identity/printing and set at least AUD ${self.review_floor:.2f}, "
            "or correct the market evidence before publishing."
        )


def assess_listing_price(
    session: Session,
    listing: ShopListing,
    *,
    proposed_price: Decimal | None = None,
    now: datetime | None = None,
) -> PricingSafetyAssessment:
    """Assess one listing without changing it or any price observations.

    A card with no sufficiently recent/valuable evidence remains publishable;
    absence of data is not silently turned into a guessed price.  Variant-
    specific observations are preferred, while generic observations remain
    usable because the eBay importer currently records its aggregate rows
    without a variant value.
    """
    proposed = _money(Decimal(str(proposed_price if proposed_price is not None else listing.price)))
    cutoff = (now or datetime.now(UTC)) - REFERENCE_MAX_AGE
    inventory = session.get(InventoryItem, listing.inventory_item_id) if listing.inventory_item_id else None
    wanted_variant = inventory.variant if inventory else None

    rows = session.scalars(
        select(CardPrice)
        .where(
            CardPrice.catalog_item_id == listing.catalog_item_id,
            CardPrice.source.in_(tuple(REFERENCE_SOURCES)),
        )
        .order_by(CardPrice.fetched_at.desc())
    ).all()

    by_source: dict[str, CardPrice] = {}
    for row in rows:
        fetched = row.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        if fetched < cutoff:
            continue
        if wanted_variant is not None and row.variant not in (None, wanted_variant):
            continue
        previous = by_source.get(row.source)
        # Prefer an exact physical variant over a generic aggregate, provided
        # both are recent. Within the same specificity, query order is newest.
        if previous is None or (
            wanted_variant is not None
            and previous.variant is None
            and row.variant == wanted_variant
        ):
            by_source[row.source] = row

    # A recent manual valuation tied to the physical inventory variant is
    # the operator's explicit resolution of an identity/printing review.
    # Once that exists, generic aggregate rows must not re-introduce a
    # different parallel under the same card number (for example a base
    # Japanese SCR inheriting the two-star chase card's eBay median).
    # Exact-variant automated observations remain eligible.
    manual = by_source.get("manual")
    if wanted_variant is not None and manual is not None and manual.variant == wanted_variant:
        by_source = {
            source: row for source, row in by_source.items()
            if source == "manual" or row.variant == wanted_variant
        }

    evidence: list[PriceEvidence] = []
    for source, row in by_source.items():
        converted = (
            float(row.price)
            if (row.currency or "").upper() == "AUD"
            else fx.to_aud(row.price, row.currency)
        )
        if converted is None:
            continue
        value = _money(Decimal(str(converted)))
        if value < MIN_MEANINGFUL_REFERENCE_AUD:
            continue
        observed_at = row.fetched_at
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        evidence.append(PriceEvidence(source, REFERENCE_SOURCES[source], value, observed_at))

    # The highest credible signal is safest for a hold. It is not imposed as
    # the selling price: only 75% becomes the review floor, leaving room for
    # optimistic active asks and imperfect cross-market comparisons.
    strongest = max((item.value_aud for item in evidence), default=None)
    floor = _money(strongest * REVIEW_FLOOR_RATIO) if strongest is not None else None
    return PricingSafetyAssessment(proposed, floor, tuple(sorted(evidence, key=lambda item: item.value_aud, reverse=True)))
