from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from automation_control.config import Settings
from automation_control.models import (
    CatalogItem,
    CatalogItemType,
    InventoryItem,
    PricingProvider,
    PricingQueueStatus,
)
from automation_control.pricing.cache import cache_key, get_cached, put_cached
from automation_control.pricing.identity import PricingIdentity
from automation_control.pricing.provider import (
    PricingProviderAdapter,
    ProviderCapability,
    UnsupportedProviderCapability,
)
from automation_control.pricing.quota import (
    PaidPricingDisabled,
    PricingBudgetExceeded,
    can_consume,
    get_or_create_daily_usage,
    record_cache_lookup,
    record_request,
    require_capacity,
)
from automation_control.pricing.queue import claim_next_refresh, enqueue_refresh, finish_refresh
from automation_control.pricing.registry import ProviderRegistry, ensure_provider_rows
from automation_control.pricing.router import PricingTier, choose_route


NOW = datetime(2026, 10, 3, 4, 0, tzinfo=UTC)


def _provider(session, key="test"):
    row = PricingProvider(key=key, display_name="Test provider")
    session.add(row)
    session.flush()
    return row


def test_identity_key_is_normalized_but_keeps_material_differences():
    raw = PricingIdentity("card-1", variant="Holo", language="English")
    equivalent = PricingIdentity(" CARD-1 ", variant=" holo ", language="ENGLISH")
    japanese = PricingIdentity("card-1", variant="holo", language="Japanese")
    graded = PricingIdentity(
        "card-1", variant="holo", language="English", grading_company="PSA", grade="10",
    )
    sealed = PricingIdentity("card-1", product_type="booster box", language="English")

    assert raw.key == equivalent.key
    assert len(raw.key) == 64
    assert len({raw.key, japanese.key, graded.key, sealed.key}) == 4


def test_cache_key_is_order_independent():
    assert cache_key("search", {"q": "goku", "page": 1}) == cache_key(
        "search", {"page": 1, "q": "goku"},
    )


def test_positive_cache_hit_and_expiry(session):
    _provider(session)
    put_cached(
        session,
        provider_key="test",
        endpoint="prices",
        parameters={"id": "abc"},
        payload={"items": [{"price": "12.00"}]},
        result_count=1,
        ttl=timedelta(hours=1),
        now=NOW,
    )

    hit = get_cached(
        session, provider_key="test", endpoint="prices",
        parameters={"id": "abc"}, now=NOW + timedelta(minutes=30),
    )
    assert hit is not None
    assert hit.negative is False
    assert hit.result_count == 1
    assert hit.payload["items"][0]["price"] == "12.00"
    assert get_cached(
        session, provider_key="test", endpoint="prices",
        parameters={"id": "abc"}, now=NOW + timedelta(hours=1),
    ) is None


def test_empty_provider_result_is_negative_cached(session):
    _provider(session)
    row = put_cached(
        session,
        provider_key="test",
        endpoint="sold",
        parameters={"identity": "missing"},
        payload={"items": []},
        result_count=0,
        ttl=timedelta(days=1),
        now=NOW,
    )
    assert row.negative is True
    hit = get_cached(
        session, provider_key="test", endpoint="sold",
        parameters={"identity": "missing"}, now=NOW + timedelta(hours=2),
    )
    assert hit is not None and hit.negative is True


def test_cache_rejects_invalid_counts_and_ttl(session):
    _provider(session)
    with pytest.raises(ValueError, match="positive"):
        put_cached(
            session, provider_key="test", endpoint="x", parameters={}, payload={},
            result_count=0, ttl=timedelta(0), now=NOW,
        )
    with pytest.raises(ValueError, match="negative"):
        put_cached(
            session, provider_key="test", endpoint="x", parameters={}, payload={},
            result_count=-1, ttl=timedelta(hours=1), now=NOW,
        )


def test_daily_quota_enforces_request_limit_and_tracks_usage(session):
    _provider(session, "ebay")
    usage = get_or_create_daily_usage(
        session, provider_key="ebay", quota_limit=2, now=NOW,
    )
    assert can_consume(usage).allowed
    record_request(usage, results=20)
    record_cache_lookup(usage, hit=False)
    record_request(usage, results=5)
    record_cache_lookup(usage, hit=True)

    assert usage.used == 2
    assert usage.results == 25
    assert usage.cache_hits == 1
    assert usage.cache_misses == 1
    assert can_consume(usage).allowed is False
    with pytest.raises(PricingBudgetExceeded, match="exhausted"):
        require_capacity(usage)


def test_paid_provider_call_is_blocked_by_default(session):
    _provider(session, "paid")
    usage = get_or_create_daily_usage(
        session, provider_key="paid", quota_limit=None, now=NOW,
    )
    with pytest.raises(PaidPricingDisabled):
        require_capacity(usage, estimated_cost=Decimal("0.01"), allow_paid=False)
    require_capacity(usage, estimated_cost=Decimal("0.01"), allow_paid=True)


def test_pricing_defaults_are_free_and_budgeted():
    settings = Settings()
    assert settings.allow_paid_pricing is False
    assert settings.pricing_ebay_daily_budget == 300


def test_router_preserves_marketplace_quota_for_low_value_items():
    low = choose_route(Decimal("9.99"))
    standard = choose_route(Decimal("25"))
    high = choose_route(Decimal("150"))
    manual = choose_route(Decimal("2"), manually_requested=True)

    assert low.tier is PricingTier.BULK
    assert low.capabilities == (ProviderCapability.BULK_CURRENT,)
    assert standard.tier is PricingTier.STANDARD
    assert ProviderCapability.ACTIVE_LISTINGS in standard.capabilities
    assert high.tier is PricingTier.HIGH_VALUE
    assert high.automatic is False
    assert ProviderCapability.SOLD_LISTINGS in manual.capabilities
    assert manual.automatic is True


def test_router_rejects_negative_estimate():
    with pytest.raises(ValueError, match="negative"):
        choose_route(Decimal("-0.01"))


def test_base_provider_distinguishes_unsupported_from_no_results():
    class MinimalProvider(PricingProviderAdapter):
        key = "minimal"

    provider = MinimalProvider()
    assert provider.supports(ProviderCapability.SOLD_LISTINGS) is False
    with pytest.raises(UnsupportedProviderCapability, match="sold_listings"):
        provider.search_sold(PricingIdentity("card-1"))


def test_provider_rows_are_seeded_without_enabling_optional_sources(session):
    rows = ensure_provider_rows(session)
    by_key = {row.key: row for row in rows}
    assert by_key["tcgcsv"].enabled is True
    assert ProviderCapability.BULK_CURRENT.value in by_key["tcgcsv"].capabilities
    assert by_key["apify"].enabled is False
    assert by_key["justtcg"].commercial_use_status == "unverified"
    assert len(ensure_provider_rows(session)) == len(rows)


def test_registry_filters_by_capability():
    class Bulk(PricingProviderAdapter):
        key = "bulk"
        capabilities = frozenset({ProviderCapability.BULK_CURRENT})

    class Active(PricingProviderAdapter):
        key = "active"
        capabilities = frozenset({ProviderCapability.ACTIVE_LISTINGS})

    registry = ProviderRegistry()
    registry.register(Bulk())
    registry.register(Active())
    assert [provider.key for provider in registry.supporting(ProviderCapability.BULK_CURRENT)] == ["bulk"]
    assert registry.get("active").key == "active"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(Bulk())


def test_refresh_queue_deduplicates_identity_and_claims_priority(session):
    session.add_all([
        CatalogItem(id="card-low", item_type=CatalogItemType.TCG_CARD, name="Low"),
        CatalogItem(id="card-high", item_type=CatalogItemType.TCG_CARD, name="High"),
    ])
    session.flush()
    low = enqueue_refresh(
        session, identity=PricingIdentity("card-low"), reason="stale", priority=100,
        available_at=NOW,
    )
    high = enqueue_refresh(
        session, identity=PricingIdentity("card-high"), reason="high value", priority=20,
        available_at=NOW,
    )
    duplicate = enqueue_refresh(
        session, identity=PricingIdentity("card-low"), reason="manual", priority=0,
        manually_requested=True, available_at=NOW,
    )
    assert duplicate.id == low.id
    assert duplicate.priority == 0
    assert duplicate.requested_manually is True

    claimed = claim_next_refresh(session, now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.id == low.id
    assert claimed.status is PricingQueueStatus.RUNNING
    assert claimed.attempts == 1
    finish_refresh(claimed, succeeded=False, error="provider timeout")
    assert claimed.status is PricingQueueStatus.FAILED
    assert claimed.last_error == "provider timeout"

    claimed = claim_next_refresh(session, now=NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.id == high.id
    finish_refresh(claimed, succeeded=True)
    assert claimed.status is PricingQueueStatus.SUCCEEDED
    assert claimed.last_error is None


def test_inventory_has_dedicated_grade_separate_from_condition():
    assert "grade" in InventoryItem.__table__.columns
    assert InventoryItem.__table__.columns.grade.type.length == 32
