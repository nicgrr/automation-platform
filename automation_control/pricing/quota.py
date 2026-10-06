"""Provider request budgets and the paid-pricing kill switch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PricingProviderUsage


class PricingBudgetExceeded(RuntimeError):
    pass


class PaidPricingDisabled(PricingBudgetExceeded):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def daily_period(now: datetime | None = None) -> tuple[datetime, datetime]:
    point = now or utcnow()
    point = point if point.tzinfo else point.replace(tzinfo=UTC)
    start = point.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    remaining: int | None
    reason: str | None = None


def get_or_create_daily_usage(
    session: Session, *, provider_key: str, quota_limit: int | None,
    now: datetime | None = None,
) -> PricingProviderUsage:
    start, end = daily_period(now)
    row = session.scalar(select(PricingProviderUsage).where(
        PricingProviderUsage.provider_key == provider_key,
        PricingProviderUsage.quota_type == "requests",
        PricingProviderUsage.period_start == start,
    ))
    if row is None:
        row = PricingProviderUsage(
            provider_key=provider_key,
            quota_type="requests",
            period_start=start,
            period_end=end,
            quota_limit=quota_limit,
            reset_at=end,
        )
        session.add(row)
        session.flush()
    elif quota_limit is not None:
        row.quota_limit = quota_limit
    return row


def can_consume(
    usage: PricingProviderUsage, *, requests: int = 1,
    estimated_cost: Decimal = Decimal("0"), allow_paid: bool = False,
) -> QuotaDecision:
    if requests < 0 or estimated_cost < 0:
        raise ValueError("requests and estimated_cost must be non-negative")
    if estimated_cost > 0 and not allow_paid:
        return QuotaDecision(False, _remaining(usage), "paid pricing is disabled")
    if usage.quota_limit is not None and usage.used + requests > usage.quota_limit:
        return QuotaDecision(False, _remaining(usage), "provider request budget exhausted")
    remaining = None if usage.quota_limit is None else usage.quota_limit - usage.used - requests
    return QuotaDecision(True, remaining)


def require_capacity(
    usage: PricingProviderUsage, *, requests: int = 1,
    estimated_cost: Decimal = Decimal("0"), allow_paid: bool = False,
) -> None:
    decision = can_consume(
        usage, requests=requests, estimated_cost=estimated_cost, allow_paid=allow_paid,
    )
    if decision.allowed:
        return
    if decision.reason == "paid pricing is disabled":
        raise PaidPricingDisabled(decision.reason)
    raise PricingBudgetExceeded(decision.reason or "pricing budget exceeded")


def record_request(
    usage: PricingProviderUsage, *, requests: int = 1, results: int = 0,
    estimated_cost: Decimal = Decimal("0"), actual_cost: Decimal = Decimal("0"),
    failed: bool = False,
) -> None:
    if min(requests, results) < 0 or min(estimated_cost, actual_cost) < 0:
        raise ValueError("usage increments must be non-negative")
    usage.used += requests
    usage.results += results
    usage.estimated_cost += estimated_cost
    usage.actual_cost += actual_cost
    usage.failures += int(failed)
    usage.updated_at = utcnow()


def record_cache_lookup(usage: PricingProviderUsage, *, hit: bool) -> None:
    if hit:
        usage.cache_hits += 1
    else:
        usage.cache_misses += 1
    usage.updated_at = utcnow()


def _remaining(usage: PricingProviderUsage) -> int | None:
    return None if usage.quota_limit is None else max(0, usage.quota_limit - usage.used)
