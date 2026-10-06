"""Daily TCGCSV bulk ingestion into EzBay's local provider catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    PricingProviderCategory,
    PricingProviderGroup,
    PricingProviderProduct,
    PricingProviderQuote,
    PricingSyncRun,
    PricingSyncStatus,
)
from .providers.tcgcsv import (
    CATEGORY_NAMES,
    TcgCsvClient,
    TcgCsvError,
    extended_fields,
    parse_timestamp,
    select_relevant_categories,
)
from .registry import ensure_provider_rows


PROVIDER_KEY = "tcgcsv"


@dataclass(frozen=True, slots=True)
class TcgCsvSyncSummary:
    skipped: bool
    source_updated_at: datetime
    categories: int = 0
    groups: int = 0
    products: int = 0
    quotes_added: int = 0
    requests: int = 0
    failures: int = 0


def utcnow() -> datetime:
    return datetime.now(UTC)


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _latest_completed_source_timestamp(session: Session) -> datetime | None:
    runs = session.scalars(
        select(PricingSyncRun)
        .where(
            PricingSyncRun.provider_key == PROVIDER_KEY,
            PricingSyncRun.job_kind == "daily_bulk",
            PricingSyncRun.status == PricingSyncStatus.SUCCEEDED,
        )
        .order_by(PricingSyncRun.finished_at.desc())
        .limit(10)
    ).all()
    for run in runs:
        parsed = parse_timestamp(str(run.details.get("source_updated_at") or ""))
        if parsed is not None:
            return parsed
    return None


def _upsert_category(session: Session, row: dict, imported_at: datetime) -> PricingProviderCategory:
    external_id = str(row["categoryId"])
    record = session.scalar(select(PricingProviderCategory).where(
        PricingProviderCategory.provider_key == PROVIDER_KEY,
        PricingProviderCategory.provider_category_id == external_id,
    ))
    if record is None:
        record = PricingProviderCategory(provider_key=PROVIDER_KEY, provider_category_id=external_id)
        session.add(record)
    record.name = str(row.get("name") or row.get("displayName") or external_id)
    record.display_name = row.get("displayName")
    record.source_updated_at = parse_timestamp(row.get("modifiedOn"))
    record.imported_at = imported_at
    record.raw = row
    session.flush()
    return record


def _upsert_group(
    session: Session, category: PricingProviderCategory, row: dict, imported_at: datetime,
) -> PricingProviderGroup:
    external_id = str(row["groupId"])
    record = session.scalar(select(PricingProviderGroup).where(
        PricingProviderGroup.provider_key == PROVIDER_KEY,
        PricingProviderGroup.provider_group_id == external_id,
    ))
    if record is None:
        record = PricingProviderGroup(provider_key=PROVIDER_KEY, provider_group_id=external_id)
        session.add(record)
    record.category_id = category.id
    record.name = str(row.get("name") or external_id)
    record.abbreviation = row.get("abbreviation")
    record.published_at = parse_timestamp(row.get("publishedOn"))
    record.source_updated_at = parse_timestamp(row.get("modifiedOn"))
    record.imported_at = imported_at
    record.raw = row
    session.flush()
    return record


def _product_type(fields: dict[str, str]) -> str:
    return "single" if fields.get("Number") or fields.get("Rarity") else "sealed_or_other"


def _sync_group(
    session: Session, products: list[dict], prices: list[dict],
    group: PricingProviderGroup, source_updated_at: datetime, imported_at: datetime,
) -> tuple[int, int]:
    incoming_ids = [str(row["productId"]) for row in products]
    existing_products = {
        item.provider_product_id: item
        for item in session.scalars(select(PricingProviderProduct).where(
            PricingProviderProduct.provider_key == PROVIDER_KEY,
            PricingProviderProduct.provider_product_id.in_(incoming_ids),
        ))
    } if incoming_ids else {}
    product_records: dict[str, PricingProviderProduct] = {}
    for row in products:
        external_id = str(row["productId"])
        record = existing_products.get(external_id)
        if record is None:
            record = PricingProviderProduct(
                provider_key=PROVIDER_KEY,
                provider_product_id=external_id,
                group_id=group.id,
            )
            session.add(record)
        fields = extended_fields(row)
        record.group_id = group.id
        record.name = str(row.get("name") or external_id)
        record.clean_name = row.get("cleanName")
        record.product_type = _product_type(fields)
        record.card_number = fields.get("Number")
        record.rarity = fields.get("Rarity")
        record.printing = fields.get("Printing")
        record.image_url = row.get("imageUrl")
        record.item_url = row.get("url")
        record.source_updated_at = parse_timestamp(row.get("modifiedOn"))
        record.imported_at = imported_at
        record.raw = row
        product_records[external_id] = record
    session.flush()

    existing_quotes = {
        (quote.provider_product_id, quote.subtype)
        for quote in session.scalars(select(PricingProviderQuote).where(
            PricingProviderQuote.provider_key == PROVIDER_KEY,
            PricingProviderQuote.source_updated_at == source_updated_at,
            PricingProviderQuote.product_id.in_([record.id for record in product_records.values()]),
        ))
    } if product_records else set()
    quotes_added = 0
    for row in prices:
        external_id = str(row.get("productId"))
        product = product_records.get(external_id)
        if product is None:
            continue
        subtype = str(row.get("subTypeName") or "Unknown")
        if (external_id, subtype) in existing_quotes:
            continue
        session.add(PricingProviderQuote(
            provider_key=PROVIDER_KEY,
            product_id=product.id,
            provider_product_id=external_id,
            subtype=subtype,
            currency="USD",
            low_price=_decimal(row.get("lowPrice")),
            mid_price=_decimal(row.get("midPrice")),
            high_price=_decimal(row.get("highPrice")),
            market_price=_decimal(row.get("marketPrice")),
            direct_low_price=_decimal(row.get("directLowPrice")),
            source_updated_at=source_updated_at,
            observed_at=imported_at,
            raw=row,
        ))
        quotes_added += 1
    session.flush()
    return len(products), quotes_added


def sync_tcgcsv(
    session: Session, client: TcgCsvClient, *, game_keys: set[str],
    force: bool = False, max_groups: int | None = None,
    commit_batches: bool = False,
) -> TcgCsvSyncSummary:
    """Sync relevant games once for a new TCGCSV build.

    A failed group is recorded and the run becomes PARTIAL; successful groups
    remain useful without pretending the full daily import succeeded.
    ``commit_batches`` is for the production SQLite job: it releases the write
    lock after each group and never holds one during network requests. Tests
    and dry runs leave it false so the caller can roll everything back.
    """
    unsupported = game_keys - set(CATEGORY_NAMES)
    if unsupported:
        raise ValueError(f"unsupported TCGCSV games: {', '.join(sorted(unsupported))}")
    if max_groups is not None and max_groups < 1:
        raise ValueError("max_groups must be positive")
    ensure_provider_rows(session)
    if commit_batches:
        session.commit()
    start_requests = client.request_count
    source_updated_at = client.last_updated()
    previous = _latest_completed_source_timestamp(session)
    if not force and previous is not None and previous >= source_updated_at:
        return TcgCsvSyncSummary(
            skipped=True, source_updated_at=source_updated_at,
            requests=client.request_count - start_requests,
        )

    selected = select_relevant_categories(client.categories(), game_keys)
    run = PricingSyncRun(
        provider_key=PROVIDER_KEY,
        job_kind="daily_bulk",
        status=PricingSyncStatus.RUNNING,
        details={"source_updated_at": source_updated_at.isoformat(), "games": sorted(game_keys)},
    )
    session.add(run)
    session.flush()
    if commit_batches:
        session.commit()
    imported_at = utcnow()
    group_count = product_count = quote_count = failure_count = 0
    errors: list[str] = []
    stop = False
    for category_row in selected:
        # Network first: SQLite must not hold a write lock while waiting for
        # the provider. The category is persisted only after its group list
        # has arrived successfully.
        group_rows = client.groups(str(category_row["categoryId"]))
        category = _upsert_category(session, category_row, imported_at)
        if commit_batches:
            session.commit()
        for group_row in group_rows:
            if max_groups is not None and group_count >= max_groups:
                stop = True
                break
            group_count += 1
            try:
                external_group_id = str(group_row["groupId"])
                products_payload = client.products(category.provider_category_id, external_group_id)
                prices_payload = client.prices(category.provider_category_id, external_group_id)
                group = _upsert_group(session, category, group_row, imported_at)
                products, quotes = _sync_group(
                    session, products_payload, prices_payload, group,
                    source_updated_at, imported_at,
                )
            except TcgCsvError as exc:
                failure_count += 1
                errors.append(f"group {group_row.get('groupId')}: {type(exc).__name__}: {exc}"[:300])
                continue
            product_count += products
            quote_count += quotes
            if commit_batches:
                session.commit()
        if stop:
            break

    run.status = PricingSyncStatus.PARTIAL if failure_count or stop else PricingSyncStatus.SUCCEEDED
    run.finished_at = utcnow()
    run.requests = client.request_count - start_requests
    run.products_seen = product_count
    run.observations_written = quote_count
    run.failures = failure_count
    run.error = "; ".join(errors[:3]) or None
    run.details = {
        **run.details,
        "categories": len(selected),
        "groups": group_count,
        "quotes_added": quote_count,
        "limited": stop,
    }
    session.flush()
    if commit_batches:
        session.commit()
    return TcgCsvSyncSummary(
        skipped=False,
        source_updated_at=source_updated_at,
        categories=len(selected),
        groups=group_count,
        products=product_count,
        quotes_added=quote_count,
        requests=run.requests,
        failures=failure_count,
    )
