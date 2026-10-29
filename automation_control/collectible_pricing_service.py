"""Category-safe pricing orchestration for non-TCG collectibles.

The existing TCG importers deliberately do not call this module.  Every public
entry point also verifies ``CatalogItem.item_type`` before resolving providers,
so a future caller cannot accidentally send a card to Depop or Vinted.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote_plus, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.ebay import EbaySandboxReadAdapter, normalize_market
from .ebay_oauth import configured, ebay_services
from .market_watch_service import exchange_rate_to_aud
from .models import (
    CatalogItem, CatalogItemType, PricingObservation, PricingProviderRule,
    PricingSourceKind, PricingValuation,
)
from .pricing.identity import PricingIdentity


CENT = Decimal("0.01")
SUPPORTED_MANUAL_PROVIDERS = frozenset({"ebay", "depop", "vinted"})
PROVIDER_DOMAINS = {
    "ebay": ("ebay.com.au", "ebay.com", "ebay.co.uk"),
    "depop": ("depop.com",),
    "vinted": ("vinted.com", "vinted.co.uk", "vinted.fr", "vinted.de", "vinted.it"),
}


class CollectiblePricingError(RuntimeError):
    pass


class CategoryRoutingError(CollectiblePricingError):
    pass


@dataclass(frozen=True, slots=True)
class CollectibleValuation:
    lowest_ask: Decimal | None
    median_ask: Decimal | None
    sold_low: Decimal | None
    sold_high: Decimal | None
    realistic_resale: Decimal | None
    recommended_listing: Decimal | None
    quick_sale: Decimal | None
    confidence_score: Decimal
    confidence_label: str
    active_count: int
    sold_count: int
    provider_count: int
    last_refresh: datetime | None


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def require_collectible(session: Session, catalog_item_id: str) -> CatalogItem:
    item = session.get(CatalogItem, catalog_item_id)
    if item is None:
        raise CollectiblePricingError("catalogue item not found")
    if item.item_type != CatalogItemType.COLLECTIBLE:
        raise CategoryRoutingError(
            "generic marketplace pricing is restricted to non-TCG collectibles"
        )
    return item


def provider_rules(session: Session, item: CatalogItem) -> list[PricingProviderRule]:
    if item.item_type != CatalogItemType.COLLECTIBLE:
        return []
    return list(session.scalars(
        select(PricingProviderRule)
        .where(
            PricingProviderRule.catalog_item_type == item.item_type,
            PricingProviderRule.enabled.is_(True),
        )
        .order_by(PricingProviderRule.priority)
    ))


def provider_rule(session: Session, item: CatalogItem, provider_key: str) -> PricingProviderRule:
    rule = session.scalar(select(PricingProviderRule).where(
        PricingProviderRule.catalog_item_type == item.item_type,
        PricingProviderRule.provider_key == provider_key,
        PricingProviderRule.enabled.is_(True),
    ))
    if rule is None:
        raise CategoryRoutingError(f"{provider_key} is not enabled for {item.item_type.value}")
    return rule


def search_link(provider_key: str, query: str) -> str:
    encoded = quote_plus(query.strip())
    links = {
        "ebay": f"https://www.ebay.com.au/sch/i.html?_nkw={encoded}",
        "depop": f"https://www.depop.com/search/?q={encoded}",
        "vinted": f"https://www.vinted.com/catalog?search_text={encoded}",
    }
    try:
        return links[provider_key]
    except KeyError as exc:
        raise CollectiblePricingError("unsupported marketplace") from exc


def validate_listing_url(provider_key: str, url: str) -> str:
    parsed = urlparse(url.strip())
    domains = PROVIDER_DOMAINS.get(provider_key, ())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not any(
        host == domain or host.endswith("." + domain) for domain in domains
    ):
        raise CollectiblePricingError(f"enter a valid {provider_key.title()} listing URL")
    return url.strip()


def add_manual_comparable(
    session: Session, *, catalog_item_id: str, provider_key: str, url: str,
    title: str, price: str, currency: str, status: str, observed_at: datetime | None = None,
    aud_price: Decimal | None = None,
) -> PricingObservation:
    item = require_collectible(session, catalog_item_id)
    rule = provider_rule(session, item, provider_key)
    if rule.access_mode not in {"manual", "automatic"} or provider_key not in SUPPORTED_MANUAL_PROVIDERS:
        raise CategoryRoutingError(f"manual comparisons are not enabled for {provider_key}")
    clean_url = validate_listing_url(provider_key, url)
    try:
        amount = Decimal(price.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise CollectiblePricingError("price must be a valid amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise CollectiblePricingError("price must be greater than zero")
    currency = currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise CollectiblePricingError("currency must be a three-letter code")
    if currency != "AUD" and aud_price is None:
        raise CollectiblePricingError("an AUD conversion is required for non-AUD observations")
    kinds = {
        "active": (PricingSourceKind.ACTIVE_LISTING, "active_asking"),
        "sold_verified": (PricingSourceKind.SOLD_LISTING, "verified_sold"),
        "accepted_unknown": (PricingSourceKind.MANUAL, "accepted_offer_unknown"),
    }
    if status not in kinds:
        raise CollectiblePricingError("unknown comparable status")
    source_kind, listing_type = kinds[status]
    if source_kind == PricingSourceKind.SOLD_LISTING and not rule.allow_sold:
        # Manual evidence may be verified by the operator even where an API
        # cannot provide sold data. Keep that distinct from automated access.
        listing_type = "verified_sold_manual"
    observed = observed_at or datetime.now(UTC)
    row = PricingObservation(
        id=str(uuid.uuid4()), catalog_item_id=item.id, provider_key=provider_key,
        source_kind=source_kind, marketplace=provider_key, listing_type=listing_type,
        title=title.strip()[:512] or item.name, original_price=amount,
        original_currency=currency, aud_price=amount if currency == "AUD" else _money(aud_price),
        total_price=amount if currency == "AUD" else _money(aud_price), item_url=clean_url,
        match_confidence=Decimal("1"), observed_at=observed,
        raw={"entry_method": "manual", "final_price_known": status != "accepted_unknown"},
    )
    session.add(row)
    session.flush()
    return row


def observations(session: Session, catalog_item_id: str) -> list[PricingObservation]:
    require_collectible(session, catalog_item_id)
    return list(session.scalars(
        select(PricingObservation)
        .where(
            PricingObservation.catalog_item_id == catalog_item_id,
            PricingObservation.ignored_at.is_(None),
        )
        .order_by(PricingObservation.observed_at.desc())
    ))


def calculate_valuation(rows: list[PricingObservation], *, now: datetime | None = None) -> CollectibleValuation:
    point = now or datetime.now(UTC)
    fresh_after = point - timedelta(days=30)
    usable = [
        row for row in rows
        if row.aud_price is not None
        and (row.observed_at if row.observed_at.tzinfo else row.observed_at.replace(tzinfo=UTC)) >= fresh_after
    ]
    asks = sorted(row.aud_price for row in usable if row.source_kind == PricingSourceKind.ACTIVE_LISTING)
    sold = sorted(row.aud_price for row in usable if row.source_kind == PricingSourceKind.SOLD_LISTING)
    ask_median = _money(Decimal(str(statistics.median(asks)))) if asks else None
    sold_median = _money(Decimal(str(statistics.median(sold)))) if sold else None
    realistic = sold_median or (_money(ask_median * Decimal("0.85")) if ask_median else None)
    recommended = _money(realistic * Decimal("1.10")) if realistic else None
    quick = _money(realistic * Decimal("0.80")) if realistic else None
    providers = {row.provider_key for row in usable}
    score = min(Decimal("100"), Decimal(len(usable) * 8 + len(providers) * 10 + len(sold) * 12))
    label = "high" if score >= 70 else "medium" if score >= 40 else "low"
    last_refresh = max((row.observed_at for row in rows), default=None)
    return CollectibleValuation(
        lowest_ask=asks[0] if asks else None, median_ask=ask_median,
        sold_low=sold[0] if sold else None, sold_high=sold[-1] if sold else None,
        realistic_resale=realistic, recommended_listing=recommended, quick_sale=quick,
        confidence_score=score, confidence_label=label,
        active_count=len(asks), sold_count=len(sold), provider_count=len(providers),
        last_refresh=last_refresh,
    )


def save_valuation(session: Session, item: CatalogItem, value: CollectibleValuation) -> PricingValuation:
    identity = PricingIdentity(catalog_item_id=item.id, product_type="collectible")
    row = PricingValuation(
        catalog_item_id=item.id, identity_key=identity.key,
        market_value_aud=value.realistic_resale, sold_median_aud=(
            _money((value.sold_low + value.sold_high) / 2) if value.sold_low is not None else None
        ),
        active_median_aud=value.median_ask,
        expected_private_sale_aud=value.realistic_resale,
        expected_ebay_sale_aud=value.recommended_listing,
        quick_sale_value_aud=value.quick_sale,
        low_aud=value.sold_low or value.lowest_ask,
        high_aud=value.sold_high or value.median_ask,
        comp_count=value.active_count + value.sold_count,
        provider_count=value.provider_count,
        confidence_score=value.confidence_score,
        confidence_label=value.confidence_label,
        confidence_components={"active": value.active_count, "sold": value.sold_count},
        calculation_version="collectibles-v1",
    )
    session.add(row)
    session.flush()
    return row


async def refresh_ebay(
    session: Session, settings, catalog_item_id: str, *, limit: int = 30, force: bool = False,
) -> int:
    item = require_collectible(session, catalog_item_id)
    rule = provider_rule(session, item, "ebay")
    if rule.access_mode != "automatic" or not rule.allow_active:
        raise CategoryRoutingError("automatic eBay active-listing research is disabled")
    latest = session.scalar(
        select(PricingObservation.observed_at)
        .where(
            PricingObservation.catalog_item_id == item.id,
            PricingObservation.provider_key == "ebay",
            PricingObservation.source_kind == PricingSourceKind.ACTIVE_LISTING,
        )
        .order_by(PricingObservation.observed_at.desc()).limit(1)
    )
    if latest is not None and not force:
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        if latest >= datetime.now(UTC) - timedelta(seconds=max(60, rule.refresh_seconds)):
            return 0
    if not configured(settings):
        raise CollectiblePricingError("eBay application credentials are not configured")
    adapter = None
    try:
        _, oauth = ebay_services(settings)
        token = (await oauth.application_token())["access_token"]
        adapter = EbaySandboxReadAdapter(token, environment=settings.ebay_env)
        payload = await adapter.search_market(item.name, limit=limit, marketplace=settings.ebay_marketplace_id)
        count = 0
        rates: dict[str, Decimal] = {"AUD": Decimal("1")}
        for result in normalize_market(payload):
            if not result.get("price") or not result.get("ebay_item_id"):
                continue
            currency = (result.get("currency") or "AUD").upper()
            if currency not in rates:
                rates[currency] = await exchange_rate_to_aud(currency)
            original = Decimal(str(result["price"]))
            shipping = Decimal(str(result.get("shipping") or 0))
            aud = _money((original + shipping) * rates[currency])
            session.add(PricingObservation(
                catalog_item_id=item.id, provider_key="ebay",
                source_item_id=result["ebay_item_id"], source_kind=PricingSourceKind.ACTIVE_LISTING,
                marketplace="EBAY_AU", listing_type="active_asking", title=result.get("title"),
                original_price=original, original_currency=currency, aud_price=aud,
                shipping_price=shipping, total_price=aud, item_url=result.get("url"),
                image_url=result.get("image_url"), condition=result.get("condition"),
                observed_at=result.get("retrieved_at") or datetime.now(UTC),
                match_confidence=Decimal("0.75"), raw=result.get("raw") or {},
            ))
            count += 1
    except Exception as exc:
        raise CollectiblePricingError("eBay pricing is temporarily unavailable") from exc
    finally:
        if adapter is not None:
            await adapter.client.aclose()
    session.flush()
    return count
