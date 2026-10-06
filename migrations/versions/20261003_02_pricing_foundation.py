"""add multi-source pricing foundation

Revision ID: 20261003_02
Revises: 20261003_01
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261003_02"
down_revision: Union[str, None] = "20261003_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _indexes(table: str, columns: list[str]) -> None:
    for column in columns:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=False)


def upgrade() -> None:
    op.add_column("inventory_items", sa.Column("grade", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_inventory_items_grade"), "inventory_items", ["grade"], unique=False)

    op.create_table(
        "pricing_providers",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("commercial_use_status", sa.String(length=32), nullable=False),
        sa.Column("health_status", sa.Enum("UNKNOWN", "HEALTHY", "DEGRADED", "UNAVAILABLE", name="pricingproviderhealth"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    _indexes("pricing_providers", ["enabled", "health_status"])

    op.create_table(
        "pricing_external_ids",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=64), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("provider_product_id", sa.String(length=128), nullable=False),
        sa.Column("provider_group_id", sa.String(length=128), nullable=True),
        sa.Column("provider_variant_id", sa.String(length=128), nullable=True),
        sa.Column("external_ids", sa.JSON(), nullable=False),
        sa.Column("match_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("match_method", sa.String(length=32), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_item_id", "provider_key", "provider_product_id", "provider_variant_id",
            name="uq_pricing_external_identity",
        ),
    )
    _indexes("pricing_external_ids", [
        "catalog_item_id", "provider_key", "provider_product_id", "provider_group_id", "provider_variant_id",
    ])

    op.create_table(
        "pricing_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=64), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("external_id_id", sa.String(length=36), nullable=True),
        sa.Column("provider_product_id", sa.String(length=128), nullable=True),
        sa.Column("source_item_id", sa.String(length=256), nullable=True),
        sa.Column("source_kind", sa.Enum("BULK_MARKET", "ACTIVE_LISTING", "SOLD_LISTING", "RETAIL_ASK", "BUYLIST", "MANUAL", name="pricingsourcekind"), nullable=False),
        sa.Column("marketplace", sa.String(length=32), nullable=True),
        sa.Column("query", sa.String(length=512), nullable=True),
        sa.Column("product_type", sa.String(length=64), nullable=True),
        sa.Column("listing_type", sa.String(length=64), nullable=True),
        sa.Column("card_name", sa.String(length=256), nullable=True),
        sa.Column("set_name", sa.String(length=256), nullable=True),
        sa.Column("set_code", sa.String(length=64), nullable=True),
        sa.Column("card_number", sa.String(length=64), nullable=True),
        sa.Column("variant", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("condition", sa.String(length=64), nullable=True),
        sa.Column("grading_company", sa.String(length=64), nullable=True),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("original_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("original_currency", sa.String(length=3), nullable=False),
        sa.Column("aud_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("shipping_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("total_price", sa.Numeric(14, 4), nullable=True),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("listed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_url", sa.String(length=1024), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("match_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ignored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ignored_by", sa.String(length=128), nullable=True),
        sa.Column("ignore_reason", sa.String(length=512), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["external_id_id"], ["pricing_external_ids.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("pricing_observations", [
        "catalog_item_id", "provider_key", "external_id_id", "provider_product_id", "source_kind",
        "marketplace", "product_type", "variant", "language", "condition", "grading_company", "grade",
        "sold_at", "match_confidence", "observed_at", "ignored_at",
    ])
    op.create_index("ix_pricing_observation_identity_time", "pricing_observations", ["catalog_item_id", "observed_at"], unique=False)
    op.create_index("ix_pricing_observation_provider_item", "pricing_observations", ["provider_key", "source_item_id"], unique=False)

    op.create_table(
        "pricing_valuations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=64), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("condition", sa.String(length=64), nullable=True),
        sa.Column("grading_company", sa.String(length=64), nullable=True),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("market_value_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("global_market_value_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("australian_market_value_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("sold_median_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("active_median_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("expected_private_sale_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("expected_ebay_sale_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("quick_sale_value_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("store_buylist_value_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("low_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("high_aud", sa.Numeric(14, 4), nullable=True),
        sa.Column("comp_count", sa.Integer(), nullable=False),
        sa.Column("provider_count", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("confidence_label", sa.String(length=16), nullable=False),
        sa.Column("confidence_components", sa.JSON(), nullable=False),
        sa.Column("calculation_version", sa.String(length=32), nullable=False),
        sa.Column("valued_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("pricing_valuations", [
        "catalog_item_id", "identity_key", "variant", "language", "condition", "grading_company", "grade",
        "confidence_label", "valued_at",
    ])
    op.create_index("ix_pricing_valuation_identity_time", "pricing_valuations", ["catalog_item_id", "valued_at"], unique=False)

    op.create_table(
        "pricing_provider_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("quota_type", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=True),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("results", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.Column("cache_misses", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("actual_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "quota_type", "period_start", name="uq_pricing_provider_usage_period"),
    )
    _indexes("pricing_provider_usage", ["provider_key", "period_start", "period_end"])

    op.create_table(
        "pricing_cache_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("negative", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_key", "cache_key", name="uq_pricing_cache_provider_key"),
    )
    _indexes("pricing_cache_entries", ["provider_key", "cache_key", "negative", "expires_at"])

    op.create_table(
        "pricing_refresh_queue",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=64), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("identity", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", name="pricingqueuestatus"), nullable=False),
        sa.Column("requested_manually", sa.Boolean(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("catalog_item_id", "identity_key", name="uq_pricing_refresh_identity"),
    )
    _indexes("pricing_refresh_queue", ["catalog_item_id", "reason", "priority", "status", "available_at"])
    op.create_index("ix_pricing_refresh_ready", "pricing_refresh_queue", ["status", "available_at", "priority"], unique=False)

    op.create_table(
        "pricing_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("job_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", name="pricingsyncstatus"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("products_seen", sa.Integer(), nullable=False),
        sa.Column("observations_written", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("actual_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["provider_key"], ["pricing_providers.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _indexes("pricing_sync_runs", ["provider_key", "job_kind", "status", "started_at", "finished_at"])

    op.create_table(
        "pricing_fx_rates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_currency", "quote_currency", "observed_at", name="uq_pricing_fx_observation"),
    )
    _indexes("pricing_fx_rates", ["base_currency", "quote_currency", "observed_at"])


def downgrade() -> None:
    for table in (
        "pricing_fx_rates", "pricing_sync_runs", "pricing_refresh_queue", "pricing_cache_entries",
        "pricing_provider_usage", "pricing_valuations", "pricing_observations", "pricing_external_ids",
        "pricing_providers",
    ):
        op.drop_table(table)
    op.drop_index(op.f("ix_inventory_items_grade"), table_name="inventory_items")
    op.drop_column("inventory_items", "grade")
